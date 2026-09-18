"""术语一致性报表（设计 §7.4 译后检查 / §11 M3）。

对每个术语：源文出现次数 vs 译文中候选（含大小写/英文复数变形）出现次数；
输出 0 命中的术语与「源文含术语但译文无候选」的可疑段落清单，可导出 CSV。
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path


def variants(word: str) -> list[str]:
    """大小写不敏感 + 英文复数宽松变形（含多词短语的最后一个词复数化）。"""
    w = word.strip().lower()
    if not w:
        return []
    out = [w]
    parts = w.split()
    last = parts[-1]
    if last.isalpha():
        prefix = " ".join(parts[:-1])
        plural = f"{prefix} {last}s" if prefix else f"{last}s"
        out.append(plural)
        if last.endswith(("s", "x", "ch", "sh")):
            out.append(f"{prefix} {last}es" if prefix else f"{last}es")
    return out


def count_occurrences(text: str, words: list[str]) -> int:
    """最长优先的正则交替匹配，避免单复数子串重复计数。"""
    if not words:
        return 0
    ordered = sorted({w for w in words if w}, key=len, reverse=True)
    pattern = "|".join(re.escape(w) for w in ordered)
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def term_consistency_report(project) -> list[dict]:
    segs = project.db.list_segments(translatable=True)
    rows = []
    for term in project.glossary.entries:
        src_hits = 0
        cand_hits = 0
        suspicious: list[int] = []
        for s in segs:
            src = s["src_text"] or ""
            tgt = (s["tgt_text"] or "").lower()
            if term.src in src:
                src_hits += 1
                found = any(v in tgt for c in term.candidates for v in variants(c))
                if not found and s["status"] in ("machine_translated", "human_edited", "confirmed"):
                    suspicious.append(s["id"])
            cand_hits += sum(count_occurrences(s["tgt_text"] or "", variants(c))
                             for c in term.candidates)
        status = "ok" if cand_hits > 0 or src_hits == 0 else "missing"
        rows.append({
            "src": term.src, "candidates": "|".join(term.candidates),
            "src_hits": src_hits, "candidate_hits": cand_hits,
            "status": status, "suspicious_segments": suspicious[:50],
        })
    rows.sort(key=lambda r: (r["status"] != "missing", -r["src_hits"]))
    return rows


def export_report_csv(rows: list[dict], path: Path) -> None:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["源词", "候选", "源文出现段数", "候选命中次数", "状态", "可疑段落ID"])
    for r in rows:
        writer.writerow([r["src"], r["candidates"], r["src_hits"], r["candidate_hits"],
                         r["status"], " ".join(map(str, r["suspicious_segments"]))])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(buf.getvalue(), encoding="utf-8-sig")
