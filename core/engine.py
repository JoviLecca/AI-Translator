"""翻译执行引擎（设计 §7.5 + 增补设计 §1 振假名 / §2 上下文滑窗）。

- 批调度：同文档连续段成批（≤10 段或 ≤4000 字符），并发信号量控制；
- 上下文滑窗：批首前 N 段（已定稿译文优先）+ 批尾后 N 段（源文），
  合计 ≤3000 字符（保近邻截断）；段级滑窗语义由批内互见 + 批外补足等价覆盖；
- TM 预检 + 运行内去重（src_hash 只送一次）；
- 错误分型（v0.6）：401/402 暂停 Run；内容策略仅段级失败；漏段单段降级；
- 振假名三策略校验（增补设计 §1.5）：
  drop  → 残留 token 剥离 + 待复核；
  keep  → token 丢失单段重译 → 仍丢补回原注音 + 待复核；
  translate → 同 keep，另收 ruby 字段译注音；缺失的 token 降级为原假名 + 待复核；
- 保护已确认内容（v0.7 #41）；取消语义（v0.6 #37）。
"""
from __future__ import annotations

import asyncio
import json
import re
import time

from adapters.base import effectively_empty, placeholders_ok
from adapters.ruby import RUBY_TOKEN_FULL, strip_ruby_tokens
from llm import prompt_builder as PB
from llm.errors import ContentPolicyError, FatalRunError, LLMError, ResponseFormatError, \
    RetryableError
from llm.provider import ILLMProvider, Message
from storage.db import Database

BATCH_SEGS = 10
BATCH_CHARS = 4000
MAX_CONTEXT_CHARS = 3000  # 前后文合计上限，超出从最远端截断（保近邻）

_PREV_SQL = ("SELECT tgt_text,src_text FROM segments WHERE doc_id=? AND translatable=1 "
             "AND seq<? AND status IN ('confirmed','human_edited','machine_translated') "
             "AND tgt_text IS NOT NULL ORDER BY seq DESC LIMIT ?")
_NEXT_SQL = ("SELECT src_text FROM segments WHERE doc_id=? AND translatable=1 AND seq>? "
             "ORDER BY seq ASC LIMIT ?")


