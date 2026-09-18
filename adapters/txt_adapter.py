"""txt 适配器：每行一段，空行透传（设计 §7.1）。EOL 归一化；振假名基词内联 + 注音槽。"""
from __future__ import annotations

from pathlib import Path

from adapters.base import Block, DocumentModel, FormatError, effectively_empty, read_text
from adapters.ruby import restore_ruby, rubyize_text
from core.segmentation import is_mostly_latin, join_parts, split_long


def normalize_eol(text: str) -> tuple[str, str]:
    """返回（归一化后的 \\n 文本, 原始 EOL）。"""
    if "\r\n" in text:
        return text.replace("\r\n", "\n"), "\r\n"
    if "\r" in text:
        return text.replace("\r", "\n"), "\n"
    return text, "\n"


class TxtAdapter:
    name = "txt"

    def parse(self, path: Path, opts: dict | None = None) -> DocumentModel:
        loose = bool((opts or {}).get("ruby_loose"))
        raw = read_text(path)
        if "\x00" in raw:
            raise FormatError("疑似二进制文件")
        text, eol = normalize_eol(raw)
        lines = text.split("\n")
        had_trailing_nl = text.endswith("\n")
        if had_trailing_nl:
            lines = lines[:-1]
        blocks: list[Block] = []
        seq = 0
        para = 0
        for line in lines:
            if effectively_empty(line):
                blocks.append(Block(seq=seq, text=line, translatable=False,
                                    meta={"kind": "filler"}))
                seq += 1
                continue
            flat, ruby = rubyize_text(line, loose=loose)
            for i, part in enumerate(split_long(flat)):
                blocks.append(Block(seq=seq, text=part,
                                    meta={"para": para, "part": i, "kind": "text",
                                          "ruby": ruby}))
                seq += 1
            para += 1
        model = DocumentModel(path=path, fmt="txt", blocks=blocks)
        model.skeleton = {"had_trailing_nl": had_trailing_nl, "eol": eol}
        return model

    def render(self, out_path: Path, model: DocumentModel,
               translations: dict[int, str], mode: str = "target",
               ruby_maps: dict[int, dict[str, str]] | None = None) -> None:
        ruby_maps = ruby_maps or {}
        eol = model.skeleton.get("eol", "\n") if model.skeleton else "\n"
        out_lines: list[str] = []
        i = 0
        blocks = model.blocks
        while i < len(blocks):
            b = blocks[i]
            if not b.translatable:
                out_lines.append(b.text)
                i += 1
                continue
            group = [bb for bb in blocks[i:] if bb.meta.get("para") == b.meta["para"]]
            latin = is_mostly_latin(b.text)
            tgts = [translations.get(bb.seq, bb.text) for bb in group]
            tgt_final = join_parts(tgts, latin)
            rt_map = {}
            for bb in group:
                rt_map.update(ruby_maps.get(bb.seq) or {})
            tgt_final = restore_ruby(tgt_final, b.meta.get("ruby"), rt_map)
            if mode == "target":
                out_lines.append(tgt_final)
            elif mode == "bi_inter":
                src_final = join_parts([bb.text for bb in group], latin)
                src_final = restore_ruby(src_final, b.meta.get("ruby"))
                out_lines.append(src_final)
                out_lines.append(tgt_final)
                out_lines.append("")
            else:
                raise FormatError("txt 仅支持 target / bi_inter 模式")
            i += len(group)
        text = eol.join(out_lines)
        if model.skeleton and model.skeleton.get("had_trailing_nl") and text:
            text += eol
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(text.encode("utf-8"))
