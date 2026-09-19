"""SRT（SubRip）字幕适配器（设计 §7.1）。

- **一个字幕条目（cue）= 一个 Block = 一个翻译段**：字幕是"时间轴 + 文本"的强
  绑定结构，cue 与译段必须 1:1，故此处**不调用 split_long**（超长 cue 保持原样
  送翻，译文按引擎返回原样写回）；
- 序号行与时间轴行逐字节原样保留（meta["index"] / meta["timing"]），render 原样写回；
- 空白行与任何无法识别为 cue 的内容都是 translatable=False 的 gap 块 → 未翻译时
  输出与输入逐字节相同（往返保真）；
- 文本先做振假名识别（html=True，先于标签保护，否则 `<ruby>` 会被保护正则吃掉），
  再用 ProtectMap 保护 ASS/SSA 覆盖码与 HTML 风格标签；
- 双语：bi_inter 为 cue 内「原文 + 译文」（字幕常见形式）；bi_table 无表格结构，
  与 txt 一致直接拒绝。
"""
from __future__ import annotations

import re
from pathlib import Path

from adapters.base import (
    Block, DocumentModel, FormatError, ProtectMap, effectively_empty, read_text,
)
from adapters.ruby import restore_ruby_mixed, rubyize_text
from adapters.txt_adapter import normalize_eol

# 时间轴行：毫秒分隔符 `,` / `.` 均接受；行尾允许位置参数（X1:.. Y1:.. 等）原样保留
_TIMING_RE = re.compile(
    r"^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}.*$")
_INDEX_RE = re.compile(r"^\d+$")
# 纯标记保护：ASS/SSA 覆盖码（`{\an8}`、`{\i1}`）+ HTML 风格标签（`<i>`、`<font …>`）
_PROTECT_PATTERS = [
    r"\{\\[^}]*\}",
    r"</?[a-zA-Z][^>]*>",
]


def _join_lines(lines: list[str]) -> str:
    """cue 内部行用 \\n 连接（保留内部换行，不合并成一行）。"""
    return "\n".join(lines)


