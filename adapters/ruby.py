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
# markdown / html 内联 HTML 振假名：<ruby>基<rt>音</rt></ruby>
# 用「整块匹配 + 内部提取」两步，比位置式正则更耐变体：允许 <rb>、<rp>、
# 标签属性、基词里夹带其它行内标签、以及跨行书写。
_HTML_RUBY_BLOCK_RE = re.compile(r"<ruby\b[^>]*>(.*?)</ruby>", re.S | re.I)
_RT_INNER_RE = re.compile(r"<rt\b[^>]*>(.*?)</rt>", re.S | re.I)
_RP_INNER_RE = re.compile(r"<rp\b[^>]*>.*?</rp>", re.S | re.I)
_RUBY_INNER_TAG_RE = re.compile(r"</?(?:ruby|rb|rt|rp)\b[^>]*>", re.I)
_WS_COLLAPSE_RE = re.compile(r"\s+")


def has_ruby(text: str) -> bool:
    return bool(RUBY_TOKEN_FULL.search(text))


def _norm_space(text: str) -> str:
    return _WS_COLLAPSE_RE.sub(" ", text or "").strip()


def _split_html_ruby(inner: str) -> tuple[str, str]:
    """从 `<ruby>` 内部拆出 (基词, 注音)。基词里保留其它行内标签（如 `<b>`）。"""
    rt_m = _RT_INNER_RE.search(inner)
    rt = _norm_space(rt_m.group(1)) if rt_m else ""
    base = _RT_INNER_RE.sub("", inner)
    base = _RP_INNER_RE.sub("", base)
    base = _RUBY_INNER_TAG_RE.sub("", base)
    return _norm_space(base), rt


def rubyize_text(text: str, loose: bool = False,
                 html: bool = False) -> tuple[str, list[dict]]:
    """文本级检测：基词内联 + 注音槽 token；返回 (新文本, entries)。确定性可重放。

    html=True 时额外识别 markdown / html 里内联的
    `<ruby>基<rt>音</rt></ruby>`（含 `<rb>` / `<rp>` / 属性 / 跨行等变体）。

    背景：markdown 适配器此前只认青空文库式 `《》` 与全角括号，**完全不认
    内联 HTML 的 `<ruby>`**，标签连同内容被当正文送去翻译 —— 于是
    drop/keep/translate 三种振假名策略全部失效（连策略指令都不会注入）。
    """
    entries: list[dict] = []

    def _emit(base: str, rt: str, style: str) -> str:
        if not base:
            return base
        token = "{r%d}" % len(entries)
        entries.append({"token": token, "base": base, "rt": rt, "style": style})
        return base + token

    def _sub(style: str):
        def repl(m: re.Match) -> str:
            return _emit(m.group(1), m.group(2), style)
        return repl

    new = text
    if html:
        # 先处理显式标记，再处理青空文库式与宽松式，编号连续不冲突
        new = _HTML_RUBY_BLOCK_RE.sub(
            lambda m: _emit(*_split_html_ruby(m.group(1)), "html"), new)
    new = _AOZORA_RE.sub(_sub("aozora"), new)
    if loose:
        new = _PAREN_RE.sub(_sub("paren"), new)
    return new, entries


def strip_ruby_tokens(text: str) -> str:
    """drop 策略：剥离注音槽并清理残留空隙。"""
    out = RUBY_TOKEN_FULL.sub("", text)
    return re.sub(r" +([，。！？；、）】》」』,.!?;:])", r"\1", out)


def strip_ruby_markup(text: str) -> str:
    """drop 策略：把 `<ruby>` 标记与注音一起剥掉，只留基词。

    仅删注音槽（`strip_ruby_tokens`）是不够的：译文里的 `<ruby>` 标签还可能来自
    ①模型把标签原样抄回；②**修复前导入的旧项目**（库里存的 src_text 仍是原始标签，
    从未被 token 化）。用户要求 drop 后译文里**不应出现 ruby 格式**，故在此兜底。

    先整块删掉 `<rt>`（注音）与 `<rp>`（括号回退），再抹掉 `<ruby>`/`<rb>` 标签
    —— 只保留基词。这样**未闭合/落单**的标签也能正确处理（块匹配做不到）。
    最后仍然走 `strip_ruby_tokens` 清残留注音槽。
    """
    if not text:
        return text
    out = text
    if "<" in out:
        out = _RT_INNER_RE.sub("", out)       # 注音整块删除
        out = _RP_INNER_RE.sub("", out)       # 括号回退整块删除
        out = _RUBY_INNER_TAG_RE.sub("", out)  # ruby/rb 等标签本身
    return strip_ruby_tokens(out)


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
_LATIN_RUN_RE = re.compile(r"[A-Za-z0-9]+")


