"""格式适配器注册表（设计 §7.1：可插拔，新增格式只加文件不改核心）。"""
from __future__ import annotations

from pathlib import Path

from adapters.base import FormatError, IFormatAdapter

_FORMAT_BY_EXT = {
    ".txt": "txt", ".md": "md", ".markdown": "md",
    ".html": "html", ".htm": "html",
    ".docx": "docx",
    ".png": "img", ".jpg": "img", ".jpeg": "img", ".bmp": "img", ".webp": "img",
}

_ADAPTERS: dict[str, IFormatAdapter] = {}


def detect_format(path: Path) -> str:
    ext = path.suffix.lower()
    fmt = _FORMAT_BY_EXT.get(ext)
    if not fmt:
        raise FormatError(f"不支持的文件格式：{path.suffix or '(无扩展名)'}，"
                          f"请先转为 txt / md / html / docx")
    return fmt


def get_adapter(fmt: str) -> IFormatAdapter:
    if fmt in _ADAPTERS:
        return _ADAPTERS[fmt]
    if fmt == "txt":
        from adapters.txt_adapter import TxtAdapter
        adapter: IFormatAdapter = TxtAdapter()
    elif fmt == "md":
        from adapters.md_adapter import MdAdapter
        adapter = MdAdapter()
    elif fmt == "html":
        from adapters.html_adapter import HtmlAdapter
        adapter = HtmlAdapter()
    elif fmt == "docx":
        from adapters.docx_adapter import DocxAdapter
        adapter = DocxAdapter()
    elif fmt == "img":
        from adapters.ocr_adapter import OcrAdapter
        adapter = OcrAdapter()
    else:
        raise FormatError(f"未实现的格式：{fmt}")
    _ADAPTERS[fmt] = adapter
    return adapter
