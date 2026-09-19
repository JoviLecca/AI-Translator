"""术语变更影响分析（设计 §7.4 / v0.4 #25）。

术语修改/删除后，反查仍在使用**旧候选**的已译段落，提供「全局替换为新候选」或
「标记重译」，替换可撤销。

「旧候选」的来源按可靠性取两级：

1. **上一份 terms_revision 快照**（首选）—— 软件内每次改术语、外部改动后重载
   （`Project.reload_glossary`）都会落快照，所以相邻快照就是「改之前 → 改之后」；
2. **terms 缓存表**（兜底）—— 历史里没有"改之前"的快照时（例如术语表是外部
   手写/外部改过但从未在软件内保存过），用上次在软件内写入的候选作为旧状态。
   这样即便用户已经在 Excel 里改完、历史不完整，也能立刻比出受影响的段落。

两级都拿不到旧状态时返回空列表，由 UI 明确告知原因，而不是静默什么都不显示。
"""
from __future__ import annotations

import json

SOURCE_REVISION = "revision"       # 用上一份快照对比
SOURCE_CACHE = "cache"             # 退回 terms 缓存表对比
SOURCE_NONE = "none"               # 没有可用的旧状态


class TermImpactService:
    def __init__(self, project):
        self.project = project
        self._undo: list[list[tuple[int, str, str]]] = []

    # ---------- 旧状态来源 ----------
    def _old_from_revision(self) -> dict[str, list[str]] | None:
        """上一份快照里的候选；不足两份快照时返回 None。"""
        revs = self.project.db.list_terms_revisions(2)
        if len(revs) < 2:
            return None
        try:
            payload = json.loads(revs[1]["payload"])
        except Exception:  # noqa: BLE001 快照损坏 → 交给兜底来源
            return None
        return {e["src"]: e["candidates"] for e in payload if e.get("src")}

    def _old_from_cache(self) -> dict[str, list[str]]:
        """terms 缓存表里的候选（= 上次在软件内保存术语时的状态）。"""
        out: dict[str, list[str]] = {}
        for t in self.project.db.list_terms():
            cands = [c for c in (t["tgt_candidates"] or "").split("|") if c]
            if t["src_term"] and cands:
                out[t["src_term"]] = cands
        return out

    def old_state(self) -> tuple[dict[str, list[str]], str]:
        """返回 (旧候选集合, 来源)。`history_source()` 是它的轻量版。"""
        old = self._old_from_revision()
        if old is not None:
            return old, SOURCE_REVISION
        cache = self._old_from_cache()
        if cache:
            return cache, SOURCE_CACHE
        return {}, SOURCE_NONE

    def history_source(self) -> str:
        """对比依据：revision / cache / none（UI 用来解释结果）。"""
        return self.old_state()[1]

    # ---------- 分析 ----------
    def analyze(self) -> list[dict]:
        db = self.project.db
        cur = {t.src: t.candidates for t in self.project.glossary.entries}
        old, _source = self.old_state()

        changed: list[tuple[str, list[str]]] = []
        for src, old_cands in old.items():
            now = cur.get(src)
            if now != old_cands:  # 修改或删除
                changed.append((src, old_cands))

        affected: dict[tuple[int, str], dict] = {}
        for src, old_cands in changed:
            new_first = (cur.get(src) or [""])[0]
            for old_c in old_cands:
                for seg in db.list_segments(search=old_c, translatable=True):
                    tgt = seg["tgt_text"] or ""
                    if old_c in tgt:
                        key = (seg["id"], old_c)
                        affected[key] = {
                            "seg_id": seg["id"], "doc_id": seg["doc_id"],
                            "term": src, "old": old_c, "new": new_first,
                        }
        return list(affected.values())

    def replace_all(self, affected: list[dict]) -> int:
        """把受影响段落中的旧候选替换为新首选候选（记录撤销栈）。"""
        db = self.project.db
        undo: list[tuple[int, str, str]] = []
        n = 0
        for item in affected:
            seg = db.get_segment(item["seg_id"])
            if seg is None or not seg["tgt_text"]:
                continue
            old_tgt, old = seg["tgt_text"], item["old"]
            new_tgt = old_tgt.replace(old, item["new"]) if item["new"] else old_tgt
            if new_tgt != old_tgt:
                undo.append((seg["id"], old_tgt, seg["status"]))
                db.update_segment(seg["id"], tgt=new_tgt, status="human_edited")
                n += 1
        if undo:
            self._undo.append(undo)
        return n

    def mark_retranslate(self, affected: list[dict]) -> int:
        seen = {item["seg_id"] for item in affected}
        for sid in seen:
            self.project.db.update_segment(sid, status="pending")
        return len(seen)

    def undo(self) -> int:
        if not self._undo:
            return 0
        undo = self._undo.pop()
        for seg_id, tgt, status in undo:
            self.project.db.update_segment(seg_id, tgt=tgt, status=status)
        return len(undo)

    def can_undo(self) -> bool:
        """是否还有可撤销的替换（UI 据此决定「撤销」按钮是否可用）。"""
        return bool(self._undo)
