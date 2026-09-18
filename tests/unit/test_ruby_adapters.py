"""四格式振假名黄金文件测试（增补设计 §1.9）：检测 → 三策略渲染往返。"""
from pathlib import Path

from adapters import get_adapter


def make_md(tmp_path, name="r.md"):
    p = tmp_path / name
    p.write_text("# 序章\n\n僕は北瀬一廣《かずひろ》。`code9` と魔法《まほう》。\n",
                 encoding="utf-8")
    return p


def test_md_ruby_parse_and_three_policies(tmp_path):
    p = make_md(tmp_path)
    ad = get_adapter("md")
    model = ad.parse(p)
    para = next(b for b in model.blocks if b.translatable and "一廣" in b.text)
    assert "{r0}" in para.text and "一廣《" not in para.text
    assert "{r1}" in para.text or "まほう" not in para.text
    entries = {e["token"]: e for e in para.meta["ruby"]}
    assert entries["{r0}"]["rt"] == "かずひろ"
    assert "`code9`" not in para.text  # 行内代码仍是保护占位符

    translations = {b.seq: f"T:{b.text}" for b in model.blocks if b.translatable}
    # keep：token 保留 → 原假名还原
    out = tmp_path / "keep.md"
    ad.render(out, model, translations, "target")
    text = out.read_text(encoding="utf-8")
    assert "T:僕は北瀬一廣《かずひろ》。" in text and "《まほう》" in text
    # translate：注音用译注音
    seq = para.seq
    out2 = tmp_path / "trans.md"
    ad.render(out2, model, translations, "target",
              ruby_maps={seq: {"{r0}": "卡兹希罗", "{r1}": "玛霍", "{r2}": "玛霍"}})
    t2 = out2.read_text(encoding="utf-8")
    assert "《卡兹希罗》" in t2 and "《玛霍》" in t2
    # drop：译文本身无 token → 无注音
    drop_trans = {b.seq: f"T:{b.text}".replace("{r0}", "").replace("{r1}", "")
                          .replace("{r2}", "")
                  for b in model.blocks if b.translatable}
    out3 = tmp_path / "drop.md"
    ad.render(out3, model, drop_trans, "target")
    t3 = out3.read_text(encoding="utf-8")
    assert "《" not in t3 and "一廣" in t3


def test_txt_loose_ruby(tmp_path):
    p = tmp_path / "r.txt"
    p.write_text("魔法（まほう）が使える。\n", encoding="utf-8")
    ad = get_adapter("txt")
    m1 = ad.parse(p)
    assert "{r0}" not in next(b.text for b in m1.blocks if b.translatable)
    m2 = ad.parse(p, opts={"ruby_loose": True})
    b = next(b for b in m2.blocks if b.translatable)
    assert b.text == "魔法{r0}が使える。"
    out = tmp_path / "o.txt"
    ad.render(out, m2, {b.seq: f"T:{b.text}"}, "target")
    assert "T:魔法（まほう）が使える。" in out.read_text(encoding="utf-8")


def test_html_ruby_native_restore(tmp_path):
    p = tmp_path / "r.html"
    p.write_text(
        "<html><body><p>彼の名は<ruby>一廣<rt>かずひろ</rt></ruby>だ。</p></body></html>",
        encoding="utf-8")
    ad = get_adapter("html")
    model = ad.parse(p)
    b = next(b for b in model.blocks if b.translatable)
    assert "一廣{r0}" in b.text and b.meta["ruby"][0]["rt"] == "かずひろ"

    translations = {b.seq: f"T:{b.text}" for b in model.blocks if b.translatable}
    out = tmp_path / "keep.html"
    ad.render(out, model, translations, "target")
    text = out.read_text(encoding="utf-8")
    # 锚定译词重建原生结构：尾串锚点（"T:"被冒号截断在 ruby 外）
    assert "<ruby>彼の名は一廣<rt>かずひろ</rt></ruby>" in text

    out2 = tmp_path / "trans.html"
    ad.render(out2, model, translations, "target", ruby_maps={b.seq: {"{r0}": "卡兹希罗"}})
    t2 = out2.read_text(encoding="utf-8")
    assert "<rt>卡兹希罗</rt>" in t2


def _add_ruby_run(para, base, rt):
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    para._p.append(parse_xml(
        f'<w:r {nsdecls("w")}><w:ruby>'
        f'<w:rubyPr><w:rubyAlign w:val="distributeSpace"/></w:rubyPr>'
        f'<w:rt><w:r><w:t>{rt}</w:t></w:r></w:rt>'
        f'<w:rubyBase><w:r><w:t>{base}</w:t></w:r></w:rubyBase>'
        f'</w:ruby></w:r>'))


def test_docx_ruby_parse_and_native_restore(tmp_path):
    from docx import Document
    src = tmp_path / "r.docx"
    doc = Document()
    para = doc.add_paragraph()
    para.add_run("彼の名は")
    _add_ruby_run(para, "一廣", "かずひろ")
    para.add_run("だ。")
    doc.save(str(src))

    ad = get_adapter("docx")
    model = ad.parse(src)
    b = next(b for b in model.blocks if b.translatable)
    assert b.text == "彼の名は一廣{r0}だ。"
    assert b.meta["ruby"][0]["rt"] == "かずひろ"

    translations = {b.seq: f"T:{b.text}" for b in model.blocks if b.translatable}
    out = tmp_path / "keep.docx"
    ad.render(out, model, translations, "target")
    xml = Document(str(out)).paragraphs[0]._p.xml
    assert "<w:rt>" in xml and "かずひろ" in xml          # 原生 w:ruby 保留（注音原样）
    assert "<w:rubyBase>" in xml and "一廣" in xml        # 尾串锚点进入 rubyBase

    out2 = tmp_path / "trans.docx"
    ad.render(out2, model, translations, "target",
              ruby_maps={b.seq: {"{r0}": "卡兹希罗"}})
    xml2 = Document(str(out2)).paragraphs[0]._p.xml
    assert "卡兹希罗" in xml2
