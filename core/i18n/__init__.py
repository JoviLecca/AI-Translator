"""界面多语言：中英切换 + 英语界面（用户反馈：设置里要能切换 UI 语言）。

为什么这样做（设计取舍）：

- app/ 里的界面文案有 600 多处（含 f-string 拼出来的动态文案），逐个包一层
  `tr()` 改动面太大、也最容易漏；这里改在**显示层**翻译：`install_message_translation()`
  把 Qt 的文本 setter（`QLabel.setText`、`QMessageBox.information` 等）包一层，
  任何时刻任何地方设置的文本都会经过 `tr()` —— 动态文案（保存状态、术语提示、
  页脚计数）因此不需要逐个接钩子。
- `tr()` 只翻"认识的"字符串：先查精确词条（O(1)），再按正则规则替换，都不命中就
  原样返回 —— 模型名、URL、用户原文/译文即使被送到 `tr()` 也不会被改坏。
- 中文是默认语言，`tr()` 直接短路返回：行为与本次改动之前完全一致。
- 词条按模块分片放在 `en_*.py`：`RULES = [(pattern, replacement), ...]`，
  按 `re.sub` 语义替换，整串用 `^…$` 锚定（按 `re.MULTILINE` 编译，所以整串规则
  也能命中多行文案里的某一行），先匹配先赢，多轮扫描让组合文案逐层翻干净。
  `tests/unit/test_i18n_coverage.py` 用 AST 扫描 app/ 的中文字面量来保证不漏。
- **写规则的两条硬约束**（踩过）：① 每轮只应用"第一条能改动文本的规则"，而模块顺序
  就是优先级 —— 宽规则会**抢走**别的模块负责的整串词条（曾经一条"把 `X（Y）` 的全角
  括号换成半角"的规则把 `默认上下文滑窗段数（前 N + 后 N）` 改了一半，害得针对它的
  整串词条永远轮不上）。只写自己模块负责的文案，别做**标点/格式整形**。② 规则末尾
  不要锚换行：`^某行：\n$` 匹配不了"拼起来的长文案"里的那一行，`_rule_variants()`
  会自动补一条去掉换行的变体，但词条本身也别依赖它。
"""
from __future__ import annotations

import importlib
import re

DEFAULT_LANGUAGE = "zh-CN"

#: 界面语言：(代码, 显示名)。显示名不翻译 —— 语言名永远用它的母语写法。
UI_LANGUAGES: list[tuple[str, str]] = [("zh-CN", "简体中文"), ("en", "English")]

#: 每种语言由哪些词条模块拼成（顺序即优先级，先匹配先赢）。
CATALOG_MODULES: dict[str, tuple[str, ...]] = {
    "en": (
        "core.i18n.en_core",
        "core.i18n.en_settings",
        "core.i18n.en_workbench",
        "core.i18n.en_review",
    ),
}

_current = DEFAULT_LANGUAGE
_compiled: dict[str, tuple[dict[str, str], list[tuple[str, re.Pattern, str]]]] = {}

#: 只有含中日韩字符的文本才可能命中规则：英文/模型名/URL 直接短路，
#: 省掉几百条规则的匹配开销（表格刷新会调用几千次）。
_CJK = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]")

#: 规则里若存在字面锚点（如 `^已保存 (.+)$` →「已保存」），要求文本里先出现它，
#: 命中不了就不必跑正则。500 条规则下这一步是数量级的差别。
_META = re.compile(r"[.^$*+?()\[\]{}|]")


def _anchor(pattern: str) -> str:
    """取出规则里的字面前缀，用作廉价预筛。"""
    out: list[str] = []
    i = 1 if pattern.startswith("^") else 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            nxt = pattern[i + 1] if i + 1 < len(pattern) else ""
            # `\n` `\t` `\d` `\w` `\s` `\1` 都不是字面量，遇到就停；
            # 只有 `\+` `\(` 这类转义标点才能进锚点（写错会让整条规则被预筛掉）。
            if not nxt or nxt.isalnum():
                break
            out.append(nxt)
            i += 2
            continue
        if ch in ".^$*+?()[]{}|":
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _is_exact(pattern: str) -> bool:
    """整串字面量（`^已确认$`）走 dict，比正则快一个量级。"""
    if not (pattern.startswith("^") and pattern.endswith("$")):
        return False
    body = pattern[1:-1]
    return not _META.search(body) and "\\" not in body


