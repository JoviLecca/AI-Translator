"""应用服务层（设计 §4）：编排领域对象，UI 只调这里。

- ImportService：导入→解析→分段→落库（含同名替换的增量重导）；
- TranslationService：预检（cfg_hash 比对+成本预估）→ Run 启动/取消；
- ReviewService：校对流转 + 搜索替换（预览/应用/撤销）；
- ExportService：按原格式回写 + 双语模式 + 源文件变更防护。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import threading
from pathlib import Path

from adapters import detect_format, get_adapter
from adapters.base import FormatError
from core import costs
from core.engine import RunEngine
from core.styles import style_prompt
from core.appconfig import get_provider
from storage import secrets


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_text(text: str) -> str:
    return _sha(text.encode("utf-8"))


def _tags_of(row) -> dict:
    try:
        return json.loads(row["tags"] or "{}")
    except Exception:
        return {}


class ImportService:
    def __init__(self, project):
        self.project = project

    def import_file(self, src: Path) -> dict:
        project = self.project
        fmt = detect_format(src)
        dest_dir = project.source_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        content_hash = _sha(dest.read_bytes())
        model = get_adapter(fmt).parse(dest, opts={"ruby_loose": project.ruby_loose})
        blocks = [{
            "seq": b.seq, "text": b.text, "is_heading": b.is_heading,
            "translatable": b.translatable,
            "src_hash": _sha_text(b.text.strip()),
            "ruby": b.meta.get("ruby") or None,
        } for b in model.blocks]
        rel = f"source/{dest.name}"
        doc_id = project.db.upsert_document(rel, fmt, content_hash, {})
        stats = project.db.replace_segments(doc_id, blocks, project.cfg_hash())
        project.db.set_doc_fields(doc_id, status="segmented")
        return {"doc_id": doc_id, "path": rel, "format": fmt, "stats": stats}

    def import_files(self, paths) -> tuple[list[dict], list[dict]]:
        ok, errs = [], []
        for p in paths:
            try:
                ok.append(self.import_file(Path(p)))
            except (FormatError, Exception) as e:  # noqa: BLE001 上层需逐文件汇报
                errs.append({"file": str(p), "error": str(e)})
        return ok, errs

    def remove_file(self, doc_id: int) -> None:
        self.project.db.delete_document(doc_id)

    def set_tags(self, doc_id: int, tags: dict) -> None:
        self.project.db.set_doc_fields(doc_id, tags=tags)


class TranslationService:
    def __init__(self, project, cfg: dict):
        self.project = project
        self.cfg = cfg

    # ---------- Provider ----------
    def build_provider(self):
        pid = self.project.provider_id
        pcfg = get_provider(self.cfg, pid)
        key = secrets.get_api_key(pid)
        ptype = pcfg.get("type", "openai")
        if ptype == "openai":
            from llm.openai_compat import OpenAICompatProvider
            return OpenAICompatProvider(pcfg, key)
        if ptype == "anthropic":
            from llm.anthropic_provider import AnthropicProvider
            return AnthropicProvider(pcfg, key)
        if ptype == "gemini":
            from llm.gemini_provider import GeminiProvider
            return GeminiProvider(pcfg, key)
        raise FormatError(f"未知 Provider 类型：{ptype}")

    def provider_prices(self) -> tuple[float, float]:
        try:
            pcfg = get_provider(self.cfg, self.project.provider_id)
            return float(pcfg.get("price_in", 0) or 0), float(pcfg.get("price_out", 0) or 0)
        except KeyError:
            return 0.0, 0.0

    # ---------- 预检（设计 v0.7 #42 / 增补设计 §2.6） ----------
    def precheck(self) -> dict:
        db = self.project.db
        rows = db.list_segments(translatable=True, status_in=("pending", "failed"))
        chars = sum(len(r["src_text"]) for r in rows)
        cfg_now = self.project.cfg_hash()
        stale = 0
        for r in db.list_segments(translatable=True,
                                  status_in=("machine_translated", "human_edited", "confirmed")):
            if r["cfg_hash"] != cfg_now:
                stale += 1
        price_in, price_out = self.provider_prices()
        slide = self.slide()
        return {
            "pending": len(rows), "chars": chars, "stale_translations": stale,
            "cfg_hash": cfg_now,
            "slide": slide,
            "ruby_policy": self.project.ruby_policy,
            "estimate": costs.estimate_run(chars, slide=slide) if rows else None,
            "glossary_terms": len(self.project.glossary.entries),
            "terms_extracted_docs": [r["id"] for r in db.list_documents()
                                     if not r["terms_extracted"]],
        }

    def slide(self) -> int:
        """滑窗段数：项目级 context_slide，缺省继承全局 context_segments。"""
        v = self.project.context_slide
        if v is None:
            v = self.cfg.get("context_segments", 2)
        return max(0, min(5, int(v)))

    def mark_stale_retranslate(self) -> int:
        """配置变更后，把过期且未人工处理的机器译稿重置为 pending（保护已确认内容 #41）。"""
        db = self.project.db
        cfg_now = self.project.cfg_hash()
        n = 0
        for r in db.list_segments(translatable=True, status_in=("machine_translated",)):
            if r["cfg_hash"] != cfg_now:
                db.update_segment(r["id"], status="pending")
                n += 1
        return n

    # ---------- 启动 ----------
    def start(self, on_progress=None, on_done=None, provider=None) -> "RunHandle":
        project, db = self.project, self.project.db
        rows = db.list_segments(translatable=True, status_in=("pending", "failed"))
        if not rows:
            raise RuntimeError("没有待翻译的段落")
        run_id = db.create_run("translate", len(rows))
        db.create_run_items(run_id, [r["id"] for r in rows])
        doc_tags = {d["id"]: _tags_of(d) for d in db.list_documents()}
        terms = [(t["src_term"], t["tgt_candidates"].split("|"), t["note"] or "", t["occurrences"])
                 for t in _approved_terms(db, project)]
        engine = RunEngine(
            provider or self.build_provider(), db,
            cfg_hash=project.cfg_hash(),
            src_lang=project.src_lang, tgt_lang=project.tgt_lang,
            style_desc=style_prompt(project.style_preset, project.custom_style_prompt),
            terms=terms, doc_tags=doc_tags,
            concurrency=int(self.cfg.get("concurrency", 4)),
            context_n=self.slide(),
            ruby_policy=project.ruby_policy,
            cancel_event=threading.Event(), progress=on_progress,
        )
        handle = RunHandle(run_id, engine)
        thread = threading.Thread(target=self._worker, args=(engine, run_id, rows, on_done, handle),
                                  daemon=True, name=f"run-{run_id}")
        handle.thread = thread
        thread.start()
        return handle

    def _worker(self, engine, run_id, rows, on_done, handle) -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(engine.run_translation(run_id, rows))
        except Exception as e:  # noqa: BLE001
            result = {"error": str(e), "crashed": True}
            try:
                self.project.db.update_run(run_id, status="failed", error=str(e))
            except Exception:
                pass
        finally:
            loop.close()
        handle.result = result
        if on_done:
            try:
                on_done(result)
            except Exception:
                pass


