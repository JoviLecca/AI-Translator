"""振假名（ruby）子系统（增补设计 §1）。

内部表示：基词保留在平文本中参与翻译 + 注音槽 token {r0}{r1}…（独立命名空间，
与 {0} 保护占位符互不冲突）；块元数据 meta["ruby"] 记录 base/rt/原始形态。

策略：
- drop     译文删除全部注音槽（引擎负责剥离与校验）
- keep     注音槽原样保留，渲染时还原为原假名
- translate 注音槽保留，注音内容译为目标语（引擎随 JSON 的 ruby 字段接收，
           渲染时用译注音还原）
"""
from __future__ import annotations

import re

RUBY_TOKEN_RE = re.compile(r"\{r(\d+)\}")
ANY_TOKEN_SPLIT = re.compile(r"(\{r?\d+\})")          # 保护占位符 + 注音槽统一切分
# 带捕获组：re.split 时保留 token 本身（docx 还原依赖）
RUBY_TOKEN_FULL = re.compile(r"(\{r\d+\})")

# 青空文库式：漢字《かんじ》（严格，默认开）
_AOZORA_RE = re.compile(
    r"([\u4e00-\u9fff々〆\u30A0-\u30FFーA-Za-z0-9]{1,20})《([^》\n]{1,30})》")
# 宽松式：漢字（かんじ），注音须为纯假名（默认关，项目开关）
_PAREN_RE = re.compile(
    r"([\u4e00-\u9fff々〆\u30A0-\u30FFー]{1,20})（([\u3040-\u30FFー]{1,30})）")


def has_ruby(text: str) -> bool:
    return bool(RUBY_TOKEN_FULL.search(text))


def rubyize_text(text: str, loose: bool = False) -> tuple[str, list[dict]]:
    """文本级检测：基词内联 + 注音槽 token；返回 (新文本, entries)。确定性可重放。"""
    entries: list[dict] = []

    def _sub(style: str):
        def repl(m: re.Match) -> str:
            token = "{r%d}" % len(entries)
            entries.append({"token": token, "base": m.group(1), "rt": m.group(2),
                            "style": style})
            return m.group(1) + token
        return repl

    new = _AOZORA_RE.sub(_sub("aozora"), text)
    if loose:
        new = _PAREN_RE.sub(_sub("paren"), new)
    return new, entries


def strip_ruby_tokens(text: str) -> str:
    """drop 策略：剥离注音槽并清理残留空隙。"""
    out = RUBY_TOKEN_FULL.sub("", text)
    return re.sub(r" +([，。！？；、）】》」』,.!?;:])", r"\1", out)


def restore_ruby(text: str, entries: list[dict] | None,
                 rt_map: dict[str, str] | None = None,
                 style_override: str | None = None) -> str:
    """token → 原格式注音；rt_map 提供 translate 策略的译注音（缺省回退原假名）。

    docx 风格默认按括号式还原（R1 已知边界）；原生 w:ruby 还原见 docx 适配器。
    """
    if not entries:
        return text
    by_token = {e["token"]: e for e in entries}

    def rep(m: re.Match) -> str:
        e = by_token.get(m.group(0))
        if e is None:
            return m.group(0)
        rt = (rt_map or {}).get(e["token"]) or e["rt"]
        style = style_override or e.get("style", "aozora")
        if style == "paren":
            return f"（{rt}）"
        return f"《{rt}》"

    return RUBY_TOKEN_FULL.sub(rep, text)


# ---------- 译词锚定（html / docx 原生还原用） ----------
_ANCHOR_RE = re.compile(r"([\u4e00-\u9fff々〆\u3040-\u30FFーA-Za-z0-9]{1,40})[ \t]*$")


def split_anchor(text: str) -> tuple[str, str | None]:
    """取文本末尾的连续词作为振假名基词锚点：返回 (before, base|None)。"""
    m = _ANCHOR_RE.search(text)
    if not m:
        return text, None
    return text[:m.start(1)], m.group(1)
