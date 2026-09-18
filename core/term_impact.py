"""术语变更影响分析（设计 §7.4 / v0.4 #25）。

基于 terms_revision 相邻快照 diff：术语修改/删除后，反查仍在使用旧候选的已译段落，
提供「全局替换为新候选」或「标记重译」；无历史快照时降级为全量扫描当前术语的旧候选。
"""
from __future__ import annotations

import json


class TermImpactService:
    def __init__(self, project):
        self.project = project
        self._undo: list[list[tuple[int, str, str]]] = []

    def analyze(self) -> list[dict]:
        db = self.project.db
        cur = {t.src: t.candidates for t in self.project.glossary.entries}
        revs = db.list_terms_revisions(2)
        old: dict[str, list[str]] = {}
        if len(revs) >= 2:
            try:
                payload = json.loads(revs[1]["payload"])
                old = {e["src"]: e["candidates"] for e in payload}
            except Exception:
                old = {}
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