class SrtAdapter:
    name = "srt"

    # ---------- 解析 ----------
    def parse(self, path: Path, opts: dict | None = None) -> DocumentModel:
        """解析为「cue 块 / gap 块」序列，块文本一律以 \\n 为行界。纯函数式、seq 可重放。

        保真的做法很直接：模型按行无损切块（空行与无法识别的内容都保留成 gap 块），
        每块自带原文 text；render 逐块拼回、再用骨架 EOL 连接 —— 未翻译时拼接结果与
        源文件逐字节相同（roundtrip 测试覆盖 CRLF / 尾部换行 / 连续空行）。
        """
        loose = bool((opts or {}).get("ruby_loose"))   # src_lang 与 SRT 无关，忽略
        text, eol = normalize_eol(read_text(path))
        had_trailing_nl = text.endswith("\n")
        lines = text.split("\n")       # 块文本一律以 \n 为行界，写出时再套骨架 EOL
        if had_trailing_nl:
            lines = lines[:-1]        # 尾部换行由 skeleton 记录，避免重复计入

        n = len(lines)
        blocks: list[Block] = []
        seq = 0
        i = 0
        while i < n:
            # ① gap：空白行（含尾行）或无法识别为 cue 的内容，原样透传
            if not lines[i].strip():
                blocks.append(Block(seq=seq, text=lines[i], translatable=False,
                                    meta={"kind": "gap"}))
                seq += 1
                i += 1
                continue
            # ② cue：可选的序号行（缺失时时间轴行直接是首行）
            index, timing_at = "", i
            if _INDEX_RE.match(lines[i]) and i + 1 < n and _TIMING_RE.match(lines[i + 1]):
                index, timing_at = lines[i], i + 1
            if not _TIMING_RE.match(lines[timing_at]):
                blocks.append(Block(seq=seq, text=lines[i], translatable=False,
                                    meta={"kind": "gap"}))
                seq += 1
                i += 1
                continue

            timing = lines[timing_at]
            j = timing_at + 1
            cue_lines: list[str] = []
            while j < n:
                if not lines[j].strip():
                    break                                   # 空行 → cue 结束
                if _TIMING_RE.match(lines[j]):
                    break                                   # 容错：缺空行的下一个 cue
                if (_INDEX_RE.match(lines[j]) and j + 1 < n
                        and _TIMING_RE.match(lines[j + 1])):
                    break                                   # 下一个 cue 的序号行
                cue_lines.append(lines[j])
                j += 1

            src_text = _join_lines(cue_lines)               # 逐行原始形态（\\n 连接）
            # 先振假名、后标记保护：顺序不可调换 —— `<ruby>` 必须先被 rubyize_text
            # 识别成注音槽，否则会被下面的 HTML 标签保护正则整体吃掉，注音当正文翻。
            flat, ruby = rubyize_text(src_text, loose=loose, html=True)
            pm = ProtectMap()
            flat = pm.protect(flat, _PROTECT_PATTERS)
            if effectively_empty(flat):
                # 只剩占位符（如 `{\an8}`）/空白 → 不进入翻译队列，原文透传
                blocks.append(Block(
                    seq=seq, text=src_text, translatable=False, is_heading=False,
                    meta={"kind": "cue", "index": index, "timing": timing,
                          "ph": pm.map, "ruby": ruby, "empty": True}))
            else:
                blocks.append(Block(
                    seq=seq, text=flat, translatable=True, is_heading=False,
                    meta={"kind": "cue", "index": index, "timing": timing,
                          "ph": pm.map, "ruby": ruby}))
            seq += 1
            i = j

        model = DocumentModel(path=path, fmt="srt", blocks=blocks)
        model.skeleton = {"eol": eol, "had_trailing_nl": had_trailing_nl}
        return model

    # ---------- 渲染 ----------
    @staticmethod
    def _restore(block: Block, text: str) -> str:
        """占位符还原（挂回该块的 ProtectMap）。"""
        pm = ProtectMap()
        pm.map = dict(block.meta.get("ph") or {})
        return pm.restore(text)

    def render(self, out_path: Path, model: DocumentModel,
               translations: dict[int, str], mode: str = "target",
               ruby_maps: dict[int, dict[str, str]] | None = None) -> None:
        if mode not in ("target", "bi_inter"):
            raise FormatError("srt 仅支持 target / bi_inter 模式")   # 字幕无表格结构
        ruby_maps = ruby_maps or {}
        skel = model.skeleton or {}
        eol = skel.get("eol", "\n")   # 块内换行也套骨架 EOL，避免 CRLF 源混行尾

        pieces: list[str] = []
        for b in model.blocks:
            if b.meta.get("kind") != "cue":
                pieces.append(b.text)      # gap（空白行/无法识别内容）：原样透传
                continue
            # cue 块：序号行 / 时间轴行原样写回，三者之间用 \n 连接
            head = [b.meta.get("index", ""), b.meta.get("timing", "")]   # 序号缺失时为空串
            ruby = b.meta.get("ruby")
            if not b.translatable:
                # 只剩标记/空白：原文透传。**头两行必须一起写回**，否则 cue 结构与
                # 时间轴丢失（roundtrip 会挂）。
                # 空串必须过滤：序号行缺失时 head[0] 是 ""，不过滤会多出一个前导
                # 空行、把时间轴挤到下一行；cue 的文本行按构造不含空行，不会丢内容。
                body = (b.text or "").split("\n")
                pieces.append(eol.join(x for x in head + body if x))
                continue
            src_lines = (b.text or "").split("\n")     # 送翻文本以 \n 连接各行
            tgt = translations.get(b.seq, b.text)
            # 引擎译文可能带 \r\n，先归一化再按行还原，避免混入 \r
            tgt_lines = normalize_eol(self._restore(b, tgt) or "")[0].split("\n")
            rt_map = ruby_maps.get(b.seq) or {}
            # 译文一路才带译注音；原文一路不传 rt_map（保留原假名）
            body = [restore_ruby_mixed(x, ruby, rt_map) for x in tgt_lines]
            if mode == "bi_inter":
                # cue 内「原文 + 译文」（字幕常见双语形式）：逐行交错，译文行不足时补原文行
                src_lines = [restore_ruby_mixed(self._restore(b, x), ruby)
                             for x in src_lines]
                body = [y for pair in zip(src_lines, body) for y in pair]
                if len(src_lines) > len(tgt_lines):
                    body += src_lines[len(tgt_lines):]
            pieces.append(eol.join(x for x in head + body if x))

        text = eol.join(pieces)
        if skel.get("had_trailing_nl"):
            text += eol
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(text.encode("utf-8"))
