"""术语变更影响分析（设计 §7.4 / v0.4 #25）。

术语**新增**、修改或删除后，反查需要订正的已译段落，提供「全局替换为新候选」或
「标记重译」，替换可撤销。两类影响分别标记：

- `kind="old"`：译文里还留着**旧译名**（改了候选/删了术语）→ 可机械替换为新首选候选；
- `kind="missing"`：源文出现了术语，译文里却一个**当前候选**都没有
  （新加的术语、或改了译名还没订正）→ 没有可机械替换的旧串，只能人工改或重译。

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

# 只在这些状态里找「需要订正」的段落：还没译的段不用订正（下次翻译自然用新术语）
TRANSLATED_STATUSES = ("machine_translated", "human_edited", "confirmed")


def segment_term_issues(src: str, tgt: str, entries, old_state: dict | None = None,
                        limit: int = 3) -> list[dict]:
    """**单个段落**的术语问题（校对页段落内提示用；反馈 2026-09-29 #6）。

    与批量分析（`TermImpactService.analyze`）同一套判断，但纯内存、不查库 ——
    段落内提示每次换段都要算，必须足够便宜。

    返回 `[{kind, term, old, new}]`，kind 见模块 docstring。`limit` 防止一段里
    术语太多把提示条撑爆。
    """
    src = src or ""
    tgt = tgt or ""
    issues: list[dict] = []
    for t in entries or []:
        src_term = getattr(t, "src", "") or ""
        cands = [c for c in (getattr(t, "candidates", None) or []) if c]
        if not src_term:
            continue
        # ① 旧译名还留在译文里（术语改过名/换过候选）
        olds = [c for c in (old_state or {}).get(src_term, []) if c and c not in cands]
        hit_old = next((c for c in olds if c in tgt), "")
        if hit_old:
            issues.append({"kind": "old", "term": src_term, "old": hit_old,
                           "new": cands[0] if cands else ""})
        # ② 源文有术语、译文里没有任何当前候选（新增术语 / 改了译名还没订正）
        elif cands and src_term in src and not any(c in tgt for c in cands):
            issues.append({"kind": "missing", "term": src_term, "old": "",
                           "new": cands[0]})
        if len(issues) >= limit:
            break
    return issues


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
        """找出需要订正的段落。

        只处理**这次动过的术语**（快照/缓存与当前 glossary 不一致的那些），
        否则每个术语都要对分段表做一次 LIKE 全扫，大项目上会明显卡顿。
        """
        db = self.project.db
        cur = {t.src: t.candidates for t in self.project.glossary.entries}
        old, _source = self.old_state()
        affected: dict[tuple[int, str], dict] = {}

        # ① 旧候选仍留在译文里（改了候选 / 删了术语）
        for src, old_cands in old.items():
            if cur.get(src) == old_cands:
                continue
            new_first = (cur.get(src) or [""])[0]
            for old_c in old_cands:
                for seg in db.list_segments(search=old_c, translatable=True):
                    tgt = seg["tgt_text"] or ""
                    if old_c and old_c in tgt:
                        affected[(seg["id"], src)] = {
                            "seg_id": seg["id"], "doc_id": seg["doc_id"], "kind": "old",
                            "term": src, "old": old_c, "new": new_first,
                        }

        # ② 新增/改过的术语：源文里有，译文里却没有任何当前候选
        for src, cands in cur.items():
            if not cands or old.get(src) == cands:
                continue
            for seg in db.list_segments(search=src, translatable=True):
                key = (seg["id"], src)
                if key in affected:
                    continue
                tgt = seg["tgt_text"] or ""
                if (seg["status"] not in TRANSLATED_STATUSES or not tgt.strip()
                        or src not in (seg["src_text"] or "")):
                    continue
                if any(c in tgt for c in cands):
                    continue
                affected[key] = {
                    "seg_id": seg["id"], "doc_id": seg["doc_id"], "kind": "missing",
                    "term": src, "old": "", "new": cands[0],
                }
        return list(affected.values())

    def replace_all(self, affected: list[dict]) -> int:
        """把受影响段落中的旧候选替换为新首选候选（记录撤销栈）。

        只处理 `kind="old"`（有旧串可换）的条目；`kind="missing"` 没有可机械替换的
        旧串 —— 硬替换会变成"到处插入新词"，交给「标记重译」或人工。
        """
        db = self.project.db
        undo: list[tuple[int, str, str]] = []
        n = 0
        for item in affected:
            old, new = item.get("old") or "", item.get("new") or ""
            if not old or not new:
                continue
            seg = db.get_segment(item["seg_id"])
            if seg is None or not seg["tgt_text"]:
                continue
            old_tgt = seg["tgt_text"]
            new_tgt = old_tgt.replace(old, new)
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
