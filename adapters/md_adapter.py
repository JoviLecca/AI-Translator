"""markdown 适配器（设计 §7.1）。

- 以 markdown-it 令牌的行范围切出块级 span（外层优先：引用/列表项整体成段，内部不再细切）；
- YAML front matter、围栏代码块、html 块、分割线不翻译，原样透传；
- 行内代码 / 数学公式 / 图片整体 / 链接 URL → 占位符保护；
- 双语模式：bi_inter 段间交错、bi_table 左右表格。
"""
from __future__ import annotations

import re
from pathlib import Path

from markdown_it import MarkdownIt

from adapters.base import (
    Block, DocumentModel, FormatError, ProtectMap, effectively_empty, read_text,
)
from adapters.ruby import restore_ruby, rubyize_text
from adapters.txt_adapter import normalize_eol
from core.segmentation import is_mostly_latin, join_parts, split_long

_BLOCK_TYPES = {
    "paragraph_open", "heading_open", "fence", "code_block", "table_open",
    "html_block", "hr", "blockquote_open", "list_item_open",
}
_PASSTHROUGH = {"fence", "code_block", "html_block", "hr", "frontmatter"}
_PROTECT_PATTERS = [
    r"`[^`\n]+`",                  # 行内代码
    r"\$[^$\n]+\$",                # 数学公式
    r"!\[[^\]\n]*\]\([^)\n]*\)",   # 图片整体
    r"(?<=\]\()[^)\s]+(?=\))",     # 链接 URL（链接文字仍翻译）
]


class MdAdapter:
    name = "md"

    def parse(self, path: Path, opts: dict | None = None) -> DocumentModel:
        loose = bool((opts or {}).get("ruby_loose"))
        text, eol = normalize_eol(read_text(path))
        lines = text.split("\n")
        md = MarkdownIt("commonmark").enable("table")
        tokens = md.parse(text)

        accepted: list[tuple[int, int, str]] = []
        # YAML front matter（设计 v0.5：不翻译）
        if lines and lines[0].strip() == "---":
            for idx in range(1, len(lines)):
                if lines[idx].strip() in ("---", "..."):
                    accepted.append((0, idx + 1, "frontmatter"))
                    break

        for tok in tokens:
            if tok.map is None or tok.type not in _BLOCK_TYPES:
                continue
            s, e = tok.map
            if any(s >= as_ and e <= ae for as_, ae, _ in accepted):
                continue
            accepted.append((s, e, tok.type))
        accepted.sort(key=lambda x: (x[0], -(x[1] - x[0])))

        blocks: list[Block] = []
        chunks: list[dict] = []
        seq = 0
        cursor = 0
        for span_idx, (s, e, ttype) in enumerate(accepted):
            if s > cursor:
                blocks.append(Block(seq=seq, text="\n".join(lines[cursor:s]),
                                    translatable=False, meta={"kind": "gap"}))
                chunks.append({"seq": seq, "s": cursor, "e": s, "kind": "gap"})
                seq += 1
            span_text = "\n".join(lines[s:e])
            kind = "frontmatter" if ttype == "frontmatter" else ttype.removesuffix("_open")
            if ttype in _PASSTHROUGH or ttype == "frontmatter":
                blocks.append(Block(seq=seq, text=span_text, translatable=False,
                                    meta={"kind": kind, "span": (s, e)}))
                chunks.append({"seq": seq, "s": s, "e": e, "kind": "gap"})
                seq += 1
            else:
                pm = ProtectMap()
                flat = pm.protect(span_text, _PROTECT_PATTERS)
                flat, ruby = rubyize_text(flat, loose=loose)
                if effectively_empty(flat):
                    # 仅占位符/空白构成的段（如纯图片段落）→ 透传，不进翻译队列（反馈 #2/#3）
                    blocks.append(Block(seq=seq, text=span_text, translatable=False,
                                        meta={"kind": "empty", "span": (s, e)}))
                    chunks.append({"seq": seq, "s": s, "e": e, "kind": "gap"})
                    seq += 1
                    cursor = e
                    continue
                parts = split_long(flat)
                for i, part in enumerate(parts):
                    blocks.append(Block(
                        seq=seq, text=part,
                        is_heading=(ttype == "heading_open"),
                        meta={"kind": kind, "para": span_idx, "part": i,
                              "ph": pm.map, "ruby": ruby, "span": (s, e)},
                    ))
                    if i == 0:
                        chunks.append({"seq": seq, "s": s, "e": e, "kind": "block"})
                    seq += 1
            cursor = e
        if cursor < len(lines):
            blocks.append(Block(seq=seq, text="\n".join(lines[cursor:]),
                                translatable=False, meta={"kind": "gap"}))
            chunks.append({"seq": seq, "s": cursor, "e": len(lines), "kind": "gap"})

        model = DocumentModel(path=path, fmt="md", blocks=blocks)
        model.skeleton = {"lines": lines, "chunks": chunks, "eol": eol}
        return model

    def _final(self, group: list[Block], translations: dict[int, str]) -> tuple[str, str]:
        latin = is_mostly_latin(group[0].text)
        src_final = join_parts([b.text for b in group], latin)
        tgt_final = join_parts([translations.get(b.seq, b.text) for b in group], latin)
        return src_final, tgt_final

    def _restore(self, block: Block, text: str) -> str:
        pm = ProtectMap()
        pm.map = dict(block.meta.get("ph") or {})
        return pm.restore(text)

    def render(self, out_path: Path, model: DocumentModel,
               translations: dict[int, str], mode: str = "target",
               ruby_maps: dict[int, dict[str, str]] | None = None) -> None:
        ruby_maps = ruby_maps or {}
        skel = model.skeleton
        lines: list[str] = skel["lines"]
        chunks: list[dict] = skel["chunks"]
        eol: str = skel.get("eol", "\n")
        by_seq = {b.seq: b for b in model.blocks}
        parts_out: list[str] = []
        table_rows: list[tuple[str, str]] = []

        for ch in chunks:
            s, e = ch["s"], ch["e"]
            tail = "\n" if e < len(lines) else ""
            if ch["kind"] == "gap":
                parts_out.append("\n".join(lines[s:e]) + tail)
                continue
            first = by_seq[ch["seq"]]
            group = [b for b in model.blocks
                     if b.meta.get("para") == first.meta["para"]
                     and b.meta.get("span") == first.meta["span"]]
            rt_map = {}
            for b in group:
                rt_map.update(ruby_maps.get(b.seq) or {})
            src_final, tgt_final = self._final(group, translations)
            src_final = self._restore(first, src_final)
            src_final = restore_ruby(src_final, first.meta.get("ruby"))
            tgt_final = self._restore(first, tgt_final)
            tgt_final = restore_ruby(tgt_final, first.meta.get("ruby"), rt_map)
            if mode == "target":
                parts_out.append(tgt_final + tail)
            elif mode == "bi_inter":
                parts_out.append(src_final + "\n\n" + tgt_final + tail)
            elif mode == "bi_table":
                table_rows.append((src_final, tgt_final))
            else:
                raise FormatError(f"未知导出模式：{mode}")

        if mode == "bi_table":
            esc = lambda t: t.replace("|", "\\|").replace("\n", "<br>")
            rows = ["| 原文 | 译文 |", "|---|---|"]
            rows += [f"| {esc(s_)} | {esc(t)} |" for s_, t in table_rows]
            text = "\n".join(rows) + "\n"
        else:
            text = "".join(parts_out)
            if text and not text.endswith("\n"):
                text += "\n"
            if eol == "\r\n":
                text = text.replace("\n", "\r\n")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(text.encode("utf-8"))
