"""SRT 字幕适配器测试（设计 §7.1）。

沙箱说明：本环境的 pytest `tmp_path` fixture 会在系统临时目录建目录并被拒
（`PermissionError: ...pytest-of-ShawnFu`），故统一改用项目内临时目录
`_tmp_srt_test/<用例名>_<随机>/`（不用 tempfile.mkdtemp —— 它会设受限权限，
导致沙箱内无法在其下继续建子目录）。若在正常环境跑，把 `case_dir(name)` 换成
pytest 的 `tmp_path` 即可，用例逻辑不变。
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from adapters.base import FormatError
from adapters.srt_adapter import SrtAdapter

ROOT = Path(__file__).resolve().parents[2]
TMP_ROOT = ROOT / "_tmp_srt_test"


def case_dir(name: str) -> Path:
    """项目内临时目录（见模块 docstring 的沙箱说明）。"""
    d = TMP_ROOT / f"{name}_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cleanup(d: Path) -> None:
    shutil.rmtree(d, ignore_errors=True)
    # 顺手清掉临时根目录，避免跑完测试在工作区留下空目录（.gitignore 已兜底）
    try:
        if TMP_ROOT.exists() and not any(TMP_ROOT.iterdir()):
            TMP_ROOT.rmdir()
    except OSError:
        pass


def write_srt(d: Path, name: str, text: str, newline: str = "\n") -> Path:
    p = d / name
    p.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return p


BASIC = (
    "1\n00:00:01,000 --> 00:00:04,000\n第一条字幕\n可能是第二行\n"
    "\n"
    "2\n00:00:05,500 --> 00:00:08,000\n第二条字幕\n"
)

# 第 2 条只剩 ASS 覆盖码（无正文）→ 应判为不可译，且必须占一个 seq
TAGS = (
    "1\n00:00:01,000 --> 00:00:04,000\n{\\an8}<i>斜体</i>文本\n"
    "\n"
    "2\n00:00:05,000 --> 00:00:06,000\n{\\an8}\n"
)

NO_INDEX = (
    "00:00:01,000 --> 00:00:04,000\n第一条字幕\n"
    "\n"
    "00:00:05,500 --> 00:00:08,000\n第二条字幕\n"
)

# 无序号行 + 第一条 cue 只剩 ASS 覆盖码（不可译）：两条易错路径同时出现
NO_INDEX_EMPTY = (
    "00:00:01,000 --> 00:00:04,000\n{\\an8}\n"
    "\n"
    "2\n00:00:05,500 --> 00:00:08,000\n正文\n"
)


def test_srt_parse_cues():
    """解析：一个 cue 一块，序号/时间轴原样保留，多行文本不被合并。"""
    d = case_dir("parse")
    try:
        src = write_srt(d, "a.srt", BASIC)
        model = SrtAdapter().parse(src)
        cues = [b for b in model.blocks if b.meta.get("kind") == "cue"]
        assert len(cues) == 2
        assert [b.meta["index"] for b in cues] == ["1", "2"]
        assert cues[0].meta["timing"] == "00:00:01,000 --> 00:00:04,000"
        assert cues[1].meta["timing"] == "00:00:05,500 --> 00:00:08,000"
        # cue 的多行文本保留内部换行（不合并成一行）
        assert cues[0].text == "第一条字幕\n可能是第二行"
        assert cues[0].translatable
        assert all(not b.is_heading for b in model.blocks)
        # 空行是 gap 块，也占一个 seq（不可译块不跳过 seq）
        assert any(b.meta.get("kind") == "gap" and not b.translatable for b in model.blocks)
        assert [b.seq for b in model.blocks] == list(range(len(model.blocks)))
        assert model.skeleton == {"eol": "\n", "had_trailing_nl": True}
    finally:
        cleanup(d)


def test_srt_parse_seq_is_deterministic():
    """seq 必须可确定性重放：导出时会重新 parse 同一源文件再按 seq 对齐译文。"""
    d = case_dir("replay")
    try:
        src = write_srt(d, "a.srt", BASIC)
        m1 = SrtAdapter().parse(src)
        m2 = SrtAdapter().parse(src)
        assert [(b.seq, b.text, b.translatable, b.meta.get("kind")) for b in m1.blocks] \
            == [(b.seq, b.text, b.translatable, b.meta.get("kind")) for b in m2.blocks]
    finally:
        cleanup(d)


def test_srt_roundtrip_bytes():
    """往返保真：没有任何译文时输出与源文件逐字节相同（含空行 / CRLF / 末尾换行）。"""
    d = case_dir("roundtrip")
    try:
        cases = {
            "lf.srt": (BASIC, "\n"),
            "crlf.srt": (BASIC, "\r\n"),
            "no_trailing.srt": (BASIC.rstrip("\n"), "\n"),
            "blank_lines.srt": ("1\n00:00:01,000 --> 00:00:02,000\nA\n\n\n\n", "\n"),
            "garbage.srt": ("乱码行\n\n1\n00:00:01,000 --> 00:00:02,000\nA\n", "\n"),
            "dot_ms.srt": ("1\n00:00:01.000 --> 00:00:04.500\n点号毫秒\n", "\n"),
            "pos.srt": (
                "1\n00:00:01,000 --> 00:00:04,000 X1:100 X2:200 Y1:300 Y2:400\n位置参数\n",
                "\n"),
        }
        ad = SrtAdapter()
        for name, (text, nl) in cases.items():
            src = write_srt(d, name, text, nl)
            out = d / f"out_{name}"
            ad.render(out, ad.parse(src), {}, "target")
            assert out.read_bytes() == src.read_bytes(), name
    finally:
        cleanup(d)


def test_srt_render_target_keeps_index_and_timing():
    """target：只输出译文，序号行与时间轴行不变；无译文时回退原文。"""
    d = case_dir("target")
    try:
        src = write_srt(d, "a.srt", BASIC)
        ad = SrtAdapter()
        model = ad.parse(src)
        translations = {b.seq: "T:" + b.text for b in model.blocks if b.translatable}
        out = d / "out.srt"
        ad.render(out, model, translations, "target")
        assert out.read_bytes().decode("utf-8") == (
            "1\n00:00:01,000 --> 00:00:04,000\nT:第一条字幕\n可能是第二行\n"
            "\n"
            "2\n00:00:05,500 --> 00:00:08,000\nT:第二条字幕\n")
        out2 = d / "fallback.srt"
        ad.render(out2, model, {}, "target")
        assert out2.read_bytes() == src.read_bytes()
    finally:
        cleanup(d)


def test_srt_render_bi_inter():
    """bi_inter：cue 内先原文后译文（字幕常见双语形式），逐行交错。"""
    d = case_dir("bi")
    try:
        src = write_srt(d, "a.srt", BASIC)
        ad = SrtAdapter()
        model = ad.parse(src)
        translations = {b.seq: "T:" + b.text for b in model.blocks if b.translatable}
        out = d / "bi.srt"
        ad.render(out, model, translations, "bi_inter")
        assert out.read_bytes().decode("utf-8") == (
            "1\n00:00:01,000 --> 00:00:04,000\n第一条字幕\nT:第一条字幕\n"
            "可能是第二行\n可能是第二行\n"
            "\n"
            "2\n00:00:05,500 --> 00:00:08,000\n第二条字幕\nT:第二条字幕\n")
    finally:
        cleanup(d)


def test_srt_parse_without_index_line():
    """序号行缺失的 SRT 也能解析，并原样渲染回原文件。"""
    d = case_dir("noindex")
    try:
        src = write_srt(d, "a.srt", NO_INDEX)
        ad = SrtAdapter()
        model = ad.parse(src)
        cues = [b for b in model.blocks if b.meta.get("kind") == "cue"]
        assert len(cues) == 2
        assert all(b.meta["index"] == "" for b in cues)       # 无序号行
        assert cues[0].meta["timing"] == "00:00:01,000 --> 00:00:04,000"
        assert cues[0].text == "第一条字幕"
        out = d / "rt.srt"
        ad.render(out, model, {}, "target")
        assert out.read_bytes() == src.read_bytes()
        out2 = d / "t.srt"
        ad.render(out2, model,
                  {b.seq: "T:" + b.text for b in model.blocks if b.translatable}, "target")
        assert out2.read_bytes().decode("utf-8") == (
            "00:00:01,000 --> 00:00:04,000\nT:第一条字幕\n"
            "\n"
            "00:00:05,500 --> 00:00:08,000\nT:第二条字幕\n")
    finally:
        cleanup(d)


def test_srt_protects_ass_and_html_tags():
    """ASS 覆盖码与 HTML 标签被占位符保护：不进送翻文本，render 时还原。"""
    d = case_dir("tags")
    try:
        src = write_srt(d, "a.srt", TAGS)
        ad = SrtAdapter()
        model = ad.parse(src)
        cue = next(b for b in model.blocks if b.meta.get("kind") == "cue" and b.translatable)
        assert "\\an8" not in cue.text        # {\an8} 已保护
        assert "<i>" not in cue.text and "</i>" not in cue.text
        assert "文本" in cue.text
        # 覆盖码与 HTML 标签各自成占位符（顺序按文本出现先后）
        assert cue.text == "{0}{1}斜体{2}文本"
        assert cue.meta["ph"] == {"{0}": "{\\an8}", "{1}": "<i>", "{2}": "</i>"}
        # 原样送翻 → 标记完整还原
        out = d / "same.srt"
        ad.render(out, model, {cue.seq: cue.text}, "target")
        assert "{\\an8}<i>斜体</i>文本" in out.read_bytes().decode("utf-8")
        # 改写正文 → 标记仍在正确位置
        out2 = d / "rewrite.srt"
        ad.render(out2, model, {cue.seq: cue.text.replace("文本", "内容")}, "target")
        assert "{\\an8}<i>斜体</i>内容" in out2.read_bytes().decode("utf-8")

        # 单独的 HTML 标签（前面没有覆盖码）逐条保护
        src2 = write_srt(d, "b.srt",
                         "1\n00:00:01,000 --> 00:00:02,000\n<i>斜体</i>文本\n")
        m2 = ad.parse(src2)
        cue2 = next(b for b in m2.blocks if b.meta.get("kind") == "cue")
        assert sorted(cue2.meta["ph"].values()) == ["</i>", "<i>"]
        assert cue2.text == "{0}斜体{1}文本"
        out3 = d / "b_out.srt"
        ad.render(out3, m2, {cue2.seq: "{0}斜体{1}内容"}, "target")
        assert "<i>斜体</i>内容" in out3.read_bytes().decode("utf-8")
    finally:
        cleanup(d)


def test_srt_empty_cue_is_passthrough():
    """只剩覆盖码/占位符的 cue → effectively_empty → translatable=False，原文透传。"""
    d = case_dir("empty")
    try:
        src = write_srt(d, "a.srt", TAGS)
        ad = SrtAdapter()
        model = ad.parse(src)
        cues = [b for b in model.blocks if b.meta.get("kind") == "cue"]
        assert len(cues) == 2
        assert cues[0].translatable is True
        assert cues[1].translatable is False                   # 仅 {\an8}
        assert cues[1].seq == 2 and cues[1].text == "{\\an8}"    # 仍占 seq
        out = d / "t.srt"
        ad.render(out, model,
                  {b.seq: "T:" + b.text for b in model.blocks if b.translatable}, "target")
        got = out.read_bytes().decode("utf-8")
        # 不可译 cue 的序号行/时间轴/原文整体透传，且未被翻译
        # （不能直接断言 "T:{\\an8}" not in got：会撞上第 1 条译文里的 T:{\an8}<i>…）
        assert got == (
            "1\n00:00:01,000 --> 00:00:04,000\nT:{\\an8}<i>斜体</i>文本\n"
            "\n"
            "2\n00:00:05,000 --> 00:00:06,000\n{\\an8}\n")
        out2 = d / "rt.srt"
        ad.render(out2, model, {}, "target")
        assert out2.read_bytes() == src.read_bytes()
    finally:
        cleanup(d)


def test_srt_roundtrip_without_index_and_empty_cue():
    """回归：序号行缺失 + cue 只剩标记时不得多出前导空行（往返须逐字节相同）。"""
    d = case_dir("noindex_empty")
    try:
        src = write_srt(d, "a.srt", NO_INDEX_EMPTY)
        ad = SrtAdapter()
        model = ad.parse(src)
        out = d / "out.srt"
        ad.render(out, model, {}, "target")
        assert out.read_bytes() == src.read_bytes()

        # CRLF 源同样要逐字节还原
        src2 = write_srt(d, "b.srt", NO_INDEX_EMPTY, newline="\r\n")
        model2 = ad.parse(src2)
        out2 = d / "out2.srt"
        ad.render(out2, model2, {}, "target")
        assert out2.read_bytes() == src2.read_bytes()
    finally:
        cleanup(d)


def test_srt_bi_table_rejected():
    """bi_table：字幕没有表格结构，与 txt 一致直接拒绝。"""
    d = case_dir("bitable")
    try:
        src = write_srt(d, "a.srt", BASIC)
        ad = SrtAdapter()
        model = ad.parse(src)
        with pytest.raises(FormatError) as ei:
            ad.render(d / "t.srt", model, {}, "bi_table")
        assert "srt 仅支持" in str(ei.value)
    finally:
        cleanup(d)


def test_srt_inline_html_ruby_tokenized():
    """cue 内的 `<ruby>漢字<rt>かんじ</rt></ruby>` 被识别成注音槽，render 能还原。"""
    d = case_dir("ruby")
    try:
        src = write_srt(
            d, "a.srt",
            "1\n00:00:01,000 --> 00:00:04,000\n<ruby>漢字<rt>かんじ</rt></ruby>です\n")
        ad = SrtAdapter()
        model = ad.parse(src)
        cue = next(b for b in model.blocks if b.meta.get("kind") == "cue")
        # 基词保留在送翻文本，注音进注音槽：<rt> 与假名绝不进送翻文本
        assert cue.text == "漢字{r0}です"
        assert "<rt>" not in cue.text and "かんじ" not in cue.text
        entries = {e["token"]: e for e in cue.meta["ruby"]}
        assert entries["{r0}"]["rt"] == "かんじ" and entries["{r0}"]["style"] == "html"

        # 先振假名、后标签保护：顺序错则 <ruby> 被标签保护正则吃掉、注音被当正文翻
        out = d / "keep.srt"
        ad.render(out, model, {cue.seq: cue.text}, "target")
        assert "<ruby>漢字<rt>かんじ</rt></ruby>です" in out.read_bytes().decode("utf-8")
        out2 = d / "trans.srt"
        ad.render(out2, model, {cue.seq: cue.text}, "target",
                  ruby_maps={cue.seq: {"{r0}": "hàn zì"}})
        assert "<rt>hàn zì</rt>" in out2.read_bytes().decode("utf-8")
        out3 = d / "rt.srt"
        ad.render(out3, model, {}, "target")
        assert out3.read_bytes() == src.read_bytes()
    finally:
        cleanup(d)
