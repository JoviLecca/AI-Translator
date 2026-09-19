"""语言代码与显示名（用户反馈：项目设置里让用户手填语言编码不人性化）。

- 下拉显示格式：`代码（中文名）`，如 `en-US（英语-美国）`；
- `itemData` 存语言代码本身，业务层一律用 `currentData()` 取值；
- `fill_combo()` 会为不在此表内的历史代码追加一项，保证旧项目仍能正确显示与保存；
- 业务只用到代码的语言前缀（zh / ja / en …），见
  `core.term_induction.extract_candidates` 与 `adapters.ocr_adapter._model_lang`，
  所以表中未列出的冷门代码依然可用。
"""
from __future__ import annotations

# (代码, 中文名)。中文名按「语言-地区」组织，与用户可读习惯一致。
LANGUAGES: list[tuple[str, str]] = [
    ("zh-CN", "中文-中国（简体）"),
    ("zh-TW", "中文-台湾（繁体）"),
    ("zh-HK", "中文-香港（繁体）"),
    ("en-US", "英语-美国"),
    ("en-GB", "英语-英国"),
    ("ja-JP", "日语-日本"),
    ("ko-KR", "韩语-韩国"),
    ("fr-FR", "法语-法国"),
    ("de-DE", "德语-德国"),
    ("es-ES", "西班牙语-西班牙"),
    ("pt-BR", "葡萄牙语-巴西"),
    ("it-IT", "意大利语-意大利"),
    ("ru-RU", "俄语-俄罗斯"),
    ("uk-UA", "乌克兰语-乌克兰"),
    ("ar-SA", "阿拉伯语-沙特阿拉伯"),
    ("th-TH", "泰语-泰国"),
    ("vi-VN", "越南语-越南"),
    ("id-ID", "印尼语-印度尼西亚"),
    ("tr-TR", "土耳其语-土耳其"),
    ("pl-PL", "波兰语-波兰"),
    ("nl-NL", "荷兰语-荷兰"),
]

_BY_CODE: dict[str, str] = dict(LANGUAGES)


def name_of(code: str) -> str:
    """语言代码对应的中文名；未知代码返回空串。"""
    return _BY_CODE.get(code or "", "")


def label(code: str) -> str:
    """下拉项显示文案：`代码（中文名）`；未知代码只显示代码本身。"""
    if not code:
        return ""
    n = name_of(code)
    return f"{code}（{n}）" if n else code


def fill_combo(combo, current: str = "") -> None:
    """填充语言下拉框（duck-typing，不 import Qt，便于复用与测试）。

    - 每项文本为 `代码（中文名）`，itemData 为语言代码；
    - `current` 命中表内代码则选中它；不在表内则**追加一项**再选中，
      避免旧项目里的冷门代码被静默改掉。
    """
    combo.clear()
    for code, _ in LANGUAGES:
        combo.addItem(label(code), code)
    if not current:
        return
    idx = combo.findData(current)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    else:
        combo.addItem(current, current)
        combo.setCurrentIndex(combo.count() - 1)
