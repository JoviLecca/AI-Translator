from pathlib import Path

from adapters import detect_format, get_adapter
from adapters.base import FormatError


def test_detect_format_rejects_unknown(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_text("x")
    try:
        detect_format(f)
        assert False, "应当抛 FormatError"
    except FormatError as e:
        assert "不支持" in str(e)


def test_txt_roundtrip(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("第一段。\n\n第二段。\n第三段。\n", encoding="utf-8")
    ad = get_adapter("txt")
    model = ad.parse(src)
    translatable = [b for b in model.blocks if b.translatable]
    assert len(translatable) == 3
    translations = {b.seq: f"T:{b.text}" for b in translatable}
    out = tmp_path / "out.txt"
    ad.render(out, model, translations, "target")
    text = out.read_text(encoding="utf-8")
    assert text == "T:第一段。\n\nT:第二段。\nT:第三段。\n"


MD_SAMPLE = """---
title: 测试小说
---

# 第一章 破庙

林凡握着一块**灵石**，只觉丹田一热。

- 第一项 `code123` 内容
- 第二项 [链接文字](https://example.com/page)

| 名称 | 数量 |
|---|---|
| 灵石 | 三块 |

> 有人低声说：他回来了。

```python
print("do not translate")
```
"""


def test_md_parse_blocks_and_protection(tmp_path):
    src = tmp_path / "ch.md"
    src.write_text(MD_SAMPLE, encoding="utf-8")
    model = get_adapter("md").parse(src)
    by_kind = {}
    for b in model.blocks:
        by_kind.setdefault(b.meta.get("kind"), []).append(b)
    assert by_kind["frontmatter"] and not by_kind["frontmatter"][0].translatable
    heading = by_kind["heading"][0]
    assert heading.is_heading and "第一章" in heading.text
    # 行内代码被占位符保护
    texts = " ".join(b.text for b in model.blocks if b.translatable)
    assert "code123" not in texts and "{0}" in texts or "{" in texts
    # 链接 URL 被保护、链接文字保留
    link_block = next(b for b in model.blocks if "链接文字" in b.text)
    assert "https://example.com" not in link_block.text
    # 代码块透传
    fence = next(b for b in model.blocks if "print" in b.text)
    assert not fence.translatable
    # 引用块成段
    assert any("有人低声说" in b.text for b in model.blocks if b.translatable)


def test_md_render_target_keeps_structure(tmp_path):
    src = tmp_path / "ch.md"
    src.write_text(MD_SAMPLE, encoding="utf-8")
    ad = get_adapter("md")
    model = ad.parse(src)
    translations = {b.seq: f"T:{b.text}" for b in model.blocks if b.translatable}
    out = tmp_path / "out.md"
    ad.render(out, model, translations, "target")
    text = out.read_text(encoding="utf-8")
    assert "T:# 第一章 破庙" in text
    assert "---\ntitle: 测试小说\n---" in text          # front matter 原样
    assert "print(\"do not translate\")" in text          # 代码块原样
    assert "[链接文字](https://example.com/page)" in text  # 链接还原
    assert "`code123`" in text                            # 行内代码还原
    assert text.count("|---|---|") == 1                   # 表格结构保留


def test_md_render_bilingual(tmp_path):
    src = tmp_path / "ch.md"
    src.write_text("第一段。\n\n第二段。\n", encoding="utf-8")
    ad = get_adapter("md")
    model = ad.parse(src)
    translations = {b.seq: f"T:{b.text}" for b in model.blocks if b.translatable}
    out = tmp_path / "bi.md"
    ad.render(out, model, translations, "bi_inter")
    text = out.read_text(encoding="utf-8")
    assert "第一段。" in text and "T:第一段。" in text

    out2 = tmp_path / "bi_table.md"
    ad.render(out2, model, translations, "bi_table")
    text2 = out2.read_text(encoding="utf-8")
    assert "| 原文 | 译文 |" in text2


def test_txt_encoding_gbk(tmp_path):
    src = tmp_path / "gbk.txt"
    src.write_text("中文内容测试。", encoding="gb18030")
    model = get_adapter("txt").parse(src)
    assert any("中文内容" in b.text for b in model.blocks if b.translatable)
