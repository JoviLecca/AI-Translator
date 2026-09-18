"""术语自动归纳（设计 §7.4）：两阶段——本地候选抽取 → LLM 判定。

- 阶段一按源语言选择策略：中文 jieba 分词（不可用时降级 CJK bigram）；其他语言词级正则；
- 判定范围含专有概念与高频人名/地名/称谓（v0.5 #32）；
- 增量：只处理 documents.terms_extracted=0 的文件（FR-07）；
- 产出 candidate 状态术语，经用户审核后写入 glossary（详见 TermImpactService/TermsReview）。
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import Counter

from llm import prompt_builder as PB
from llm.errors import FatalRunError, RetryableError
from llm.provider import ILLMProvider, Message
from adapters.ruby import RUBY_TOKEN_FULL

_CJK = re.compile(r"[\u4e00-\u9fff]")
_KATAKANA = re.compile(r"[\u30A0-\u30FF\u31F0-\u31FFー]{2,}")
_ZH_STOP = set("的了是在和与也很他她它你您我你们我们他们她们这那一个一些不没有自己之之中上下里被把让就像是如果但是所以因为还又再去来说要会能可能已经于是因此并且而且呢吧啊嘛哦呀罢了其实然后开始终于突然其中各种非常十分".split() + ["一个", "什么", "怎么", "这样", "那样", "没有", "已经", "知道", "起来", "出来", "过来"])
_JA_STOP = {"ノ", "カ", "コ", "ソ", "ド", "ト"}  # 单字符片假名误切保护（正则已要求≥2字符）
_EN_STOP = {"the", "and", "for", "are", "but", "not", "you", "all", "can", "her", "was",
            "one", "our", "out", "his", "has", "have", "this", "that", "with", "from",
            "they", "will", "would", "there", "their", "what", "about", "which", "when",
            "your", "said", "into", "them", "then", "than", "some", "could", "were"}


def extract_candidates(texts: list[str], src_lang: str,
                       top_k: int = 300, min_freq: int = 2) -> list[tuple[str, int]]:
    """阶段一：本地频次候选（免费），语言相关策略（zh: jieba；ja: 片假名+汉字；其他: 拉丁词）。

    振假名注音槽 {rN} 先剥离，防止注音内容混入候选（增补设计 §1.7）。
    """
    text = "\n".join(RUBY_TOKEN_FULL.sub("", t) for t in texts)
    if not text.strip():
        return []
    lang = src_lang.lower()
    if lang.startswith("ja"):
        # 日文：片假名术语串（人名/专有名词主力）+ 汉字 bigram（概念词）
        words = [m.group(0) for m in _KATAKANA.finditer(text)
                 if m.group(0) not in _JA_STOP]
        seqs = _CJK.findall(text)
        words += [a + b for a, b in zip(seqs, seqs[1:])]
    elif lang.startswith("zh"):
        try:
            import jieba
            toks = jieba.lcut(text)
            words = [t for t in toks
                     if len(t) >= 2 and _CJK.search(t) and t not in _ZH_STOP]
        except ImportError:
            seqs = _CJK.findall(text)
            words = [a + b for a, b in zip(seqs, seqs[1:])]
    else:
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", text)
                 if w.lower() not in _EN_STOP]
    counter = Counter(words)
    return [(w, c) for w, c in counter.most_common(top_k) if c >= min_freq]


class InductionService:
    def __init__(self, project, provider: ILLMProvider, retries: int = 3, backoff: float = 0.5):
        self.project = project
        self.provider = provider
        self.retries = retries
        self.backoff = backoff

    def run(self, doc_ids: list[int] | None = None, on_progress=None) -> dict:
        return asyncio.run(self._run(doc_ids, on_progress))

    async def _run(self, doc_ids, on_progress) -> dict:
        db = self.project.db
        project = self.project
        if doc_ids is None:
            docs = [d for d in db.list_documents() if not d["terms_extracted"]]
        else:
            docs = [db.get_document(i) for i in doc_ids]
        docs = [d for d in docs if d]
        run_id = db.create_run("terms", len(docs))
        result = {"docs": len(docs), "candidates": [], "tokens_in": 0,
                  "tokens_out": 0, "cost": 0.0, "paused": False, "error": ""}
        if not docs:
            db.update_run(run_id, status="completed", done=0, finished_at=time.time())
            return result
        texts_by_doc: dict[int, str] = {}
        import json as _json
        for d in docs:
            segs = db.list_segments(doc_id=d["id"], translatable=True)
            texts_by_doc[d["id"]] = "\n".join(s["src_text"] for s in segs)
        all_texts = list(texts_by_doc.values())

        cands = extract_candidates(all_texts, project.src_lang)
        freq = {w: c for w, c in cands}
        merged: dict[str, tuple[list[str], str]] = {}
        fatal = ""
        batch_size = 40
        for i in range(0, len(cands), batch_size):
            batch = cands[i:i + batch_size]
            tags = {}
            for d in docs:
                try:
                    tags.update({k: v for k, v in _json.loads(d["tags"] or "{}").items() if v})
                except Exception:
                    pass
            msgs = [
                Message("system", PB.induction_system(project.src_lang, project.tgt_lang, tags)),
                Message("user", PB.induction_user([w for w, _ in batch])),
            ]
            try:
                resp = await self._call(msgs)
            except FatalRunError as e:
                fatal = str(e)
                break
            except Exception:
                continue  # 该批失败跳过，不阻塞整体
            result["tokens_in"] += resp.tokens_in
            result["tokens_out"] += resp.tokens_out
            try:
                for src, cands2, note in PB.parse_terms(resp.text):
                    if src in merged:
                        old = merged[src]
                        merged[src] = (list(dict.fromkeys(old[0] + cands2))[:3],
                                       note or old[1])
                    else:
                        merged[src] = (cands2[:3], note)
            except Exception:
                continue
            if on_progress:
                on_progress({"event": "induction", "done": min(i + batch_size, len(cands)),
                             "total": len(cands)})

        result["cost"] = (result["tokens_in"] * self.provider.price_in
                          + result["tokens_out"] * self.provider.price_out) / 1e6
        if fatal:
            # 暂停时不标记 terms_extracted，修复配置后可整体重跑（增量语义）
            db.update_run(run_id, status="paused", tokens_in=result["tokens_in"],
                          tokens_out=result["tokens_out"], cost=result["cost"],
                          error=fatal, finished_at=time.time())
            result["paused"] = True
            result["error"] = fatal
            return result
        existing = {t.src for t in project.glossary.entries}
        for src, (cands2, note) in merged.items():
            if src in existing:
                continue  # 用户已定稿的术语优先（设计 §7.4）
            db.upsert_term(src, cands2, note, origin="ai_induced",
                           status="candidate", occurrences=freq.get(src, 1))
            result["candidates"].append({"src": src, "candidates": cands2, "note": note,
                                         "occurrences": freq.get(src, 1)})
        for d in docs:
            db.set_doc_fields(d["id"], terms_extracted=1)
        db.update_run(run_id, status="completed",
                      done=len(docs), tokens_in=result["tokens_in"],
                      tokens_out=result["tokens_out"], cost=result["cost"],
                      finished_at=time.time())
        return result

    async def _call(self, msgs):
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return await self.provider.chat(msgs, json_mode=True)
            except RetryableError as e:
                last = e
                if attempt < self.retries:
                    await asyncio.sleep(self.backoff * (2 ** attempt))
        raise last or RetryableError("unknown")

    # ---------- 审核 ----------
    def approve(self, terms: list[tuple[str, list[str], str]]) -> int:
        g = self.project.glossary
        n = 0
        for src, cands, note in terms:
            g.add(src, cands, note)
            self.project.db.upsert_term(src, cands, note, origin="ai_induced",
                                        status="approved")
            n += 1
        g.save()
        self.project.db.save_terms_revision(g.effective_hash(), g.to_payload())
        return n

    def reject(self, srcs: list[str]) -> None:
        for src in srcs:
            row = next((t for t in self.project.db.list_terms() if t["src_term"] == src), None)
            if row:
                self.project.db.upsert_term(src, row["tgt_candidates"].split("|"),
                                            row["note"], origin="ai_induced",
                                            status="rejected")