def _rule_variants(pattern: str, replacement: str) -> list[tuple[str, str]]:
    """规则本体 + 一条"末尾不锚换行"的变体（连替换串里的换行一起去掉）。

    词条按源码字面量写：`("^检测到以下本地服务：\\n$", "Detected…\\n")` 末尾那个换行
    只表示"这行后面还有内容"。可拼出来的长文案里它后面接的是别的行，`\\n$`（换行 +
    行尾锚）就永远匹配不上 —— 变体去掉末尾换行、靠 MULTILINE 的 `$` 锚行尾，
    换行由文案自己保留，字面量与拼装文案两种形态都能命中。
    """
    out = [(pattern, replacement)]
    for tail in ("\\r\\n$", "\\n$", "\r\n$", "\n$"):
        if pattern.endswith(tail):
            body = pattern[: -len(tail)] + "$"
            text = replacement[:-2] if replacement.endswith("\r\n") else replacement
            if text.endswith("\n"):
                text = text[:-1]
            out.append((body, text))
            break
    return out


def _catalog(code: str):
    cached = _compiled.get(code)
    if cached is not None:
        return cached
    exact: dict[str, str] = {}
    rules: list[tuple[str, re.Pattern, str]] = []
    for mod_name in CATALOG_MODULES.get(code, ()):
        module = importlib.import_module(mod_name)
        for pattern, replacement in getattr(module, "RULES", ()):
            if _is_exact(pattern):
                body = pattern[1:-1]
                exact[body] = replacement
                stripped = body.rstrip("\r\n")
                if stripped != body:                     # 整串、但末尾不带换行的形态
                    text = replacement[:-2] if replacement.endswith("\r\n") else replacement
                    exact[stripped] = text[:-1] if text.endswith("\n") else text
                # 整串词条同时进规则表：多行文案（弹窗正文）里的**某一行**也可能是
                # 一条整串词条，此时 dict 查不到，得靠按行锚定的正则命中。
                raw = "^" + re.escape(body) + "$"
            else:
                raw = pattern
            for one, text in _rule_variants(raw, replacement):
                rules.append((_anchor(one), re.compile(one, re.MULTILINE), text))
    cached = (exact, rules)
    _compiled[code] = cached
    return cached


def language() -> str:
    """当前界面语言代码。"""
    return _current


def normalize_language(code: str | None) -> str:
    """把外部传入的语言写法（`en-US`、`EN`、`zh`…）收敛到我们支持的两种。"""
    if not code:
        return DEFAULT_LANGUAGE
    low = str(code).replace("_", "-").strip().lower()
    return "en" if low.startswith("en") else DEFAULT_LANGUAGE


def set_language(code: str | None) -> str:
    """切换界面语言，返回收敛后的代码。已生效的控件需要重建界面才会变（见 MainWindow.retranslate_ui）。"""
    global _current
    _current = normalize_language(code)
    return _current


def language_name(code: str | None = None) -> str:
    wanted = normalize_language(code or _current)
    for value, label in UI_LANGUAGES:
        if value == wanted:
            return label
    return wanted


#: 组合文案（弹窗 = 前缀 + 若干行 + 问句、`· 名称：地址（状态，模型数）` 这类拼出来的行）
#: 需要多条规则接力：一轮替换完再从头扫一遍，几轮就收敛，所以给个上限。
_MAX_PASSES = 4


