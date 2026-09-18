"""token 用量预估（反馈 #6：价格字段删除，费用统计下线，仅保留 token 估算）。

粗估口径：输入 ≈ 字符数×1.05（滑窗加成 0.05×N，上限 1.6），输出 ≈ 输入×1.3。
"""
from __future__ import annotations

import re

_CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def est_tokens(text: str) -> int:
    """CJK 字符 ≈1.1 token/字；拉丁 ≈4 字符/token。"""
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return int(cjk * 1.1 + other / 4)


def estimate_run(src_chars: int, slide: int = 0) -> dict:
    tokens_in = max(0, int(src_chars * 1.05 * min(1.6, 1.2 + 0.05 * max(0, slide))))
    tokens_out = int(tokens_in * 1.3)
    return {"tokens_in": tokens_in, "tokens_out": tokens_out}