def _approved_terms(db, project):
    """术语来源：glossary 文件为权威；occurrences 取 DB 缓存（无则 1）。"""
    out = []
    cached = {t["src_term"]: t for t in db.list_terms()}
    for term in project.glossary.entries:
        row = cached.get(term.src)
        out.append({
            "src_term": term.src, "tgt_candidates": "|".join(term.candidates),
            "note": term.note, "occurrences": row["occurrences"] if row else 1,
        })
    return out


class RunHandle:
    def __init__(self, run_id: int, engine: RunEngine):
        self.run_id = run_id
        self.engine = engine
        self.thread: threading.Thread | None = None
        self.result: dict | None = None

    def cancel(self) -> None:
        self.engine.cancel_event.set()

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def join(self, timeout: float | None = None) -> None:
        if self.thread:
            self.thread.join(timeout)


class ReviewService:
    def __init__(self, project):
        self.project = project
        self._undo_stack: list[tuple[str, list[tuple[int, str, str]]]] = []

    def segments(self, doc_id: int | None = None, status_filter: str | None = None,
                 search: str | None = None, review_flag: bool | None = None) -> list[dict]:
        status_in = (status_filter,) if status_filter and status_filter != "all" else None
        rows = self.project.db.list_segments(doc_id=doc_id, translatable=True,
                                             status_in=status_in, search=search,
                                             review_flag=review_flag)
        docs = {d["id"]: d["path"] for d in self.project.db.list_documents()}
        out = []
        for r in rows:
            try:
                ruby_map = json.loads(r["ruby_map"]) if r["ruby_map"] else {}
            except Exception:
                ruby_map = {}
            try:
                ruby_src = json.loads(r["ruby_src"]) if r["ruby_src"] else []
            except Exception:
                ruby_src = []
            d = {"id": r["id"], "doc_id": r["doc_id"], "seq": r["seq"],
                 "src": r["src_text"], "tgt": r["tgt_text"] or "",
                 "status": r["status"], "review_flag": bool(r["review_flag"]),
                 "is_heading": bool(r["is_heading"]),
                 "doc_path": docs.get(r["doc_id"], ""),
                 "ruby_map": ruby_map, "ruby_src": ruby_src}
            out.append(d)
        return out

    def set_ruby_map(self, seg_id: int, ruby_map: dict) -> None:
        """校对页注音内联编辑（增补设计 R3）。"""
        self.project.db.update_segment(seg_id,
                                       ruby_map=json.dumps(ruby_map, ensure_ascii=False)
                                       if ruby_map else "{}")

    def edit(self, seg_id: int, tgt: str) -> None:
        self.project.db.update_segment(seg_id, tgt=tgt, status="human_edited")

    def confirm(self, seg_id: int) -> None:
        r = self.project.db.get_segment(seg_id)
        if r and r["status"] in ("machine_translated", "human_edited"):
            self.project.db.update_segment(seg_id, status="confirmed")

    def confirm_document(self, doc_id: int) -> int:
        n = 0
        for r in self.project.db.list_segments(
                doc_id=doc_id, translatable=True,
                status_in=("machine_translated", "human_edited")):
            self.project.db.update_segment(r["id"], status="confirmed")
            n += 1
        return n

    def confirm_all(self) -> int:
        n = 0
        for d in self.project.db.list_documents():
            n += self.confirm_document(d["id"])
        return n

    def mark_retranslate(self, seg_ids: list[int]) -> None:
        for sid in seg_ids:
            self.project.db.update_segment(sid, status="pending")

    # ---------- 搜索替换（预览 + 可撤销，设计 v0.6 #39） ----------
    def search_replace(self, find: str, replace: str, *, doc_id: int | None = None,
                       case_sensitive: bool = False, regex: bool = False) -> "ReplacePreview":
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(find if regex else re.escape(find), flags)
        matches = []
        for s in self.segments(doc_id=doc_id):
            if not s["tgt"]:
                continue
            new, n = pattern.subn(replace, s["tgt"])
            if n:
                matches.append((s["id"], s["doc_path"], s["tgt"], new))
        return ReplacePreview(self, find, matches)

    def push_undo(self, label: str, changes: list[tuple[int, str, str]]) -> None:
        self._undo_stack.append((label, changes))

    def undo_last(self) -> str | None:
        if not self._undo_stack:
            return None
        label, changes = self._undo_stack.pop()
        for seg_id, tgt, status in changes:
            self.project.db.update_segment(seg_id, tgt=tgt, status=status)
        return label


