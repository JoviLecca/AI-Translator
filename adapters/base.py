"""格式适配层基类（设计 §7.1）。

统一表示：DocumentModel = 结构骨架 + 段列表（Block）。
- 块级整体成段，内联元素（行内代码/公式/图片/链接 URL）以占位符 {0}、{1} 保护（设计 §7.1 内联结构保真）；
- 超长段由 build 流程做确定性切分（导入与导出重放同一算法，保证 seq 稳定对齐）；
- 非文本段 translatable=False，直接透传。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


class FormatError(Exception):
    pass


@dataclass
class Block:
    seq: int
    text: str                      # 送翻平文本（占位符已替换）
    is_heading: bool = False
    translatable: bool = True
    meta: dict = field(default_factory=dict)


@dataclass
class DocumentModel:
    path: Path
    fmt: str
    blocks: list[Block]
    skeleton: object | None = None  # 格式专属骨架（lxml 树、docx 对象等）


_TOKEN_RE = re.compile(r"\{r?(\d+)\}")
_WS_RE = re.compile(r"[\s\u200b\u200c\u200d\u2060\ufeff]+")


def effectively_empty(text: str) -> bool:
    """空白段判定（反馈 #2/#3）：剥离占位符/注音槽与所有 Unicode 空白（含零宽）后为空。

    仅含图片占位符 {0}、行内代码、不可见字符的段都视为透传段，不进入翻译队列。
    """
    return not _WS_RE.sub("", _TOKEN_RE.sub("", text or ""))


class ProtectMap:
    """占位符保护：protect() 替换敏感片段为 {n}；restore() 还原。"""

    def __init__(self) -> None:
        self.map: dict[str, str] = {}

    def protect(self, text: str, patterns: list[str]) -> str:
        for pat in patterns:
            def _sub(m: re.Match) -> str:
                token = "{" + str(len(self.map)) + "}"
                self.map[token] = m.group(0)
                return token
            text = re.sub(pat, _sub, text)
        return text

    def restore(self, text: str) -> str:
        def _sub(m: re.Match) -> str:
            return self.map.get(m.group(0), m.group(0))
        return _TOKEN_RE.sub(_sub, text)


def tokens_of(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def placeholders_ok(src: str, tgt: str) -> bool:
    """占位符数量与顺序校验（设计 §7.5 译文合理性校验）。"""
    return tokens_of(src) == tokens_of(tgt)


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise FormatError(f"疑似二进制文件：{path.name}")
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise FormatError(f"无法识别文件编码：{path.name}")


class IFormatAdapter:
    name = ""

    def parse(self, path: Path, opts: dict | None = None) -> DocumentModel:
        """opts：ruby_loose（宽松识别全角括号假名注音，默认关）。"""
        raise NotImplementedError

    def render(self, out_path: Path, model: DocumentModel,
               translations: dict[int, str], mode: str = "target",
               ruby_maps: dict[int, dict[str, str]] | None = None) -> None:
        """mode: target | bi_inter（段间交错） | bi_table（左右表格）
        ruby_maps: {seq: {token: 译注音}}（translate 策略的注音译文）。"""
        raise NotImplementedError
