"""分段器（设计 §7.2）：段落为基本单位；超长段按句末标点二次切分。

- 中英混排均支持：CJK 句末标点直接断句；拉丁句点要求后随空白/行尾/大写，避免小数与缩写误切；
- 切分出的子段带 part 序号，渲染时按语言习惯重新拼接（CJK 无空格、拉丁空格）。
"""
from __future__ import annotations

import re

MAX_CHARS = 1500
_CJK_END = "。！？；…"
_LATIN_END = ".!?"
_CLOSERS = "」』”）\"’》〉〕"
_HARD_LANG = re.compile(r"[\u4e00-\u9fff]")


def is_mostly_latin(text: str) -> bool:
    cjk = len(_HARD_LANG.findall(text))
    return cjk * 2 < len(text)


def _break_positions(text: str) -> list[int]:
    """返回每个可断句的位置（断点索引，含闭合引号之后）。"""
    breaks: list[int] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in _CJK_END or ch in _LATIN_END:
            j = i + 1
            while j < n and text[j] in _CLOSERS:
                j += 1
            if ch in _CJK_END or ch in "!?":
                breaks.append(j)
            elif ch == ".":
                # 拉丁句点：后随行尾/空白+任意、或直接到结尾才算句末；小数/缩写跳过
                if j >= n:
                    breaks.append(j)
                elif text[j] in " \t\n\r" or text[j] in _CLOSERS:
                    breaks.append(j)
            i = j
            continue
        i += 1
    if not breaks or breaks[-1] < n:
        breaks.append(n)
    return breaks


def split_long(text: str, limit: int = MAX_CHARS) -> list[str]:
    """超长段切分为若干 <=limit 的子段（单句超长时硬切）。"""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    start = 0
    for end in _break_positions(text):
        if end - start >= limit:
            # 当前句已到/超限：先吐出之前积累的，再处理这句
            if start < end:
                chunk = text[start:end]
                while len(chunk) > limit:
                    parts.append(chunk[:limit])
                    chunk = chunk[limit:]
                parts.append(chunk)
            start = end
        elif end == len(text):
            parts.append(text[start:end])
            start = end
    if start < len(text):
        parts.append(text[start:])
    return [p for p in parts if p]


def join_parts(parts: list[str], latin: bool) -> str:
    return (" ".join(p.strip() for p in parts)).strip() if latin else "".join(parts)