class ReplacePreview:
    def __init__(self, service: ReviewService, find: str,
                 matches: list[tuple[int, str, str, str]]):
        self.service = service
        self.find = find
        self.matches = matches
        self.applied = False

    def apply(self) -> int:
        changes = []
        for seg_id, _doc, before, after in self.matches:
            row = self.service.project.db.get_segment(seg_id)
            changes.append((seg_id, before, row["status"]))
            self.service.project.db.update_segment(seg_id, tgt=after, status="human_edited")
        if changes:
            self.service.push_undo(f"替换「{self.find}」×{len(changes)}", changes)
        self.applied = True
        return len(changes)


class ExportService:
    def __init__(self, project):
        self.project = project

    def export(self, doc_ids: list[int], mode: str = "target",
               force: bool = False) -> list[dict]:
        project = self.project
        results = []
        suffix = project.tgt_lang.split("-")[0].lower() or "out"
        for doc_id in doc_ids:
            row = project.db.get_document(doc_id)
            if row is None:
                continue
            src = project.root / row["path"]
            warnings = []
            statuses = project.db.list_segments(doc_id=doc_id, translatable=True)
            pending = [s for s in statuses if s["status"] in ("pending", "failed")]
            if pending:
                warnings.append(f"{len(pending)} 段未完成（pending/failed）")
            if src.exists() and _sha(src.read_bytes()) != row["content_hash"]:
                warnings.append("源文件在翻译后被修改，译文可能与源文不一致")
                if not force:
                    results.append({"doc_id": doc_id, "path": row["path"], "ok": False,
                                    "warnings": warnings, "out": None})
                    continue
            if not src.exists():
                results.append({"doc_id": doc_id, "path": row["path"], "ok": False,
                                "warnings": ["源文件缺失"], "out": None})
                continue
            adapter = get_adapter(row["format"])
            model = adapter.parse(src, opts={"ruby_loose": self.project.ruby_loose})
            # 渲染按块 seq 对齐（导入/导出重放同一确定性分段算法）
            translations = {s["seq"]: s["tgt_text"] for s in statuses if s["tgt_text"]}
            ruby_maps = {}
            for s in statuses:
                if s["ruby_map"]:
                    try:
                        ruby_maps[s["seq"]] = json.loads(s["ruby_map"])
                    except Exception:
                        pass
            ext = ".md" if row["format"] == "img" else src.suffix
            out = project.target_dir() / f"{src.stem}.{suffix}{ext}"
            try:
                adapter.render(out, model, translations, mode, ruby_maps=ruby_maps)
            except FormatError as e:
                results.append({"doc_id": doc_id, "path": row["path"], "ok": False,
                                "warnings": [str(e)], "out": None})
                continue
            project.db.set_doc_fields(doc_id, status="exported")
            results.append({"doc_id": doc_id, "path": row["path"], "ok": True,
                            "warnings": warnings, "out": str(out)})
        return results
