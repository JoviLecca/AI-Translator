"""翻译风格预设（设计 FR-05）：预设 + 自定义。"""
from __future__ import annotations

STYLE_PRESETS = {
    "formal": "正式书面语",
    "colloquial": "轻松口语",
    "literary": "文学意译",
    "academic": "学术严谨",
    "custom": "自定义",
}

_PROMPTS = {
    "formal": "采用正式书面语风格：用词严谨、句式规范，避免口语化与网络用语。",
    "colloquial": "采用轻松口语风格：自然流畅的日常表达，可适度意译，贴近目标语言读者的阅读习惯。",
    "literary": "采用文学翻译风格：注重文气、节奏与画面感，修辞得体，允许创造性表达以传达原文韵味。",
    "academic": "采用学术风格：术语准确、表述客观、逻辑严密，遵循目标语言的学术写作惯例。",
}


def style_prompt(preset: str, custom_prompt: str = "") -> str:
    if preset == "custom":
        return custom_prompt.strip() or "按译者自定义要求翻译。"
    return _PROMPTS.get(preset, _PROMPTS["formal"])
