from pathlib import Path

from adapters.ruby import (RUBY_TOKEN_FULL, restore_ruby, restore_ruby_native,
                           rubyize_text, split_anchor_hint, strip_ruby_markup,
                           strip_ruby_tokens)
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


def test_strip_ruby_markup_removes_tags_and_reading():
    """drop 的最终效果：译文里既不出现注音、也不出现 `<ruby>` 格式，只留基词。"""
    # 基本形态
    assert strip_ruby_markup("他使用了<ruby>魔法<rt>まほう</rt></ruby>。") == "他使用了魔法。"
    # <rb> 变体 + 标签属性
    assert strip_ruby_markup("A<ruby class='r'><rb>魔法</rb><rt>まほう</rt></ruby>B") == "A魔法B"
    # <rp> 括号回退写法
    assert (strip_ruby_markup("A<ruby>魔法<rp>(</rp><rt>まほう</rt><rp>)</rp></ruby>B")
            == "A魔法B")
    # 未闭合 / 落单标签
    assert strip_ruby_markup("A<ruby>魔法<rt>まほう</rt>B") == "A魔法B"
    # 同时残留注音槽
    assert strip_ruby_markup("魔法{r0}を使った。") == "魔法を使った。"
    # 无标签时原样返回
    assert strip_ruby_markup("普通文本") == "普通文本"
    assert strip_ruby_markup("") == ""


def test_restore_ruby_native_trims_base_by_source_length():
    """东亚文字无空格，锚定要按源基词长度截短，不能把前面的词卷进 `<ruby>`。"""
    entries = [{"token": "{r0}", "base": "魔法", "rt": "まほう", "style": "html"}]
    # 日语：前面的假名不能被卷进来
    assert (restore_ruby_native("彼は魔法{r0}を使った。", entries)
            == "彼は<ruby>魔法<rt>まほう</rt></ruby>を使った。")
    # 中文目标更极端（全是汉字，按字符类分不开）
    assert (restore_ruby_native("他使用了魔法{r0}。", entries)
            == "他使用了<ruby>魔法<rt>まほう</rt></ruby>。")
    # 同段连续两个构造互不干扰
    two = [{"token": "{r0}", "base": "剣", "rt": "けん", "style": "html"},
           {"token": "{r1}", "base": "盾", "rt": "たて", "style": "html"}]
    assert (restore_ruby_native("そして剣{r0}と盾{r1}を得た。", two)
            == "そして<ruby>剣<rt>けん</rt></ruby>と<ruby>盾<rt>たて</rt></ruby>を得た。")
    # 拉丁词按整词处理，不截短
    en = [{"token": "{r0}", "base": "magic", "rt": "majik", "style": "html"}]
    assert (restore_ruby_native("he used magical{r0} arts", en)
            == "he used <ruby>magical<rt>majik</rt></ruby> arts")
    # 锚不到译词 → 降级为括号注音
    assert (restore_ruby_native("{r0}冒頭", entries) == "（まほう）冒頭")


def test_split_anchor_hint():
    # 不传源基词时即纯尾串锚点（CJK 无分词边界，见增补设计 §1.6 启发式）
    assert split_anchor_hint("その名はカズヒロ") == ("", "その名はカズヒロ")
    # ASCII 标点/冒号自然截断锚点
    assert split_anchor_hint("T:彼の名は一廣") == ("T:", "彼の名は一廣")
    _before, base = split_anchor_hint("hello world")   # 拉丁空格分界
    assert base == "world"
    assert split_anchor_hint("。！？")[1] is None      # 纯标点无锚
    # 传源基词 → 按长度截短，基词前面的词留在 ruby 之外
    assert split_anchor_hint("T:彼の名は一廣", "一廣") == ("T:彼の名は", "一廣")


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
