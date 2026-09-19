"""语言代码表与下拉填充（用户反馈：项目设置里手填语言编码不人性化）。"""
from core.langs import LANGUAGES, fill_combo, label, name_of


class FakeCombo:
    """模拟 QComboBox 的最小接口（core.langs.fill_combo 是 duck-typing 的）。"""

    def __init__(self):
        self.items: list[tuple[str, str]] = []
        self.index = -1

    def clear(self):
        self.items = []
        self.index = -1

    def addItem(self, text, data):  # noqa: N802
        self.items.append((text, data))

    def findData(self, data):  # noqa: N802
        for i, (_, d) in enumerate(self.items):
            if d == data:
                return i
        return -1

    def setCurrentIndex(self, i):  # noqa: N802
        self.index = i

    def count(self):
        return len(self.items)

    def currentData(self):  # noqa: N802
        if 0 <= self.index < len(self.items):
            return self.items[self.index][1]
        return None

    def currentText(self):  # noqa: N802
        if 0 <= self.index < len(self.items):
            return self.items[self.index][0]
        return ""


def test_label_format_is_code_plus_name():
    assert label("en-US") == "en-US（英语-美国）"
    assert label("ja-JP") == "ja-JP（日语-日本）"
    assert name_of("zh-CN").startswith("中文")
    # 未知代码只显示代码本身（不编造名称）
    assert label("eo-XX") == "eo-XX"
    assert label("") == ""


def test_fill_combo_selects_known_code():
    c = FakeCombo()
    fill_combo(c, "ja-JP")
    assert c.currentData() == "ja-JP"
    assert c.currentText() == "ja-JP（日语-日本）"
    assert c.count() == len(LANGUAGES)


def test_fill_combo_keeps_unknown_code():
    """旧项目里的冷门代码要追加一项并选中，不能被静默改成别的语言。"""
    c = FakeCombo()
    fill_combo(c, "eo-XX")
    assert c.currentData() == "eo-XX"
    assert c.currentText() == "eo-XX"
    assert c.count() == len(LANGUAGES) + 1


def test_fill_combo_empty_current_leaves_first_item():
    c = FakeCombo()
    fill_combo(c)
    assert c.count() == len(LANGUAGES)
    assert c.index == -1


def test_language_codes_are_unique():
    codes = [code for code, _ in LANGUAGES]
    assert len(codes) == len(set(codes))
    assert all("-" in code for code in codes)
