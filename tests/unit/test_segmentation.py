from core.segmentation import is_mostly_latin, join_parts, split_long


def test_short_unchanged():
    text = "林凡睁开眼，发现自己躺在破庙里。"
    assert split_long(text) == [text]


def test_long_split_on_cjk():
    sentence = "他运转功法，只觉丹田一热。"
    text = sentence * 120  # >1500 字
    parts = split_long(text)
    assert len(parts) > 1
    assert all(len(p) <= 1500 for p in parts)
    assert join_parts(parts, latin=False) == text


def test_decimal_not_split():
    text = "直径 3.14 厘米。" * 300
    parts = split_long(text)
    rejoined = join_parts(parts, latin=False)
    assert rejoined == text
    # 3.14 不应成为断点：首段开头不应是 "14 厘米"
    assert not parts[0].startswith("14")


def test_latin_sentence_split():
    text = ("The old man smiled. " * 200) + "End."
    parts = split_long(text)
    assert len(parts) > 1
    assert join_parts(parts, latin=True) == " ".join(p.strip() for p in parts).strip()


def test_is_mostly_latin():
    assert is_mostly_latin("Hello world this is a test")
    assert not is_mostly_latin("这是一段中文文本")
