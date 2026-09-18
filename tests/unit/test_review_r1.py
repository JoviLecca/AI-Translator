"""代码审查五轮循环 · 第1轮回归：数据与格式适配层。"""
from pathlib import Path

from adapters import get_adapter
from storage.db import Database


def make_db(tmp_path):
    db = Database(tmp_path / "w.db")
    doc = db.upsert_document("source/a.md", "md", "h")
    blocks = [
        {"seq": 0, "text": "正文", "is_heading": False, "translatable": True,
         "src_hash": "h1"},
        {"seq": 1, "text": "", "is_heading": False, "translatable": False,
         "src_hash": "h2"},
        {"seq": 2, "text": "第二段", "is_heading": False, "translatable": True,
         "src_hash": "h3"},
    ]
    db.replace_segments(doc, blocks, "cfg")
    return db


def test_list_segments_translatable_false_filters_strictly(tmp_path):
    """translatable=False 只返回透传段（第1轮修复：旧实现返回全部）。"""
    db = make_db(tmp_path)
    non = db.list_segments(translatable=False)
    assert len(non) == 1 and non[0]["translatable"] == 0
    yes = db.list_segments(translatable=True)
    assert len(yes) == 2 and all(r["translatable"] == 1 for r in yes)
    db.close()


def test_html_bi_table_includes_attr_segments(tmp_path):
    """bi_table 模式属性段（alt 等）成对入表，不再丢失（第1轮修复）。"""
    p = tmp_path / "a.html"
    p.write_text('<html><body><p>段落</p><p><img src="x.png" alt="原图注"></p>'
                 '</body></html>', encoding="utf-8")
    ad = get_adapter("html")
    model = ad.parse(p)
    tr = {b.seq: f"T:{b.text}" for b in model.blocks if b.translatable}
    out = tmp_path / "t.html"
    ad.render(out, model, tr, "bi_table")
    text = out.read_text(encoding="utf-8")
    assert "T:原图注" in text and "原图注" in text
    # 表头 + 段落行 + 属性行
    assert text.count("<tr>") == 3
    # target 模式属性照旧回 DOM
    out2 = tmp_path / "t2.html"
    ad.render(out2, model, tr, "target")
    assert 'alt="T:原图注"' in out2.read_text(encoding="utf-8")


def test_html_bi_inter_table_uses_td(tmp_path):
    """表格单元格内双语原文用 <td> 而非非法的 <tr>><p>（第1轮修复）。"""
    p = tmp_path / "b.html"
    p.write_text('<html><body><table><tr><td>单元A</td><td>单元B</td></tr>'
                 '</table></body></html>', encoding="utf-8")
    ad = get_adapter("html")
    model = ad.parse(p)
    tr = {b.seq: f"T:{b.text}" for b in model.blocks if b.translatable}
    out = tmp_path / "bi.html"
    ad.render(out, model, tr, "bi_inter")
    text = out.read_text(encoding="utf-8")
    assert '<td class="bilingual-src">' in text
    assert "<p class=\"bilingual-src\"" not in text  # 表内不再出现 p
    assert "单元A" in text and "T:单元A" in text
    # 非表格环境的 bi_inter 仍用 <p>
    p2 = tmp_path / "c.html"
    p2.write_text("<html><body><p>普通段</p></body></html>", encoding="utf-8")
    m2 = ad.parse(p2)
    tr2 = {b.seq: f"T:{b.text}" for b in m2.blocks if b.translatable}
    out2 = tmp_path / "bi2.html"
    ad.render(out2, m2, tr2, "bi_inter")
    assert '<p class="bilingual-src">' in out2.read_text(encoding="utf-8")