def tr(text):
    """翻译一条界面文案。不认识就原样返回 —— 这是整个方案的安全底线。

    规则按 `re.MULTILINE` 编译：`^…$` 既匹配整串，也匹配多行文案里的某一行，
    所以拼出来的弹窗由多条规则分别命中；每轮替换后从头重扫（最多 `_MAX_PASSES`
    轮），组合文案因此能逐层翻干净。
    """
    if _current == DEFAULT_LANGUAGE or not isinstance(text, str) or not text:
        return text
    if not _CJK.search(text):
        return text
    exact, rules = _catalog(_current)
    hit = exact.get(text)
    if hit is not None:
        return hit
    out = text
    for _ in range(_MAX_PASSES):
        if not _CJK.search(out):
            break
        changed = False
        for anchor, pattern, replacement in rules:
            if anchor and anchor not in out:
                continue
            new = pattern.sub(replacement, out)
            if new != out:
                out = new
                changed = True
                break
        if not changed:
            break
    return out


def tr_all(items):
    """按顺序翻译一串文案（表头、下拉项、filters）。"""
    return [tr(item) if isinstance(item, str) else item for item in items]


def reset_cache() -> None:
    """丢掉已编译词条（测试改了词条模块、或想验证重复 set_language 幂等时用）。"""
    _compiled.clear()


# --- 显示层接管 ---------------------------------------------------------

