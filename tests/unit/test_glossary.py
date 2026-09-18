import json

from core.glossary import Glossary


def test_csv_roundtrip(tmp_path):
    g = Glossary(tmp_path)
    g.add("灵石", ["spirit stone"], "修炼货币")
    g.add("金丹期", ["Golden Core", "Core Formation"], "境界")
    g.save()
    assert (tmp_path / "glossary.csv").exists()

    g2 = Glossary(tmp_path)
    g2.load()
    assert len(g2.entries) == 2
    t = g2.get("金丹期")
    assert t.candidates == ["Golden Core", "Core Formation"]
    assert t.note == "境界"


def test_candidates_capped_at_3(tmp_path):
    g = Glossary(tmp_path)
    g.add("术法", ["a", "b", "c", "d"], "")
    assert len(g.get("术法").candidates) == 3


def test_effective_hash_note_change_stable(tmp_path):
    """改注释不改变有效指纹（设计 v0.4 #24）。"""
    g = Glossary(tmp_path)
    g.add("灵石", ["spirit stone"], "old")
    h1 = g.effective_hash()
    g.get("灵石").note = "new note"
    assert g.effective_hash() == h1
    g.get("灵石").candidates = ["mana stone"]
    assert g.effective_hash() != h1


def test_txt_format(tmp_path):
    (tmp_path / "glossary.txt").write_text(
        "灵石\tspirit stone\t货币\n剑修\tsword cultivator\t\n", encoding="utf-8")
    g = Glossary(tmp_path)
    g.load()
    assert g.get("灵石").candidates == ["spirit stone"]
    assert g.get("剑修").note == ""


def test_external_changed(tmp_path):
    g = Glossary(tmp_path)
    g.add("灵石", ["spirit stone"])
    g.save()
    assert not g.external_changed()
    (tmp_path / "glossary.csv").write_text("源语言,目标语,注释\n灵石,mana stone,\n",
                                           encoding="utf-8-sig")
    assert g.external_changed()


def test_import_merge(tmp_path):
    g = Glossary(tmp_path)
    g.add("灵石", ["spirit stone"])
    other = tmp_path / "other.csv"
    other.write_text("源语言,目标语,注释\n灵石,ignored,\n宗门,sect,组织\n",
                     encoding="utf-8-sig")
    n = g.import_merge(other)
    assert n == 1
    assert g.get("灵石").candidates == ["spirit stone"]  # 已有不覆盖
    assert g.get("宗门").candidates == ["sect"]
