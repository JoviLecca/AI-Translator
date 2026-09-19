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
from adapters.ruby import strip_ruby_markup
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
        old = project.db.get_document_by_path(f"source/{dest.name}")
        old_hash = old["content_hash"] if old else None
        shutil.copy2(src, dest)
        content_hash = _sha(dest.read_bytes())
        model = get_adapter(fmt).parse(
            dest, opts={"ruby_loose": project.ruby_loose, "src_lang": project.src_lang})
        blocks = [{
            "seq": b.seq, "text": b.text, "is_heading": b.is_heading,
            "translatable": b.translatable,
            "src_hash": _sha_text(b.text.strip()),
            "ruby": b.meta.get("ruby") or None,
        } for b in model.blocks]
        rel = f"source/{dest.name}"
        doc_id = project.db.upsert_document(rel, fmt, content_hash, {})
        # 审查第3轮修复：文件内容变更后重置归纳标记，新内容可被再次归纳
        # （设计 §7.4：内容变更应重新识别；旧实现保持 terms_extracted=1 导致新术语永不被归纳）
        if old is not None and old_hash != content_hash:
            project.db.set_doc_fields(doc_id, terms_extracted=0)
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

    # ---------- 预检（设计 v0.7 #42 / 增补设计 §2.6） ----------
    def _segments(self, doc_ids: list[int] | None = None, **kw):
        """按文档范围取段；doc_ids 为空表示全部文档（用户反馈：支持只翻译选中文件）。"""
        db = self.project.db
        if not doc_ids:
            return db.list_segments(**kw)
        out = []
        for did in doc_ids:
            out.extend(db.list_segments(doc_id=did, **kw))
        return out

    def precheck(self, doc_ids: list[int] | None = None) -> dict:
        db = self.project.db
        # 审查第3轮修复：外部（如 Excel）改过术语表 → 预检即自动重载，
        # 保证开始翻译用的术语与 cfg_hash 与文件一致（设计 §12 外部变更检测）
        glossary_reloaded = False
        if self.project.glossary.external_changed():
            # 重载 + 补齐变更历史（外部改动原先不落快照，术语变更影响分析会比不出差异）
            self.project.reload_glossary()
            glossary_reloaded = True
        rows = self._segments(doc_ids, translatable=True, status_in=("pending", "failed"))
        chars = sum(len(r["src_text"]) for r in rows)
        cfg_now = self.project.cfg_hash()
        stale = 0
        for r in self._segments(doc_ids, translatable=True,
                                status_in=("machine_translated", "human_edited", "confirmed")):
            if r["cfg_hash"] != cfg_now:
                stale += 1
        slide = self.slide()
        return {
            "pending": len(rows), "chars": chars, "stale_translations": stale,
            "cfg_hash": cfg_now,
            "slide": slide,
            "ruby_policy": self.project.ruby_policy,
            "glossary_reloaded": glossary_reloaded,
            "estimate": costs.estimate_run(chars, slide=slide) if rows else None,
            "glossary_terms": len(self.project.glossary.entries),
            "doc_ids": list(doc_ids) if doc_ids else None,
            "doc_count": len(doc_ids) if doc_ids else len(db.list_documents()),
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
    def start(self, on_progress=None, on_done=None, provider=None,
              doc_ids: list[int] | None = None) -> "RunHandle":
        project, db = self.project, self.project.db
        rows = self._segments(doc_ids, translatable=True, status_in=("pending", "failed"))
        if not rows:
            raise RuntimeError("所选范围内没有待翻译的段落")
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
        # 人工编辑即视为已复核：清除待复核标记（审查第2轮修复）
        self.project.db.update_segment(seg_id, tgt=tgt, status="human_edited",
                                       review_flag=False)

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

    # ---------- 目标路径与「同名文件」处理 ----------
    def _target_suffix(self) -> str:
        return self.project.tgt_lang.split("-")[0].lower() or "out"

    def target_path(self, row, output_format: str | None = None) -> Path:
        """算出该文档的导出目标路径。

        UI 用它统计「target/ 里已存在多少个同名文件」，好让用户在导出前做选择。
        """
        src = self.project.root / row["path"]
        fmt_key = output_format or row["format"]
        if fmt_key != row["format"]:
            ext = f".{fmt_key}"
        else:
            ext = ".md" if row["format"] == "img" else src.suffix
        return self.project.target_dir() / f"{src.stem}.{self._target_suffix()}{ext}"

    @staticmethod
    def _resolve_out(out: Path, on_conflict: str) -> tuple[Path | None, str, bool]:
        """按 on_conflict 处理 target/ 中已存在的同名导出文件。

        - ``overwrite``（默认，沿用旧行为）：直接覆盖
        - ``keep_both``：自动改名 ``xxx(2).md``，两份都保留
        - ``skip``：本次不导出该文档（保留已有文件）

        返回 (最终路径, 说明文案, 是否覆盖了已有文件)；说明文案进入导出结果提示。
        """
        existed = out.exists()
        if on_conflict == "skip":
            return (None, "", existed) if existed else (out, "", False)
        if on_conflict == "keep_both" and existed:
            n = 2
            while True:
                cand = out.with_name(f"{out.stem}({n}){out.suffix}")
                if not cand.exists():
                    return cand, f"已有同名文件，本次另存为 {cand.name}", False
                n += 1
        return out, "", existed

    def export(self, doc_ids: list[int], mode: str = "target",
               force: bool = False, output_format: str | None = None,
               on_progress=None, cancel_check=None,
               on_conflict: str = "overwrite") -> list[dict]:
        """on_progress(done, total, path)：每处理一个文档前回调，done 为已完成数。

        cancel_check() 返回 True 时停止（已导出的文件保留，未处理的不动）。
        on_conflict：同名文件处理方式，见 `_resolve_out`；默认覆盖（旧行为）。
        本方法在调用线程同步执行；UI 侧用 QProgressDialog + processEvents 驱动显示。
        """
        project = self.project
        results = []
        total = len(doc_ids)
        stopped = False
        for idx, doc_id in enumerate(doc_ids):
            if cancel_check is not None and cancel_check():
                stopped = True
                break
            row = project.db.get_document(doc_id)
            if on_progress is not None:
                try:
                    on_progress(idx, total, row["path"] if row else "")
                except Exception:  # noqa: BLE001 进度回调不应影响导出
                    pass
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
            model = adapter.parse(src, opts={"ruby_loose": project.ruby_loose})
            # 渲染按块 seq 对齐（导入/导出重放同一确定性分段算法）
            translations = {s["seq"]: s["tgt_text"] for s in statuses if s["tgt_text"]}
            # drop 策略：导出前兜底剥离 ruby 标记与注音。
            # 引擎落库时已清理，但**修复前导入的旧项目**其 tgt_text 里可能仍带着
            # <ruby> 标签（源文从未被 token 化，模型把标签原样译回）。用户要求
            # drop 后译文里不出现 ruby 格式，这里保证导出结果正确，且**无需重新翻译**。
            # 策略取值与引擎一致：文件级标签覆盖优先。
            if (_tags_of(row).get("ruby_policy") or project.ruby_policy) == "drop":
                translations = {k: strip_ruby_markup(v) for k, v in translations.items()}
            ruby_maps = {}
            for s in statuses:
                if s["ruby_map"]:
                    try:
                        ruby_maps[s["seq"]] = json.loads(s["ruby_map"])
                    except Exception:
                        pass

            # 目标路径统一先算出来（跨格式只是扩展名不同），再按「同名文件」设置处理：
            # 旧行为是直接覆盖，用户要求给出选择（保留两者 / 跳过 / 覆盖）
            fmt_key = output_format or row["format"]
            cross = fmt_key != row["format"]
            desired = self.target_path(row, output_format)
            out, conflict_note, overwrote = self._resolve_out(desired, on_conflict)
            if conflict_note:
                warnings.append(conflict_note)
            if out is None:
                # skip 模式：保留 target/ 中已有文件，本次不重新导出该文档
                results.append({"doc_id": doc_id, "path": row["path"], "ok": True,
                                "warnings": warnings, "out": None, "skipped": True,
                                "overwrote": False})
                continue

            # 跨格式导出（S2）：output_format 指定且不同于源格式 → 构造合成模型
            if cross:
                try:
                    out, cross_warn = self._cross_format_export(
                        model, translations, fmt_key, mode, out)
                    warnings.extend(cross_warn)
                    results.append({"doc_id": doc_id, "path": row["path"], "ok": True,
                                    "warnings": warnings, "out": str(out),
                                    "overwrote": overwrote})
                    project.db.set_doc_fields(doc_id, status="exported")
                    continue
                except FormatError as e:
                    results.append({"doc_id": doc_id, "path": row["path"], "ok": False,
                                    "warnings": [str(e)], "out": None})
                    continue

            try:
                adapter.render(out, model, translations, mode, ruby_maps=ruby_maps)
            except FormatError as e:
                results.append({"doc_id": doc_id, "path": row["path"], "ok": False,
                                "warnings": [str(e)], "out": None})
                continue
            project.db.set_doc_fields(doc_id, status="exported")
            results.append({"doc_id": doc_id, "path": row["path"], "ok": True,
                            "warnings": warnings, "out": str(out),
                            "overwrote": overwrote})
        if on_progress is not None and not stopped:
            try:
                on_progress(total, total, "")
            except Exception:  # noqa: BLE001
                pass
        return results

    # ---------- 跨格式导出（S2） ----------
    def _cross_format_export(self, model, translations: dict, fmt: str,
                             mode: str, out: Path) -> tuple[Path, list[str]]:
        """从源格式模型构造目标格式文件（段级数据不变，格式重排）。

        `out` 由调用方算好 —— 已按「同名文件」策略解析（覆盖/另存/跳过）。
        """
        import re as _re
        from adapters.base import Block
        warnings = []
        blocks = []
        for b in model.blocks:
            if not b.translatable:
                continue
            tgt = translations.get(b.seq, b.text)
            if b.is_heading:
                # 去掉源格式的标题前缀标记（如 md 的 "# "，可能被翻译器加了前缀）
                tgt = _re.sub(r"#{1,6}\s*", "", tgt)
            blocks.append(Block(seq=b.seq, text=tgt,
                                is_heading=b.is_heading, translatable=True,
                                meta={"kind": "para", "para": len(blocks), "part": 0}))
        if fmt == "txt":
            lines = [b.text for b in blocks]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
        elif fmt == "md":
            parts = []
            for b in blocks:
                if b.is_heading:
                    parts.append(f"# {b.text}")
                else:
                    parts.append(b.text)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
        elif fmt == "html":
            from lxml import html as lhtml
            doc = lhtml.document_fromstring("<html><body></body></html>")
            body = doc.find("body")
            for b in blocks:
                if b.is_heading:
                    # 从源文推断标题层级（# → h1, ## → h2 …）
                    m = _re.match(r"(#{1,6})", model.blocks[b.seq].text
                                  if b.seq < len(model.blocks) else "")
                    level = len(m.group(1)) if m else 1
                    el = lhtml.Element(f"h{min(level, 6)}")
                else:
                    el = lhtml.Element("p")
                el.text = b.text
                body.append(el)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(lhtml.tostring(doc, encoding="unicode"), encoding="utf-8")
        elif fmt == "docx":
            from docx import Document as new_docx
            doc = new_docx()
            for b in blocks:
                if b.is_heading:
                    doc.add_heading(b.text, level=1)
                else:
                    doc.add_paragraph(b.text)
            doc.save(str(out))
        else:
            raise FormatError(f"不支持的跨格式导出：{fmt}")
        warnings.append(f"已从 {model.fmt} 转换为 {fmt}（结构按段落重排，源格式特有元素可能丢失）")
        return out, warnings