#: (类, 方法名, 需要翻译的位置参数下标, 其中哪些位置是"一串文案")
#: 位置下标按"整份实参"算（方法都按 obj.method(...) 调用，下标 0 是 self）。
_TEXT_SETTERS: list[tuple[str, str, tuple[int, ...], tuple[int, ...]]] = [
    ("PySide6.QtWidgets:QLabel", "setText", (1,), ()),
    ("PySide6.QtWidgets:QAbstractButton", "setText", (1,), ()),
    ("PySide6.QtWidgets:QGroupBox", "setTitle", (1,), ()),
    ("PySide6.QtWidgets:QWidget", "setWindowTitle", (1,), ()),
    ("PySide6.QtWidgets:QWidget", "setToolTip", (1,), ()),
    ("PySide6.QtWidgets:QWidget", "setStatusTip", (1,), ()),
    ("PySide6.QtWidgets:QWidget", "setWhatsThis", (1,), ()),
    ("PySide6.QtWidgets:QLineEdit", "setPlaceholderText", (1,), ()),
    ("PySide6.QtWidgets:QTextEdit", "setPlaceholderText", (1,), ()),
    ("PySide6.QtWidgets:QPlainTextEdit", "setPlaceholderText", (1,), ()),
    ("PySide6.QtWidgets:QComboBox", "addItem", (1,), ()),
    ("PySide6.QtWidgets:QComboBox", "insertItem", (2,), ()),
    ("PySide6.QtWidgets:QComboBox", "setItemText", (2,), ()),
    ("PySide6.QtWidgets:QComboBox", "addItems", (1,), (1,)),
    ("PySide6.QtWidgets:QComboBox", "insertItems", (2,), (2,)),
    ("PySide6.QtWidgets:QTabWidget", "addTab", (2,), ()),
    ("PySide6.QtWidgets:QTabWidget", "insertTab", (3,), ()),
    ("PySide6.QtWidgets:QTabWidget", "setTabText", (2,), ()),
    ("PySide6.QtWidgets:QTableWidget", "setHorizontalHeaderLabels", (1,), (1,)),
    ("PySide6.QtWidgets:QTableWidget", "setVerticalHeaderLabels", (1,), (1,)),
    ("PySide6.QtWidgets:QTableWidgetItem", "setText", (1,), ()),
    ("PySide6.QtWidgets:QListWidgetItem", "setText", (1,), ()),
    ("PySide6.QtWidgets:QTreeWidgetItem", "setText", (2,), ()),
    ("PySide6.QtGui:QAction", "setText", (1,), ()),
    ("PySide6.QtWidgets:QMenu", "setTitle", (1,), ()),
    ("PySide6.QtWidgets:QStatusBar", "showMessage", (1,), ()),
    ("PySide6.QtWidgets:QWizardPage", "setTitle", (1,), ()),
    ("PySide6.QtWidgets:QWizardPage", "setSubTitle", (1,), ()),
    # 弹窗：静态便捷方法覆盖了仓库里 58 处 QMessageBox 调用
    ("PySide6.QtWidgets:QMessageBox", "information", (1, 2), ()),
    ("PySide6.QtWidgets:QMessageBox", "warning", (1, 2), ()),
    ("PySide6.QtWidgets:QMessageBox", "critical", (1, 2), ()),
    ("PySide6.QtWidgets:QMessageBox", "question", (1, 2), ()),
    ("PySide6.QtWidgets:QMessageBox", "about", (1, 2), ()),
    ("PySide6.QtWidgets:QMessageBox", "setText", (1,), ()),
    ("PySide6.QtWidgets:QMessageBox", "setInformativeText", (1,), ()),
    ("PySide6.QtWidgets:QFileDialog", "getOpenFileName", (1, 3), ()),
    ("PySide6.QtWidgets:QFileDialog", "getOpenFileNames", (1, 3), ()),
    ("PySide6.QtWidgets:QFileDialog", "getSaveFileName", (1, 3), ()),
    ("PySide6.QtWidgets:QFileDialog", "getExistingDirectory", (1,), ()),
    ("PySide6.QtWidgets:QInputDialog", "getText", (1, 2), ()),
    ("PySide6.QtWidgets:QInputDialog", "getItem", (1, 2, 3), (3,)),
    ("PySide6.QtWidgets:QInputDialog", "getInt", (1, 2), ()),
    ("PySide6.QtWidgets:QInputDialog", "getDouble", (1, 2), ()),
    # 布局/容器的便利方法：文案由 C++ 侧再新建控件承载，Python 层看不到那个控件，
    # 只能在入口处翻（仓库里 20 处 `QFormLayout.addRow("名称", …)` 就是这一类）。
    ("PySide6.QtWidgets:QFormLayout", "addRow", (1,), ()),
    ("PySide6.QtWidgets:QFormLayout", "insertRow", (2,), ()),
    ("PySide6.QtWidgets:QListWidget", "addItem", (1,), ()),
    ("PySide6.QtWidgets:QListWidget", "insertItem", (2,), ()),
    ("PySide6.QtWidgets:QListWidget", "addItems", (1,), (1,)),
    ("PySide6.QtWidgets:QDialogButtonBox", "addButton", (1,), ()),
    ("PySide6.QtWidgets:QMessageBox", "addButton", (1,), ()),
    ("PySide6.QtWidgets:QMenu", "addAction", (1,), ()),
    ("PySide6.QtWidgets:QMenu", "addMenu", (1,), ()),
    ("PySide6.QtWidgets:QMenuBar", "addMenu", (1,), ()),
    ("PySide6.QtWidgets:QToolBar", "addAction", (1,), ()),
]

#: 文本属性用关键字调用的情形（`setText(text="…")`）。
_TEXT_KWARGS = ("text", "title", "label", "caption", "message", "informativeText")

#: 构造器直接吃文案的条目类（`QListWidgetItem(name)`、`QTableWidgetItem(text)`）。
_ITEM_CLASSES = (
    "PySide6.QtWidgets:QListWidgetItem",
    "PySide6.QtWidgets:QTableWidgetItem",
    "PySide6.QtWidgets:QTreeWidgetItem",
)

#: 构造器第一个参数就是显示文案的控件类（`QLabel("名称")`、`QPushButton("保存设置")`、
#: `QGroupBox("项目信息")`）。这些位置不经 setText，只包装 setter 会漏掉整片界面 ——
#: 实测英语界面里「本地模型」「一键添加」「保存设置」等 99 处属于这一类。
_WIDGET_CTORS = (
    "PySide6.QtWidgets:QLabel",
    "PySide6.QtWidgets:QPushButton",
    "PySide6.QtWidgets:QCheckBox",
    "PySide6.QtWidgets:QRadioButton",
    "PySide6.QtWidgets:QToolButton",
    "PySide6.QtWidgets:QCommandLinkButton",
    "PySide6.QtWidgets:QGroupBox",
    "PySide6.QtGui:QAction",
)

