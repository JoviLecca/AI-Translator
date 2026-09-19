"""epub 适配器测试：容器解析、多文档 seq 连续性、XHTML 良构写回、条目保留。"""
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from adapters.base import FormatError
from adapters.epub_adapter import EpubAdapter

CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _opf(chapters: list[str]) -> str:
    items = "\n".join(
        f'    <item id="c{i}" href="{n}" media-type="application/xhtml+xml"/>'
        for i, n in enumerate(chapters))
    spine = "\n".join(f'    <itemref idref="c{i}"/>' for i in range(len(chapters)))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>テスト</dc:title><dc:language>ja</dc:language>
  </metadata>
  <manifest>
{items}
    <item id="pic" href="pic.png" media-type="image/png"/>
  </manifest>
  <spine>
{spine}
  </spine>
</package>
"""


def chapter_xhtml(title: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>{title}</title></head>
<body>{body}</body>
</html>
"""


def make_epub(path: Path, chapters: dict[str, str], *, with_opf: bool = True,
              extra: dict[str, bytes] | None = None) -> Path:
    """构造一个最小可用 EPUB（mimetype 必须是第一个且不压缩）。"""
    with zipfile.ZipFile(path, "w") as z:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        z.writestr(info, "application/epub+zip")
        if with_opf:
            z.writestr("META-INF/container.xml", CONTAINER)
            z.writestr("OEBPS/content.opf", _opf(list(chapters)))
        for name, text in chapters.items():
            z.writestr(f"OEBPS/{name}", text)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return path


def two_chapter_epub(tmp_path: Path) -> Path:
    return make_epub(tmp_path / "book.epub", {
        "ch1.xhtml": chapter_xhtml("第一章", "<h1>第一章</h1><p>日文の本文一。</p>"),
        "ch2.xhtml": chapter_xhtml("第二章", "<h1>第二章</h1><p>日文の本文二。</p>"),
    }, extra={"OEBPS/pic.png": b"\x89PNG\r\n\x1a\nFAKE"})


# ---------- 解析 ----------

def test_parse_collects_blocks_from_all_content_docs(tmp_path):
    ad = EpubAdapter()
    model = ad.parse(two_chapter_epub(tmp_path))

    texts = [b.text for b in model.blocks]
    assert "第一章" in texts and "日文の本文一。" in texts
    assert "第二章" in texts and "日文の本文二。" in texts
    # h1 标为标题（跨格式导出依赖）
    assert any(b.is_heading and b.text == "第一章" for b in model.blocks)
    # 非内容文档（图片）不产生段
    assert not any("pic.png" in (b.text or "") for b in model.blocks)


def test_seq_is_contiguous_across_documents_and_replayable(tmp_path):
    """多个内容文档共用同一 seq 空间，且 parse 可确定性重放（导出按 seq 对齐）。"""
    ep = two_chapter_epub(tmp_path)
    m1 = EpubAdapter().parse(ep)
    m2 = EpubAdapter().parse(ep)
    assert [b.seq for b in m1.blocks] == list(range(len(m1.blocks))), "seq 必须连续"
    assert [(b.seq, b.text) for b in m1.blocks] == [(b.seq, b.text) for b in m2.blocks]
    # 两个文档的段都在同一个序列里（否则导出会错位）
    assert len(m1.blocks) >= 4


def test_parse_rejects_non_zip_and_zip_without_xhtml(tmp_path):
    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"not a zip at all")
    with pytest.raises(FormatError, match="不是有效的 EPUB"):
        EpubAdapter().parse(bad)

    empty = tmp_path / "empty.epub"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "nothing here")
    with pytest.raises(FormatError, match="没有找到"):
        EpubAdapter().parse(empty)


def test_parse_falls_back_to_extension_scan_without_opf(tmp_path):
    """OPF/container 缺失时不报错，退回扫描 .xhtml。"""
    ep = make_epub(tmp_path / "noopf.epub",
                   {"ch1.xhtml": chapter_xhtml("章", "<p>本文。</p>")}, with_opf=False)
    model = EpubAdapter().parse(ep)
    assert any(b.text == "本文。" for b in model.blocks)


# ---------- 渲染 ----------

def test_render_writes_valid_epub_and_preserves_other_entries(tmp_path):
    ep = two_chapter_epub(tmp_path)
    ad = EpubAdapter()
    model = ad.parse(ep)
    translations = {b.seq: f"T:{b.text}" for b in model.blocks if b.translatable}
    out = tmp_path / "out.epub"
    ad.render(out, model, translations, "target")

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        # mimetype 必须是第一个条目且不压缩（EPUB 规范）
        first = z.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"
        # 其它条目原样保留
        assert z.read("OEBPS/pic.png") == b"\x89PNG\r\n\x1a\nFAKE"
        assert "META-INF/container.xml" in names and "OEBPS/content.opf" in names
        # 正文已翻译，且仍是良构 XHTML
        ch1 = z.read("OEBPS/ch1.xhtml").decode("utf-8")
    assert "T:日文の本文一。" in ch1
    root = etree.fromstring(ch1.encode("utf-8"))
    # XML 解析器会把默认命名空间解析进标签名 → 说明 xmlns 被正确保留、产物是良构 XHTML
    assert root.tag == "{http://www.w3.org/1999/xhtml}html"


