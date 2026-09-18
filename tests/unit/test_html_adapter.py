from pathlib import Path

from adapters import get_adapter

HTML_SAMPLE = """<!DOCTYPE html>
<html><head><title>测试页</title><meta name="description" content="页面描述"></head>
<body>
<h1 class="title">第一章</h1>
<p id="p1">林凡握着<strong>灵石</strong>，念出 <code>abracadabra</code> 咒语。</p>
<ul><li>第一项</li><li>第二项 <img src="x.png" alt="示意图"></li></ul>
<table><tr><td>单元格一</td><td>单元格二</td></tr></table>
<a href="https://example.com">链接文字</a>
</body></html>
"""


def test_html_parse_blocks_and_protection(tmp_path):
    src = tmp_path / "a.html"
    src.write_text(HTML_SAMPLE, encoding="utf-8")
    model = get_adapter("html").parse(src)
    texts = [b.text for b in model.blocks if b.translatable]
    joined = " ".join(texts)
    assert "测试页" in joined                                # <title> 成段
    assert "abracadabra" not in joined           # 行内代码被占位符保护
    assert "https://example.com" not in joined   # 链接整体保护
    assert "示意图" in texts or any("示意图" in t for t in texts)  # alt 属性成段
    assert any("<strong>" in t for t in texts)   # 行内标记保留
    assert any("单元格一" in t for t in texts)
    # h1 标记
    h1 = next(b for b in model.blocks if "第一章" in b.text)
    assert h1.is_heading


def test_html_render_target(tmp_path):
    src = tmp_path / "a.html"
    src.write_text(HTML_SAMPLE, encoding="utf-8")
    ad = get_adapter("html")
    model = ad.parse(src)
    translations = {b.seq: f"T:{b.text}" for b in model.blocks if b.translatable}
    out = tmp_path / "out.html"
    ad.render(out, model, translations, "target")
    text = out.read_text(encoding="utf-8")
    assert 'class="title"' in text or "class=title" in text.replace("'", '"')  # 属性保留
    assert "T:第一章" in text
    assert "<code>abracadabra</code>" in text        # 行内代码还原
    assert 'src="x.png"' in text                     # 图片保留
    assert "T:示意图" in text                        # alt 已译
    assert "<table>" in text and "单元格一" not in text.replace("T:单元格一", "")
    assert "<a href=" in text                        # 链接节点保留（整体未译，边界已知）


def test_html_render_bilingual(tmp_path):
    src = tmp_path / "a.html"
    src.write_text("<html><body><p>第一段。</p><p>第二段。</p></body></html>",
                   encoding="utf-8")
    ad = get_adapter("html")
    model = ad.parse(src)
    translations = {b.seq: f"T:{b.text}" for b in model.blocks if b.translatable}
    out = tmp_path / "bi.html"
    ad.render(out, model, translations, "bi_inter")
    text = out.read_text(encoding="utf-8")
    assert 'class="bilingual-src"' in text and "第一段。" in text and "T:第一段。" in text

    out2 = tmp_path / "bit.html"
    ad.render(out2, model, translations, "bi_table")
    t2 = out2.read_text(encoding="utf-8")
    assert "<table" in t2 and "原文" in t2 and "译文" in t2
