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


def test_import_service_passes_src_lang_to_ocr(tmp_path):
    """回归：真实导入链路必须把项目源语言传给 OCR 适配器。

    原实现 `ImportService.import_file` 只传 {"ruby_loose": ...}，适配器拿不到
    src_lang（缺省 "zh-CN"），日文/韩文项目的图片会用中英模型识别（用户反馈：
    导入日文图片识别结果错误）。上面的 test_ocr_backend_receives_lang 直接构造
    适配器并手动传参，覆盖不到调用点，所以这里补一条走 ImportService 的测试。
    """
    import adapters
    from adapters.ocr_adapter import set_default_backend
    from core.pipeline import ImportService
    from core.project import Project

    received: list[str] = []

    def lang_backend(path: str, lang: str) -> list[str]:
        received.append(lang)
        return ["一行日文"]

    img = tmp_path / "page1.png"
    img.write_bytes(b"\x89PNG fake")

    project = Project.create(tmp_path / "proj", name="ocr", src_lang="ja-JP",
                             tgt_lang="zh-CN")
    saved = dict(adapters._ADAPTERS)
    try:
        adapters._ADAPTERS.clear()          # 让 get_adapter("img") 重建适配器
        set_default_backend(lang_backend)
        ImportService(project).import_file(img)
    finally:
        adapters._ADAPTERS.clear()
        adapters._ADAPTERS.update(saved)
        set_default_backend(None)           # 恢复自动探测
        project.close()

    assert received == ["ja-JP"], received


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