def test_render_output_can_be_reparsed(tmp_path):
    """渲染产物必须还能被自己的适配器读回来（结构性自检）。"""
    ad = EpubAdapter()
    model = ad.parse(two_chapter_epub(tmp_path))
    translations = {b.seq: f"T:{b.text}" for b in model.blocks if b.translatable}
    out = tmp_path / "out.epub"
    ad.render(out, model, translations, "target")

    again = EpubAdapter().parse(out)
    texts = [b.text for b in again.blocks]
    assert "T:日文の本文一。" in texts
    assert [b.seq for b in again.blocks] == list(range(len(again.blocks)))


def test_render_keeps_xml_declaration_and_doctype(tmp_path):
    ad = EpubAdapter()
    model = ad.parse(two_chapter_epub(tmp_path))
    out = tmp_path / "out.epub"
    ad.render(out, model, {}, "target")
    with zipfile.ZipFile(out) as z:
        ch1 = z.read("OEBPS/ch1.xhtml").decode("utf-8")
    assert ch1.lstrip().startswith("<?xml"), ch1[:80]
    assert "<!DOCTYPE html>" in ch1


def test_render_without_translations_preserves_text(tmp_path):
    """没有任何译文时，正文文字不得丢失或改变（往返保真）。"""
    ad = EpubAdapter()
    model = ad.parse(two_chapter_epub(tmp_path))
    out = tmp_path / "out.epub"
    ad.render(out, model, {}, "target")
    with zipfile.ZipFile(out) as z:
        ch1 = z.read("OEBPS/ch1.xhtml").decode("utf-8")
        ch2 = z.read("OEBPS/ch2.xhtml").decode("utf-8")
    assert "日文の本文一。" in ch1 and "第一章" in ch1
    assert "日文の本文二。" in ch2 and "第二章" in ch2


def test_render_bi_inter_inserts_source_after_translation(tmp_path):
    ad = EpubAdapter()
    model = ad.parse(two_chapter_epub(tmp_path))
    translations = {b.seq: f"T:{b.text}" for b in model.blocks if b.translatable}
    out = tmp_path / "bi.epub"
    ad.render(out, model, translations, "bi_inter")
    with zipfile.ZipFile(out) as z:
        ch1 = z.read("OEBPS/ch1.xhtml").decode("utf-8")
    assert 'class="bilingual-src"' in ch1
    assert "日文の本文一。" in ch1 and "T:日文の本文一。" in ch1


def test_render_rejects_bi_table(tmp_path):
    ad = EpubAdapter()
    model = ad.parse(two_chapter_epub(tmp_path))
    with pytest.raises(FormatError, match="epub 仅支持 target / bi_inter"):
        ad.render(tmp_path / "x.epub", model, {}, "bi_table")


def test_ruby_in_content_doc_is_tokenized_and_restored(tmp_path):
    """内容文档里的 <ruby> 也要走振假名链路（与 html/md 一致）。"""
    ep = make_epub(tmp_path / "r.epub", {
        "ch1.xhtml": chapter_xhtml("章", "<p>彼は<ruby>魔法<rt>まほう</rt></ruby>を使った。</p>"),
    })
    ad = EpubAdapter()
    model = ad.parse(ep)
    para = next(b for b in model.blocks if b.translatable and "魔法" in b.text)
    assert "魔法{r0}" in para.text
    assert "まほう" not in para.text and "<rt>" not in para.text

    out = tmp_path / "r_out.epub"
    ad.render(out, model, {para.seq: "他使用了魔法{r0}。"}, "target")
    with zipfile.ZipFile(out) as z:
        ch1 = z.read("OEBPS/ch1.xhtml").decode("utf-8")
    assert "<ruby>魔法<rt>まほう</rt></ruby>" in ch1, ch1


def test_image_alt_attribute_is_translated(tmp_path):
    """<img alt> 等可译属性独立成段（与 html 适配器一致）。"""
    ep = make_epub(tmp_path / "alt.epub", {
        "ch1.xhtml": chapter_xhtml("章", '<p>本文。</p><img src="p.png" alt="挿絵"/>'),
    })
    ad = EpubAdapter()
    model = ad.parse(ep)
    alt = next(b for b in model.blocks if b.meta.get("kind") == "attr")
    assert alt.text == "挿絵"
    out = tmp_path / "alt_out.epub"
    ad.render(out, model, {alt.seq: "插图"}, "target")
    with zipfile.ZipFile(out) as z:
        ch1 = z.read("OEBPS/ch1.xhtml").decode("utf-8")
    assert 'alt="插图"' in ch1, ch1