def split_anchor_hint(text: str, src_base: str | None = None) -> tuple[str, str | None]:
    """取文本末尾的词作为振假名基词锚点：返回 (before, base|None)。

    会按**源基词长度**截短。纯贪心锚定会把基词前面的词一起卷进 `<ruby>`
    （源文 `彼の名は一廣`，基词其实只有 `一廣`）；中文目标更极端 ——
    `他使用了魔法` 全是汉字，按字符类无法区分。源基词长度是可靠的上界；
    纯拉丁串按整词处理、不截短。不传 `src_base` 时即纯尾串锚点。

    md / html / epub / docx 的原生还原统一走这个函数，避免各格式行为不一致。
    """
    m = _ANCHOR_RE.search(text)
    if not m:
        return text, None
    base = _trim_base(m.group(1), src_base)
    return text[: m.end(1) - len(base)], base


def _trim_base(run: str, src_base: str | None) -> str:
    """用**源基词长度**给锚定结果截短。

    东亚文字没有词间空格，贪心锚定会把前面的词一起吃进基词：
    `彼は<ruby>魔法…` → run 为 `彼は魔法`，中文目标更极端（`他使用了魔法`，
    全是汉字，按字符类无法区分）。源基词长度（`魔法`=2）是可靠的上界。
    纯拉丁/数字串按整词处理不截短 —— 英文单词长度与源文并不对应。
    """
    if not src_base or not run:
        return run
    if _LATIN_RUN_RE.fullmatch(run):
        return run
    n = max(1, len(src_base))
    return run[-n:] if len(run) > n else run


def restore_ruby_native(text: str, entries: list[dict] | None,
                        rt_map: dict[str, str] | None = None) -> str:
    """token → 原生 `<ruby>译词<rt>注音</rt></ruby>`（以 token 前最近的词为译词）。

    用于 markdown / html / epub 里内联 HTML 振假名（style="html"）。与 html 适配器
    的兜底策略一致：锚不到译词时降级为 `（注音）`，不静默丢注音。

    按 token 切分并累积前置文本，因此同一段里的连续多个 `<ruby>` 都能正确锚定
    （若逐个 replace，前一个插入的 `</ruby>` 会挡住后一个的锚定）。
    """
    if not entries:
        return text
    by_token = {e["token"]: e for e in entries}
    out: list[str] = []
    buf = ""
    for piece in ANY_TOKEN_SPLIT.split(text):
        if not piece:
            continue
        e = by_token.get(piece)
        if e is None:
            buf += piece
            continue
        rt = (rt_map or {}).get(piece) or e.get("rt", "")
        before, base = split_anchor_hint(buf, e.get("base"))
        if base:
            out.append(f"{before}<ruby>{base}<rt>{rt}</rt></ruby>")
        else:
            out.append(f"{buf}（{rt}）")
        buf = ""
    out.append(buf)
    return "".join(out)


def restore_ruby_mixed(text: str, entries: list[dict] | None,
                       rt_map: dict[str, str] | None = None) -> str:
    """按条目样式还原振假名（md / srt 共用，故放在这里而非某个适配器内）。

    - `style="html"`（内联 `<ruby>基<rt>音</rt></ruby>`）→ 原生
      `<ruby>译词<rt>注音</rt></ruby>`（保持源文件的 HTML 形态）
    - 其余（`aozora` / `paren`）→ 文本还原 `《注音》` / `（注音）`

    两种样式可能同段共存，各自只处理自己的 token，互不干扰。
    """
    if not entries:
        return text
    native = [e for e in entries if e.get("style") == "html"]
    textual = [e for e in entries if e.get("style") != "html"]
    if native:
        text = restore_ruby_native(text, native, rt_map)
    if textual:
        text = restore_ruby(text, textual, rt_map)
    return text
