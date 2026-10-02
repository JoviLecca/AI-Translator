"""英语界面覆盖率：app/ 里每一处中文字面量都必须有词条，否则英文界面会漏。

为什么用扫描而不是人工核对：界面文案有 600 多处，以后任何一次改动（改一个字、
加一句提示）都可能让英文界面重新冒出中文。这里用 AST 扫描把它变成回归测试：

- 普通字符串常量（跳过 docstring）直接要求 `tr()` 能翻成不含中文的英文；
- f-string 把每个插值位换成 `1` 拼成样本文案再要求 —— 所以词条要按
  `^已保存 (.+)$` 这类正则写，而不是只写静态整串；
- 白名单只留确实不该翻译的（正则、配置键、本地化标记），每条都要写理由，
  而且必须仍然存在（陈旧白名单会被这条测试抓出来）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from core import i18n

REPO = Path(__file__).resolve().parents[2]

APP_FILES: list[Path] = sorted((REPO / "app").rglob("*.py")) + [REPO / "main.py"]

#: 中日韩字符（含全角标点）——英文界面里不该出现。
CJK = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]")

#: 确实不该被翻译的字符串：键名、正则、语言标记。每条都必须有理由，
#: 由各词条模块的 `ALLOWED` 声明（`[(原文, 理由), ...]`），这里合并起来检查。
def _allowed() -> dict[str, str]:
    merged: dict[str, str] = {}
    for module_name in i18n.CATALOG_MODULES["en"]:
        module = __import__(module_name, fromlist=["ALLOWED"])
        for text, reason in getattr(module, "ALLOWED", ()):
            merged[text] = reason
    return merged


ALLOWED: dict[str, str] = {}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """收集 docstring 节点 —— 说明文字不是界面文案。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    ids.add(id(body[0].value))
    return ids


def _sample(node: ast.JoinedStr) -> str:
    """把 f-string 的插值位换成 `1`，拼出一条代表性文案。"""
    out = []
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            out.append(part.value)
        elif isinstance(part, ast.FormattedValue):
            out.append("1")
    return "".join(out)


def _joined_children(tree: ast.AST) -> set[int]:
    """f-string 内部的字面量片段。

    它们单独看不是界面文案（`f"已删除 {pid} 的密钥"` 的片段是「已删除 」），
    要求它们各自能翻会让词条里塞满碎片规则；整条的覆盖由 `_sample()` 拼出来的
    样本文案负责，所以这里把片段排除掉。
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant):
                    ids.add(id(part))
    return ids


def ui_strings(path: Path) -> list[tuple[int, str]]:
    """(行号, 文案) —— 该文件里所有可能显示给用户的中文字面量。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_nodes(tree) | _joined_children(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in skip:
                found.append((node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            found.append((node.lineno, _sample(node)))
    return found


@pytest.fixture(autouse=True)
def _restore_language():
    original = i18n.language()
    yield
    i18n.set_language(original)


@pytest.mark.parametrize("path", APP_FILES, ids=lambda p: p.relative_to(REPO).as_posix())
def test_every_ui_string_has_english(path: Path):
    i18n.set_language("en")
    allowed = _allowed()
    missing: list[str] = []
    for lineno, text in ui_strings(path):
        if not CJK.search(text) or text in allowed:
            continue
        translated = i18n.tr(text)
        if CJK.search(translated):
            missing.append(f"  {path.name}:{lineno}  {text!r} → {translated!r}")
    assert not missing, (
        f"{path.relative_to(REPO).as_posix()} 里还有 {len(missing)} 处文案没有英文词条"
        f"（英文界面会露出中文）：\n" + "\n".join(missing)
    )


def test_catalog_replacements_are_english():
    """词条自己不能留中文 —— 否则"翻了"跟没翻一样。"""
    i18n.set_language("en")
    bad: list[str] = []
    for module_name in i18n.CATALOG_MODULES["en"]:
        module = __import__(module_name, fromlist=["RULES"])
        for pattern, replacement in module.RULES:
            if CJK.search(replacement):
                bad.append(f"  {module_name}: {pattern!r} → {replacement!r}")
    assert not bad, "英语词条里出现中文：\n" + "\n".join(bad)


def test_whitelist_entries_still_exist():
    """陈旧白名单要清掉：白名单里的字符串必须仍能在仓库里找到。

    扫描面比"界面文案"宽 —— 词条模块也可以为 core/ 里给用户看的消息声明白名单
    （例如给模型的提示词、正则片段），只要那条字符串还在代码里就不是陈旧的。
    """
    i18n.set_language("en")
    allowed = _allowed()
    seen = {text for path in APP_FILES for _, text in ui_strings(path)}
    for pattern in ("core/**/*.py", "llm/**/*.py", "adapters/**/*.py", "storage/**/*.py"):
        for path in sorted(REPO.glob(pattern)):
            if "i18n" in path.parts:
                continue
            seen |= {text for _, text in ui_strings(path)}
    stale = [text for text in allowed if text not in seen]
    assert not stale, f"白名单里的字符串已经不在代码里了，请删掉：{stale}"


def test_scan_actually_finds_strings():
    """守住扫描本身：文件清单/取样坏了会让上面几条测试变成永远通过。"""
    total = sum(len(ui_strings(path)) for path in APP_FILES)
    assert total > 500, f"只扫到 {total} 条字符串，AST 扫描可能坏了"


def test_whitelist_entries_do_not_shadow_ui_strings():
    """白名单按**字符串**匹配，会连界面上的同一句话一起放过 —— 这是真踩过的坑。

    `译文` 曾经被 en_core 以「导出文件的固定列名」为由白名单化，于是校对页段表第 4 列
    的表头在英语界面里一直显示中文，而覆盖率测试照样全绿。所以这里反过来查一遍：
    凡是"同时也出现在 app/ 界面文案里"的白名单条目，必须真的能翻出英文 ——
    翻不出来就说明它遮住了界面词条（导出文件的内容不走 tr()，不需要白名单保护）。
    """
    i18n.set_language("en")
    allowed = _allowed()
    ui_texts = {text for path in APP_FILES for _, text in ui_strings(path)}
    shadowed = [
        text for text in allowed
        if text in ui_texts and i18n.tr(text) == text and text not in _SHADOW_OK
    ]
    assert not shadowed, (
        "这些白名单条目挡住了界面词条（英语界面会露出中文）："
        f"{shadowed} —— 界面文案请加词条，导出文件内容不需要白名单"
    )


#: 允许"同时也是界面文案片段"的白名单条目（各自都有整行/整串规则兜着，或本就不是界面文案）。
_SHADOW_OK = {
    " 个", "模型 ",            # settings_pages 本地服务清单行的片段，整行由 `^· …（…，模型 (\d+) 个）$` 覆盖
    "术语一致性报表.csv",       # 导出一致性报表时的建议文件名
}

