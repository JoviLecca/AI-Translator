"""图片 OCR 适配器（设计 §7.1 / §11 M3）：提取文字翻译，导出为 .md（不做原图还原）。

后端可注入（backend: img_path -> list[str]），默认 PaddleOCR（未安装时给出明确指引）。
"""
from __future__ import annotations

from pathlib import Path

from adapters.base import Block, DocumentModel, FormatError
from core.segmentation import is_mostly_latin, join_parts, split_long

_OCR = None


def paddle_backend(img_path: str) -> list[str]:
    global _OCR
    try:
        from paddleocr import PaddleOCR
    except ImportError as e:
        raise FormatError("未安装 PaddleOCR，请先执行：pip install paddleocr paddlepaddle") from e
    if _OCR is None:
        _OCR = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    result = _OCR.ocr(img_path)
    lines: list[str] = []
    for page in result or []:
        for item in page or []:
            text = item[1][0] if isinstance(item[1], (list, tuple)) else str(item[1])
            if text and text.strip():
                lines.append(text.strip())
    return lines


class OcrAdapter:
    name = "img"

    def __init__(self, backend=None):
        self.backend = backend or paddle_backend

    def parse(self, path: Path, opts: dict | None = None) -> DocumentModel:
        lines = [ln for ln in self.backend(str(path)) if ln.strip()]
        blocks: list[Block] = []
        para = 0
        for line in lines:
            parts = split_long(line)
            for i, part in enumerate(parts):
                blocks.append(Block(seq=len(blocks), text=part,
                                    meta={"para": para, "part": i, "kind": "text"}))
            para += 1
        model = DocumentModel(path=path, fmt="img", blocks=blocks)
        model.skeleton = {"lines": lines}
        return model

    def render(self, out_path: Path, model: DocumentModel,
               translations: dict[int, str], mode: str = "target",
               ruby_maps: dict[int, dict[str, str]] | None = None) -> None:
        if out_path.suffix.lower() not in (".md", ".txt"):
            out_path = out_path.with_suffix(".md")
        lines_out: list[str] = []
        i = 0
        blocks = model.blocks
        while i < len(blocks):
            b = blocks[i]
            group = [bb for bb in blocks[i:] if bb.meta.get("para") == b.meta["para"]]
            latin = is_mostly_latin(b.text)
            tgts = [translations.get(bb.seq, bb.text) for bb in group]
            tgt_final = join_parts(tgts, latin)
            if mode == "target":
                lines_out.append(tgt_final)
            elif mode == "bi_inter":
                lines_out.append(b.text)
                lines_out.append(tgt_final)
                lines_out.append("")
            else:
                raise FormatError("图片导出仅支持 target / bi_inter 模式")
            i += len(group)
        text = "\n".join(lines_out) + ("\n" if lines_out else "")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
