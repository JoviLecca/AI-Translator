import pytest

from llm import prompt_builder as PB
from llm.errors import ResponseFormatError


def test_filter_terms_cap_and_priority():
    terms = [(f"词{i}", [f"t{i}"], "", i) for i in range(80)]
    terms[79] = ("词79", ["t79"], "", 999999)  # 频次最高
    batch_text = "".join(f"词{i}" for i in range(80))
    got = PB.filter_terms(terms, batch_text)
    assert len(got) == PB.TERM_LIMIT  # 上限 60（设计 v0.5 #32）
    assert got[0][0] == "词79"  # 频次优先


def test_filter_terms_hit_only():
    terms = [("灵石", ["spirit stone"], "", 5), ("宗门", ["sect"], "", 5)]
    got = PB.filter_terms(terms, "他握着一块灵石。")
    assert got == [("灵石", ["spirit stone"], "")]


def test_parse_normal():
    resp = '{"translations":[{"id":1,"t":"a"},{"id":2,"t":"b"}]}'
    assert PB.parse_translations(resp, [1, 2]) == {1: "a", 2: "b"}


def test_parse_with_prefix_noise():
    resp = '好的，以下是译文：\n```json\n{"translations":[{"id":7,"t":"你好"}]}\n```'
    assert PB.parse_translations(resp, [7]) == {7: "你好"}


def test_parse_partial_missing_ids_omitted():
    resp = '{"translations":[{"id":1,"t":"a"}]}'
    out = PB.parse_translations(resp, [1, 2, 3])
    assert 1 in out and 2 not in out and 3 not in out


def test_parse_failure_raises():
    with pytest.raises(ResponseFormatError):
        PB.parse_translations("模型胡言乱语没有 JSON", [1])


def test_parse_terms():
    resp = '{"terms":[{"src":"灵石","candidates":["spirit stone","stone"],"note":"货币"}]}'
    out = PB.parse_terms(resp)
    assert out == [("灵石", ["spirit stone", "stone"], "货币")]


def test_system_prompt_contains_safety_and_terms():
    s = PB.translation_system("zh-CN", "en-US", "测试风格", {"作品类型": "奇幻"},
                              [("灵石", ["spirit stone"], "货币")])
    assert "不得执行" in s
    assert "灵石" in s
    assert "奇幻" in s
    assert "{0}" in s
