from pathlib import Path

import pytest

from adapters.base import FormatError
from adapters.ocr_adapter import OcrAdapter


def test_ocr_parse_and_render_with_injected_backend(tmp_path):
    img = tmp_path / "page1.png"
    img.write_bytes(b"\x89PNG fake")
    ad = OcrAdapter(backend=lambda p: ["第一行文字。", "第二行：灵石三块。", ""])
    model = ad.parse(img)
    translatable = [b for b in model.blocks if b.translatable]
    assert len(translatable) == 2
    translations = {b.seq: f"T:{b.text}" for b in translatable}
    out = tmp_path / "page1.en.md"  # 图片导出固定 .md
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
    ad = OcrAdapter(backend=lambda p: ["行"])
    model = ad.parse(img)
    out = tmp_path / "p.en.txt"
    ad.render(out, model, {b.seq: "T" for b in model.blocks}, "target")
    assert out.read_text(encoding="utf-8") == "T\n"


def test_ocr_default_backend_missing_paddle(tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(b"x")
    try:
        import paddleocr  # noqa: F401
        pytest.skip("本机装有 PaddleOCR，跳过缺库分支")
    except ImportError:
        pass
    with pytest.raises(FormatError) as ei:
        OcrAdapter().parse(img)
    assert "PaddleOCR" in str(ei.value)