_wrapped_names: list[str] = []


def _resolve(path: str):
    module_name, _, attr = path.partition(":")
    module = importlib.import_module(module_name)
    return module, getattr(module, attr)


def _translate_arg(value, sequence: bool):
    if sequence and not isinstance(value, (str, bytes)):
        # `QComboBox.addItems(STYLE_PRESETS.values())` 这类传的是 dict_values，
        # 不是 list/tuple —— 按可迭代对象统一处理（重建为 list，顺序不变）。
        if isinstance(value, (list, tuple)):
            return type(value)(tr(item) if isinstance(item, str) else item for item in value)
        try:
            items = list(value)
        except TypeError:
            return value
        return [tr(item) if isinstance(item, str) else item for item in items]
    return tr(value) if isinstance(value, str) else value


def _wrap_method(cls, name: str, positions: tuple[int, ...], sequences: tuple[int, ...]) -> bool:
    orig = getattr(cls, name, None)
    if orig is None or getattr(orig, "_i18n_wrapped", False):
        return False

    def wrapper(*args, **kwargs):
        if _current != DEFAULT_LANGUAGE:
            args = tuple(
                _translate_arg(arg, index in sequences) if index in positions else arg
                for index, arg in enumerate(args)
            )
            kwargs = {
                key: (_translate_arg(value, False) if key in _TEXT_KWARGS else value)
                for key, value in kwargs.items()
            }
        return orig(*args, **kwargs)

    wrapper._i18n_wrapped = True  # type: ignore[attr-defined]
    wrapper.__name__ = name
    wrapper.__doc__ = getattr(orig, "__doc__", None)
    setattr(cls, name, wrapper)
    return True


def _wrap_item_init(cls) -> bool:
    """构造器直接给文案的类（`QListWidgetItem("项目管理")`、`QLabel("名称")`）也要翻到。

    只翻字符串实参：`tr()` 只认词条，用户数据（模型名、URL、原文）不会被改坏。
    """
    orig = cls.__init__
    if getattr(orig, "_i18n_wrapped", False):
        return False

    def wrapper(*args, **kwargs):
        if _current != DEFAULT_LANGUAGE:
            args = tuple(tr(arg) if isinstance(arg, str) else arg for arg in args)
            kwargs = {
                key: (tr(value) if key in ("text", "title") and isinstance(value, str) else value)
                for key, value in kwargs.items()
            }
        return orig(*args, **kwargs)

    wrapper._i18n_wrapped = True  # type: ignore[attr-defined]
    wrapper.__name__ = "__init__"
    setattr(cls, "__init__", wrapper)
    return True


def install_message_translation() -> list[str]:
    """把 Qt 的文本 setter 接管过来，返回被包装的方法名（便于文档/测试核对）。

    幂等：重复调用不会把包装再套一层（靠 `_i18n_wrapped` 标记）。
    PySide6 只在真正用到时才导入，core/ 的纯逻辑测试不必拉起 Qt。
    """
    if _wrapped_names:
        return list(_wrapped_names)
    for path, name, positions, sequences in _TEXT_SETTERS:
        _, cls = _resolve(path)
        if _wrap_method(cls, name, positions, sequences):
            _wrapped_names.append(f"{cls.__name__}.{name}")
    for path in _ITEM_CLASSES:
        _, cls = _resolve(path)
        if _wrap_item_init(cls):
            _wrapped_names.append(f"{cls.__name__}.__init__")
    for path in _WIDGET_CTORS:
        _, cls = _resolve(path)
        if _wrap_item_init(cls):
            _wrapped_names.append(f"{cls.__name__}.__init__")
    return list(_wrapped_names)


def wrapped_names() -> list[str]:
    """已包装的入口（未安装时为空）。"""
    return list(_wrapped_names)
