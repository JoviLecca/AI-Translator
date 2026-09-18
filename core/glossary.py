"""术语表（设计 §5 / §7.4）：根目录 glossary.* 为权威数据，软件读写 + 外部变更检测。

- CSV：三列（源语言、目标语、注释），UTF-8 带 BOM，Excel 可直接编辑；
- 多候选用 | 分隔（≤3）；
- 有效指纹只含「源词 + 候选序列」——改注释不触发缓存失效（设计 v0.4 #24）。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path

CSV_HEADER = ["源语言", "目标语", "注释"]


@dataclass
class Term:
    src: str
    candidates: list[str]
    note: str = ""


def parse_candidates(raw: str) -> list[str]:
    return [c.strip() for c in (raw or "").split("|") if c.strip()][:3]


class Glossary:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path: Path | None = None
        self.kind: str | None = None
        self.entries: list[Term] = []
        self._state: tuple | None = None
        csv_p, txt_p = self.root / "glossary.csv", self.root / "glossary.txt"
        if csv_p.exists():
            self.path, self.kind = csv_p, "csv"
        elif txt_p.exists():
            self.path, self.kind = txt_p, "txt"

    # ---------- 读写 ----------
    def load(self) -> None:
        self.entries = []
        if self.path and self.path.exists():
            text = self.path.read_text(encoding="utf-8-sig", errors="replace")
            if self.kind == "csv":
                self._parse_csv(text)
            else:
                self._parse_txt(text)
        self._state = self._file_state()

    def _parse_csv(self, text: str) -> None:
        reader = csv.reader(io.StringIO(text))
        for i, row in enumerate(reader):
            if not row or not any(cell.strip() for cell in row):
                continue
            if i == 0 and row[0].strip() in CSV_HEADER:
                continue
            if len(row) < 2:
                continue
            src = row[0].strip()
            cands = parse_candidates(row[1])
            note = row[2].strip() if len(row) > 2 else ""
            if src and cands:
                self.entries.append(Term(src, cands, note))

    def _parse_txt(self, text: str) -> None:
        for line in text.splitlines():
            parts = [p for p in line.split("\t")]
            if len(parts) < 2 or not parts[0].strip():
                continue
            cands = parse_candidates(parts[1])
            if cands:
                note = parts[2].strip() if len(parts) > 2 else ""
                self.entries.append(Term(parts[0].strip(), cands, note))

    def save(self) -> None:
        if self.path is None:
            self.path, self.kind = self.root / "glossary.csv", "csv"
        buf = io.StringIO()
        if self.kind == "csv":
            writer = csv.writer(buf, lineterminator="\n")
            writer.writerow(CSV_HEADER)
            for t in self.entries:
                writer.writerow([t.src, "|".join(t.candidates), t.note])
            data = buf.getvalue()
        else:
            lines = ["\t".join([t.src, "|".join(t.candidates), t.note]) for t in self.entries]
            data = "\n".join(lines) + ("\n" if lines else "")
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(data, encoding="utf-8-sig" if self.kind == "csv" else "utf-8")
        tmp.replace(self.path)  # 原子写入（设计 §12）
        self._state = self._file_state()

    # ---------- 变更检测 ----------
    def _file_state(self) -> tuple | None:
        if not self.path or not self.path.exists():
            return None
        stat = self.path.stat()
        return (stat.st_mtime_ns, stat.st_size)

    def external_changed(self) -> bool:
        """用户在外部（如 Excel）手改术语表后的检测（设计 §12）。"""
        return self._file_state() != self._state

    # ---------- 增删改 ----------
    def get(self, src: str) -> Term | None:
        src = src.strip()
        return next((t for t in self.entries if t.src == src), None)

    def add(self, src: str, candidates: list[str], note: str = "") -> None:
        src = src.strip()
        cands = parse_candidates("|".join(candidates))
        if not src or not cands:
            raise ValueError("术语与候选不能为空")
        existed = self.get(src)
        if existed:
            existed.candidates, existed.note = cands, note
        else:
            self.entries.append(Term(src, cands, note))

    def remove(self, src: str) -> None:
        self.entries = [t for t in self.entries if t.src != src.strip()]

    def import_merge(self, other_path: Path) -> int:
        """合并外部术语表文件（csv/txt 自动嗅探），返回合并条数。"""
        g = Glossary(other_path.parent)
        g.path, g.kind = other_path, ("csv" if other_path.suffix.lower() == ".csv" else "txt")
        g.load()
        merged = 0
        for t in g.entries:
            if self.get(t.src) is None:
                self.entries.append(t)
                merged += 1
        return merged

    # ---------- 指纹 ----------
    def effective_hash(self) -> str:
        """有效术语集合指纹（源词+候选），用于 cfg_hash（设计 §6.1）。"""
        lines = sorted(f"{t.src}\x1f{'|'.join(t.candidates)}" for t in self.entries)
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()

    def to_payload(self) -> list[dict]:
        return [{"src": t.src, "candidates": t.candidates, "note": t.note} for t in self.entries]

    def snapshot_payload(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False)