class RunEngine:
    def __init__(self, provider: ILLMProvider, db: Database, *,
                 cfg_hash: str, src_lang: str, tgt_lang: str, style_desc: str,
                 terms: list[tuple[str, list[str], str, int]],
                 doc_tags: dict[int, dict],
                 concurrency: int = 4, context_n: int = 2,
                 retries: int = 3, backoff: float = 0.05,
                 ruby_policy: str = "drop",
                 cancel_event=None, progress=None):
        self.provider = provider
        self.db = db
        self.cfg_hash = cfg_hash
        self.src_lang, self.tgt_lang = src_lang, tgt_lang
        self.style_desc = style_desc
        self.terms = terms
        self.doc_tags = doc_tags
        self.concurrency = concurrency
        self.context_n = context_n
        self.retries = retries
        self.backoff = backoff
        self.ruby_policy = ruby_policy
        self.cancel_event = cancel_event
        self.progress = progress
        self._langs_differ = src_lang.split("-")[0].lower() != tgt_lang.split("-")[0].lower()

    # ---------- 进度 ----------
    def _report(self, event: str, **kw) -> None:
        if self.progress:
            try:
                self.progress({"event": event, **kw})
            except Exception:
                pass

    def _cancelled(self) -> bool:
        return bool(self.cancel_event and self.cancel_event.is_set())

    # ---------- 主流程 ----------
    async def run_translation(self, run_id: int, rows: list) -> dict:
        db = self.db
        stats = {"done": 0, "failed": 0, "tm": 0, "dedup": 0, "tokens_in": 0, "tokens_out": 0,
                 "cost": 0.0, "paused": False, "cancelled": False, "error": "", "total": len(rows)}
        self._stats = stats
        db.update_run(run_id, status="running")

        # TM 预检
        todo = []
        for r in rows:
            if effectively_empty(r["src_text"]):
                # 历史遗留的空白可译段（反馈 #2/#3）：直接置空完成，不查 TM、不参与去重，
                # 防止"空白段哈希相同 → 一处翻译被回填到全部空白段"的重复内容问题
                db.update_segment(r["id"], tgt="", status="machine_translated")
                db.update_run_item(run_id, r["id"], "done")
                stats["done"] += 1
                continue
            tm = db.find_tm(r["src_hash"], self.cfg_hash, exclude_doc=r["doc_id"])
            if tm and tm["tgt_text"]:
                db.update_segment(r["id"], tgt=tm["tgt_text"], status=tm["status"],
                                  cfg_hash=self.cfg_hash)
                db.update_run_item(run_id, r["id"], "done")
                stats["tm"] += 1
                stats["done"] += 1
            else:
                todo.append(r)
        db.update_run(run_id, done=stats["done"])
        self._report("tm", **stats)

        # 运行内去重：相同 src_hash 只送翻一次，结果回填到重复段（v0.7 #43）
        seen: dict[str, dict] = {}
        dupes: dict[str, list] = {}
        unique_todo: list = []
        for r in todo:
            h = r["src_hash"]
            if h in seen:
                dupes.setdefault(h, []).append(r)
            else:
                seen[h] = r
                unique_todo.append(r)
        self._dupes = dupes

        # 分批：同文档连续
        batches, cur, cur_doc, chars = [], [], None, 0
        for r in unique_todo:
            if cur and (r["doc_id"] != cur_doc or len(cur) >= BATCH_SEGS or chars >= BATCH_CHARS):
                batches.append(cur)
                cur, chars = [], 0
            cur.append(r)
            cur_doc = r["doc_id"]
            chars += len(r["src_text"])
        if cur:
            batches.append(cur)

        sem = asyncio.Semaphore(self.concurrency)
        fatal: list[str] = []

        async def do_batch(batch):
            if fatal or self._cancelled():
                return
            async with sem:
                if fatal or self._cancelled():
                    return
                try:
                    await self._translate_batch(run_id, batch, stats)
                except FatalRunError as e:
                    fatal.append(str(e))
                    self._report("paused", error=str(e))

        await asyncio.gather(*(do_batch(b) for b in batches))

        stats["cost"] = (stats["tokens_in"] * self.provider.price_in
                         + stats["tokens_out"] * self.provider.price_out) / 1e6
        if fatal:
            status, error = "paused", fatal[0]
        elif self._cancelled():
            status, error = "cancelled", ""
        else:
            status, error = "completed", ""
        stats["paused"] = bool(fatal)
        stats["cancelled"] = status == "cancelled"
        stats["error"] = error
        db.update_run(run_id, status=status, done=stats["done"], failed=stats["failed"],
                      tokens_in=stats["tokens_in"], tokens_out=stats["tokens_out"],
                      cost=stats["cost"], error=error, finished_at=time.time())
        self._report("finished", **stats)
        return stats

    # ---------- 上下文滑窗（增补设计 §2） ----------
    def _context_blocks(self, batch) -> tuple[list[str], list[str]]:
        first, last = batch[0], batch[-1]
        n = self.context_n
        if n <= 0:
            return [], []
        prev = self.db.execute(_PREV_SQL, (first["doc_id"], first["seq"], n)).fetchall()
        before = [r["tgt_text"] for r in reversed(prev)]
        nxt = self.db.execute(_NEXT_SQL, (last["doc_id"], last["seq"], n)).fetchall()
        after = [r["src_text"] for r in nxt]
        # 合计 ≤MAX_CONTEXT_CHARS：从最远端截断（保近邻）——前文删头、后文删尾
        while before and sum(map(len, before)) + sum(map(len, after)) > MAX_CONTEXT_CHARS:
            before.pop(0)
        while after and sum(map(len, before)) + sum(map(len, after)) > MAX_CONTEXT_CHARS:
            after.pop()
        return before, after

    # ---------- 批级 ----------
    def _policy_for(self, doc_id: int) -> str:
        return (self.doc_tags.get(doc_id) or {}).get("ruby_policy") or self.ruby_policy

    def _system_for(self, doc_id: int, batch_text: str) -> str:
        terms = PB.filter_terms(self.terms, batch_text)
        policy = None
        if RUBY_TOKEN_FULL.search(batch_text):
            policy = self._policy_for(doc_id)
        return PB.translation_system(self.src_lang, self.tgt_lang, self.style_desc,
                                     self.doc_tags.get(doc_id, {}), terms,
                                     ruby_policy=policy)

    async def _translate_batch(self, run_id: int, batch: list, stats: dict) -> None:
        ids = [r["id"] for r in batch]
        before, after = self._context_blocks(batch)
        batch_text = "\n".join(r["src_text"] for r in batch)
        policy = self._policy_for(batch[0]["doc_id"]) if RUBY_TOKEN_FULL.search(batch_text) \
            else None
        msgs = [
            Message("system", self._system_for(batch[0]["doc_id"], batch_text)),
            Message("user", PB.translation_user([(r["id"], r["src_text"]) for r in batch],
                                                 before=before, after=after)),
        ]
        try:
            result = await self._call_with_retry(msgs, json_mode=True)
        except ContentPolicyError:
            # 内容策略拒绝 → 整批降级为逐段：只有真正触发过滤的段落失败（v0.6 #36）
            for r in batch:
                await self._translate_single(run_id, r, stats, before, after,
                                             cause="content_policy")
            self._report("batch_policy", **stats)
            return
        except FatalRunError:
            raise  # 401/402 → 暂停整个 Run（v0.6 #35）
        except (ResponseFormatError, RetryableError, LLMError) as e:
            self._account(result=None)
            for r in batch:
                await self._translate_single(run_id, r, stats, before, after, cause=str(e))
            return
        self._account(result)
        try:
            parsed, ruby_parsed = PB.parse_translations_full(result.text, ids)
        except ResponseFormatError:
            for r in batch:
                await self._translate_single(run_id, r, stats, before, after, cause="parse")
            return

        for r in batch:
            t = parsed.get(r["id"])
            ruby_map = (ruby_parsed.get(r["id"]) or {}) if policy == "translate" else {}
            suspicious = (
                t is None or not str(t).strip()
                or (self._langs_differ and str(t).strip() == r["src_text"].strip())
                or not placeholders_ok(r["src_text"], str(t or ""))
                or (policy in ("keep", "translate") and self._ruby_missing(r, str(t or "")))
            )
            if suspicious:
                ok, fixed, fixed_ruby = await self._single(run_id, r, stats, before, after)
                if ok:
                    self._mark_done(run_id, r, stats, fixed, ruby_map=fixed_ruby)
                elif fixed is not None:
                    # 重译仍异常：走落定链（含 keep/translate 丢 token 的补回原注音）
                    text, rt_map, _ = self._finalize_ruby(r, fixed, fixed_ruby, policy)
                    self._mark_done(run_id, r, stats, text, review_flag=True,
                                    ruby_map=rt_map or fixed_ruby)
                elif t is not None and str(t).strip():
                    text, rt_map, flag = self._finalize_ruby(r, str(t), ruby_map, policy)
                    self._mark_done(run_id, r, stats, text, review_flag=flag, ruby_map=rt_map)
                else:
                    self._fail(run_id, r, stats, "empty_after_retry")
            else:
                text, rt_map, flag = self._finalize_ruby(r, str(t), ruby_map, policy)
                self._mark_done(run_id, r, stats, text, review_flag=flag, ruby_map=rt_map)

    async def _translate_single(self, run_id: int, r, stats: dict,
                                before: list[str], after: list[str],
                                cause: str = "") -> None:
        ok, text, ruby_map = await self._single(run_id, r, stats, before, after)
        if ok:
            self._mark_done(run_id, r, stats, text, ruby_map=ruby_map)
        elif text is not None:
            batch_text = r["src_text"]
            policy = None
            if RUBY_TOKEN_FULL.search(batch_text):
                policy = self._policy_for(r["doc_id"])
            final, rt_map, _ = self._finalize_ruby(r, text, ruby_map, policy)
            self._mark_done(run_id, r, stats, final, review_flag=True,
                            ruby_map=rt_map or ruby_map)
        else:
            self._fail(run_id, r, stats, f"single:{cause}" if cause else "single_failed")

    # ---------- 振假名校验链（增补设计 §1.5） ----------
    @staticmethod
    def _ruby_missing(r, tgt: str) -> bool:
        """keep/translate 策略下源文注音槽在译文中丢失。"""
        return (RUBY_TOKEN_FULL.search(r["src_text"]) is not None
                and not RUBY_TOKEN_FULL.search(tgt))

    def _finalize_ruby(self, r, tgt: str, ruby_map: dict, policy: str | None):
        """按策略落定译文与注音映射；返回 (text, ruby_map, review_flag)。

        审查第2轮加固：
        - 伪造/未知 token、重复 token 一律清除（待复核）；
        - 部分丢失的 token 补回原注音（待复核），不再静默丢注音；
        - 源文无注音槽但译文伪造 token → 剥离。
        """
        flag = False
        try:
            entries = json.loads(r["ruby_src"] or "[]")
        except Exception:
            entries = []
        valid_ordered = [e["token"] for e in entries if e.get("token")]
        valid = set(valid_ordered)
        rt_of = {e["token"]: e.get("rt", "") for e in entries}

        if not valid or policy == "drop":
            if RUBY_TOKEN_FULL.search(tgt):
                return strip_ruby_tokens(tgt), {}, True
            return tgt, {}, False

        # keep / translate：规整 token —— 未知删除、重复折叠为一次
        seen: list[str] = []

        def _clean(m: re.Match) -> str:
            tok = m.group(0)
            if tok in valid and tok not in seen:
                seen.append(tok)
                return tok
            return ""

        cleaned = RUBY_TOKEN_FULL.sub(_clean, tgt)
        cleaned = re.sub(r" +([，。！？；、）】》」』,.!?;:])", r"\1", cleaned)
        if cleaned != tgt:
            flag = True
        tgt = cleaned
        missing = [tok for tok in valid_ordered if tok not in seen]
        if missing:
            # 部分丢失（重译也没救）→ 补回原注音 + 待复核（保底不断稿）
            tgt = tgt.rstrip() + "".join(f"《{rt_of.get(tok, '')}》" for tok in missing)
            return tgt, {}, True
        if policy == "translate":
            rt_map = dict(ruby_map)
            for tok in seen:
                if tok not in rt_map:
                    flag = True  # 漏给译注音 → 渲染回退原假名
            return tgt, rt_map, flag
        return tgt, {}, flag

    # ---------- 单段 ----------
    async def _single(self, run_id: int, r, stats: dict,
                      before: list[str], after: list[str]) -> tuple[bool, str | None, dict]:
        batch_text = r["src_text"]
        policy = None
        if RUBY_TOKEN_FULL.search(batch_text):
            policy = self._policy_for(r["doc_id"])
        msgs = [
            Message("system", self._system_for(r["doc_id"], batch_text)),
            Message("user", PB.translation_user([(r["id"], r["src_text"])],
                                                 before=before, after=after)),
        ]
        try:
            result = await self._call_with_retry(msgs, json_mode=True)
        except ContentPolicyError:
            return False, None, {}
        except FatalRunError:
            raise  # 暂停 Run，不降级为段失败
        except LLMError:
            return False, None, {}
        self._account(result)
        try:
            parsed, ruby_parsed = PB.parse_translations_full(result.text, [r["id"]])
            t = parsed.get(r["id"])
            ruby_map = (ruby_parsed.get(r["id"]) or {}) if policy == "translate" else {}
        except ResponseFormatError:
            return False, None, {}
        if t is None or not str(t).strip():
            return False, None, {}
        if not placeholders_ok(r["src_text"], str(t)):
            return False, str(t), {}
        if policy in ("keep", "translate") and self._ruby_missing(r, str(t)):
            return False, str(t), {}
        text, rt_map, _ = self._finalize_ruby(r, str(t), ruby_map, policy)
        return True, text, rt_map

    # ---------- 基础设施 ----------
    async def _call_with_retry(self, msgs, *, json_mode: bool):
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            if self._cancelled():
                raise RetryableError("cancelled")
            try:
                return await self.provider.chat(msgs, json_mode=json_mode)
            except RetryableError as e:
                last = e
                if attempt < self.retries:
                    await asyncio.sleep(self.backoff * (2 ** attempt))
        raise last or RetryableError("unknown")

    def _account(self, result) -> None:
        """用量记账：每次成功调用累计 token（成本在 Run 收尾统一折算）。"""
        stats = getattr(self, "_stats", None)
        if result is not None and stats is not None:
            stats["tokens_in"] += result.tokens_in
            stats["tokens_out"] += result.tokens_out

    def _mark_done(self, run_id: int, r, stats: dict, text: str, review_flag: bool = False,
                   ruby_map: dict | None = None) -> None:
        ruby_json = json.dumps(ruby_map, ensure_ascii=False) if ruby_map else None
        self.db.update_segment(r["id"], tgt=text, status="machine_translated",
                               review_flag=review_flag, cfg_hash=self.cfg_hash,
                               ruby_map=ruby_json)
        self.db.update_run_item(run_id, r["id"], "done")
        stats["done"] += 1
        # 回填同 hash 的重复段（运行内去重）
        for d in getattr(self, "_dupes", {}).get(r["src_hash"], []):
            self.db.update_segment(d["id"], tgt=text, status="machine_translated",
                                   review_flag=review_flag, cfg_hash=self.cfg_hash,
                                   ruby_map=ruby_json)
            self.db.update_run_item(run_id, d["id"], "done")
            stats["done"] += 1
            stats["dedup"] += 1
        self._report("segment", seg=r["id"], **{k: stats[k] for k in
                                                ("done", "failed", "tm", "total")})

    def _fail(self, run_id: int, r, stats: dict, reason: str) -> None:
        self.db.update_segment(r["id"], status="failed")
        self.db.update_run_item(run_id, r["id"], "failed", error=reason)
        stats["failed"] += 1
        for d in getattr(self, "_dupes", {}).get(r["src_hash"], []):
            self.db.update_segment(d["id"], status="failed")
            self.db.update_run_item(run_id, d["id"], "failed", error=reason)
            stats["failed"] += 1
        self._report("segment_failed", seg=r["id"], reason=reason,
                     **{k: stats[k] for k in ("done", "failed", "tm", "total")})
