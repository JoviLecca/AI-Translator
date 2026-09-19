"""OCR 适配器测试（S1 RapidOCR 替代版）。"""
from pathlib import Path

import pytest

from adapters.base import FormatError
from adapters.ocr_adapter import OcrAdapter, get_default_backend


def _backend(path: str, lang: str = "zh-CN") -> list[str]:
    return ["第一行文字。", "第二行：灵石三块。", ""]


def test_ocr_parse_and_render_with_injected_backend(tmp_path):
    img = tmp_path / "page1.png"
    img.write_bytes(b"\x89PNG fake")
    ad = OcrAdapter(backend=_backend)
    model = ad.parse(img)
    translatable = [b for b in model.blocks if b.translatable]
    assert len(translatable) == 2
    translations = {b.seq: f"T:{b.text}" for b in translatable}
    out = tmp_path / "page1.en.md"
    ad.render(out, model, translations, "target")
    text = out.read_text(encoding="utf-8")
    assert "T:第一行文字。" in text and "T:第二行：灵石三块。" in text

    out2 = tmp_path / "page1.bi.md"
    ad.render(out2, model, translations, "bi_inter")
    t2 = out2.read_text(encoding="utf-8")
    assert "第一行文字。" in t2 and "T:第一行文字。" in t2


def test_ocr_render_suffix_forced_md(tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(b"x")
    ad = OcrAdapter(backend=_backend)
    model = ad.parse(img)
    out = tmp_path / "p.en.txt"
    ad.render(out, model, {b.seq: "T" for b in model.blocks}, "target")
    assert out.read_text(encoding="utf-8") == "T\nT\n"


def test_ocr_default_backend_available():
    """RapidOCR 已安装 → 默认引擎可用。"""
    backend = get_default_backend()
    assert backend is not None, "RapidOCR 或 winsdk 应至少一个可用"


def test_ocr_backend_receives_lang(tmp_path):
    """backend 正确接收 src_lang 参数。"""
    received = []
    def lang_backend(path: str, lang: str) -> list[str]:
        received.append(lang)
        return ["文本"]
    ad = OcrAdapter(backend=lang_backend)
    img = tmp_path / "p.png"
    img.write_bytes(b"x")
    ad.parse(img, opts={"src_lang": "ja-JP"})
    assert received == ["ja-JP"]


def test_rapidocr_real_chinese(tmp_path):
    """RapidOCR 真跑：构造含中文的测试图并识别（S1 验收）。"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (400, 120), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 28)
    except OSError:
        font = ImageFont.load_default()
    draw.text((20, 20), "灵石三块", fill="black", font=font)
    draw.text((20, 60), "宗门大厅", fill="black", font=font)
    p = tmp_path / "zh.png"
    img.save(str(p))

    from adapters.ocr_adapter import rapidocr_backend
    lines = rapidocr_backend(str(p), "zh-CN")
    assert any("灵石" in line for line in lines), lines
    assert any("宗门" in line for line in lines), lines
