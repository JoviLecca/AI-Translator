from pathlib import Path

from adapters.ruby import (RUBY_TOKEN_FULL, restore_ruby, rubyize_text,
                           split_anchor, strip_ruby_tokens)
from core.project import Project


def test_rubyize_strict_aozora():
    text = "僕の名前は北瀬一廣《かずひろ》だ。"
    flat, entries = rubyize_text(text)
    assert flat == "僕の名前は北瀬一廣{r0}だ。"
    assert entries == [{"token": "{r0}", "base": "北瀬一廣", "rt": "かずひろ",
                        "style": "aozora"}]


def test_rubyize_multiple_and_numbering():
    text = "漢字《かんじ》と漢字《かんじ》と魔法《まほう》。"
    flat, entries = rubyize_text(text)
    assert flat == "漢字{r0}と漢字{r1}と魔法{r2}。"
    assert [e["token"] for e in entries] == ["{r0}", "{r1}", "{r2}"]


def test_rubyize_loose_paren_off_by_default():
    text = "魔法（まほう）が使える。"
    flat, entries = rubyize_text(text)
    assert flat == text and entries == []
    flat2, entries2 = rubyize_text(text, loose=True)
    assert "{r0}" in flat2 and entries2[0]["style"] == "paren"


def test_restore_ruby_three_modes():
    _, entries = rubyize_text("漢字《かんじ》だ")
    assert restore_ruby("訳詞{r0}だ", entries) == "訳詞《かんじ》だ"          # keep
    assert restore_ruby("訳詞{r0}だ", entries, {"{r0}": "汉字注音"}) == \
        "訳詞《汉字注音》だ"                                                 # translate
    assert restore_ruby("訳詞だ", entries) == "訳詞だ"                        # drop（无 token）
    _, paren_entries = rubyize_text("魔法（まほう）", loose=True)
    assert restore_ruby("魔法{r0}", paren_entries) == "魔法（まほう）"


def test_strip_ruby_tokens():
    assert strip_ruby_tokens("訳詞{r0}です") == "訳詞です"
    assert strip_ruby_tokens("訳詞 {r0}、です") == "訳詞、です"


def test_split_anchor():
    # CJK 无分词边界：整个尾串即锚点（ruby 覆盖尾串，见增补设计 §1.6 启发式）
    assert split_anchor("その名はカズヒロ") == ("", "その名はカズヒロ")
    # ASCII 标点/冒号自然截断锚点
    assert split_anchor("T:彼の名は一廣") == ("T:", "彼の名は一廣")
    before, base = split_anchor("hello world")   # 拉丁空格分界
    assert base == "world"
    assert split_anchor("。！？")[1] is None      # 纯标点无锚


def test_token_namespace_no_collision():
    """注音槽 {rN} 与保护占位符 {N} 互不匹配。"""
    assert RUBY_TOKEN_FULL.search("{0}") is None
    assert RUBY_TOKEN_FULL.search("{r12}") is not None


def test_cfg_hash_ruby_semantics(tmp_path):
    """默认策略不改变指纹（旧项目升级不失效）；策略/宽松变化才失效（增补设计 §1.7）。"""
    a = Project.create(tmp_path / "a", name="a", src_lang="ja-JP", tgt_lang="zh-CN")
    b = Project.create(tmp_path / "b", name="b", src_lang="ja-JP", tgt_lang="zh-CN",
                       ruby_policy="drop", ruby_loose=False)  # 显式默认值
    assert a.cfg_hash() == b.cfg_hash()
    b.update_config(ruby_policy="translate")
    assert a.cfg_hash() != b.cfg_hash()
    b.update_config(ruby_policy="drop", ruby_loose=True)
    assert a.cfg_hash() != b.cfg_hash()
    a.close(); b.close()
