"""界面多语言（用户反馈：设置里要能切 UI 语言、要有英语界面）。

分两层验证：

- `core/i18n` 的翻译机制：`tr()` 只翻"认识的"字符串，其余原样返回（安全底线）；
  规则按行锚定 + 多轮扫描，所以拼出来的多行弹窗也能逐层翻干净。
- 显示层接管 + 语言切换：`install_message_translation()` 包装 Qt 的文本 setter 后，
  任何时刻设置的文本都会经过 `tr()`；`MainWindow.retranslate_ui()` 就地重建界面，
  当前页保持不变、旧页面先 `on_leave()` 落库。

词条本身漏没漏由 `tests/unit/test_i18n_coverage.py` 用 AST 扫描保证。
"""
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest                                                     # noqa: E402
from PySide6.QtWidgets import (                                   # noqa: E402
    QApplication, QComboBox, QLabel, QListWidgetItem, QMessageBox,
    QTableWidget, QTableWidgetItem, QTabWidget, QWidget,
)

from app import main_window as mw                                 # noqa: E402
from core import appconfig, i18n                                  # noqa: E402

#: 多行组合文案：前缀 + 服务清单行 + 问句，由三条规则接力翻译。
DETECT_TEXT = (
    "检测到以下本地服务：\n"
    "· Ollama（本地）：http://127.0.0.1:11434/v1（可添加，模型 3 个）\n"
    "\n"
    "是否把其中 1 个加入 Provider 列表？"
)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    i18n.install_message_translation()      # 真实程序启动时也是这么接管的
    yield app


@pytest.fixture(autouse=True)
def chinese():
    """每条用例都从中文开始、回中文结束（语言是全局状态，别泄漏给别的文件）。"""
    i18n.set_language("zh-CN")
    yield
    i18n.set_language("zh-CN")


@pytest.fixture
def en():
    i18n.set_language("en")
    return i18n


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    """真实主窗体，但把全局配置重定向到临时目录（不碰用户 %APPDATA%）。"""
    monkeypatch.setattr(appconfig, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(appconfig, "CONFIG_PATH", tmp_path / "config.json")
    from app.context import AppContext
    w = mw.MainWindow(AppContext())
    w.resize(1280, 800)
    w.show()
    qapp.processEvents()
    yield w
    w.close()


# --- 语言设置与 tr() 机制 -------------------------------------------------

def test_default_language_is_chinese():
    assert i18n.language() == "zh-CN"
    assert i18n.language() == i18n.DEFAULT_LANGUAGE
    assert [code for code, _ in i18n.UI_LANGUAGES] == ["zh-CN", "en"]
    #: 语言名永远用母语写法，不进词条
    assert i18n.language_name("en") == "English"
    assert i18n.language_name("zh-CN") == "简体中文"
    #: 全局配置里要有 ui_language，否则新用户第一次打开设置页看不到当前语言
    from core import appconfig
    assert appconfig.DEFAULTS["ui_language"] == i18n.DEFAULT_LANGUAGE


@pytest.mark.parametrize("code,expected", [
    ("en", "en"), ("EN", "en"), ("en-US", "en"), ("en_US", "en"),
    ("zh", "zh-CN"), ("zh-CN", "zh-CN"), ("zh-TW", "zh-CN"),
    ("ja", "zh-CN"), ("", "zh-CN"), (None, "zh-CN"),
])
def test_normalize_language(code, expected):
    assert i18n.normalize_language(code) == expected


def test_chinese_mode_is_a_no_op(en_unused=None):
    """中文是默认语言：`tr()` 直接短路，行为与加多语言之前完全一致。"""
    for text in ("已确认", "获取到 3 个模型", DETECT_TEXT):
        assert i18n.tr(text) == text
    label = QLabel()
    label.setText("已确认")
    assert label.text() == "已确认"


def test_status_names_translated(en):
    assert en.tr("已确认") == "Confirmed"
    assert en.tr("机翻") == "MT"
    assert en.tr("就绪") == "Ready"


def test_dynamic_rules(en):
    assert en.tr("获取到 12 个模型") == "Fetched 12 models"
    assert en.tr("失败：404 not found") == "Failed: 404 not found"
    assert en.tr("设置已保存") == "Settings saved"


def test_multiline_composition_is_translated_line_by_line(en):
    out = en.tr(DETECT_TEXT)
    assert not en._CJK.search(out), out
    assert out.splitlines()[0] == "Detected these local services:"
    assert out.splitlines()[1] == "· Ollama: http://127.0.0.1:11434/v1 (can be added, 3 models)"
    assert out.splitlines()[-1] == "Add these 1 service(s) to the provider list?"


@pytest.mark.parametrize("text", [
    "qwen2.5:7b",                                        # 模型名
    "http://127.0.0.1:11434/v1",                         # 地址
    "sk-abcdef123456",                                   # 密钥
    "deepseek",                                          # Provider id
    "これは日本語の原文です。",                              # 日文原文
    "彼は（笑）と言った。",                                  # 用户原文里的全角括号不能被改
    "这是一段还没翻译的用户译文。",
    "已确认的原文是什么意思？",                              # 只是包含词条，不动它
])
def test_unknown_text_passes_through(en, text):
    assert en.tr(text) == text


@pytest.mark.parametrize("value", [None, 123, 4.5, b"bytes", ["已确认"]])
def test_non_string_passes_through(en, value):
    assert en.tr(value) is value


def test_tr_all(en):
    assert en.tr_all(["已确认", "qwen2.5:7b", 7]) == ["Confirmed", "qwen2.5:7b", 7]


# --- 显示层接管 -----------------------------------------------------------

def test_wrapping_is_installed_and_idempotent():
    first = i18n.install_message_translation()
    second = i18n.install_message_translation()
    assert first == second and len(first) >= 40
    for name in ("QLabel.setText", "QComboBox.addItem", "QMessageBox.information",
                 "QListWidgetItem.__init__", "QTableWidgetItem.__init__"):
        assert name in first
    assert i18n.wrapped_names() == first


def test_label_and_button_text_translated(en):
    label = QLabel()
    label.setText("已配置")
    assert label.text() == "Configured"
    label.setText("qwen2.5:7b")               # 数据不会被改坏
    assert label.text() == "qwen2.5:7b"


def test_item_constructors_translated(en):
    assert QListWidgetItem("项目管理").text() == "Projects"
    assert QTableWidgetItem("已确认").text() == "Confirmed"
    assert QTableWidgetItem("未配置").text() == "Not configured"
    assert QTableWidgetItem("deepseek").text() == "deepseek"


def test_combo_items_and_user_data(en):
    combo = QComboBox()
    combo.addItems(["未译", "qwen2.5:7b"])
    assert combo.itemText(0) == "Untranslated"
    assert combo.itemText(1) == "qwen2.5:7b"
    combo.addItem("已确认", "confirmed")       # userData 必须原样保留
    assert combo.itemText(2) == "Confirmed"
    assert combo.itemData(2) == "confirmed"
    combo.setItemText(2, "设置")
    assert combo.itemText(2) == "Settings"


def test_tabs_and_headers(en):
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "设置")
    assert tabs.tabText(0) == "Settings"
    table = QTableWidget(0, 2)
    table.setHorizontalHeaderLabels(["名称", "密钥"])
    assert table.horizontalHeaderItem(0).text() == "Name"
    assert table.horizontalHeaderItem(1).text() == "API key"


def test_messagebox_instance_texts(en):
    box = QMessageBox()
    box.setText("已确认")
    box.setInformativeText("密钥保存失败")
    assert box.text() == "Confirmed"
    assert box.informativeText() == "Could not save the API key"


def test_messagebox_static_calls_are_wrapped():
    """静态 `QMessageBox.information(...)` 也接得住（app/ 里有 50 多处）。"""
    assert "QMessageBox.information" in i18n.wrapped_names()
    assert "QMessageBox.question" in i18n.wrapped_names()


# --- 主窗体的语言切换 -----------------------------------------------------

def test_window_starts_in_chinese(win):
    assert win.windowTitle() == "AI 项目翻译器"
    assert win.nav.currentItem().text() == "项目管理"


def test_retranslate_rebuilds_ui_in_new_language(win, qapp):
    win.ctx.cfg["ui_language"] = "en"
    win.ctx.save_cfg()
    win.retranslate_ui()
    qapp.processEvents()
    assert i18n.language() == "en"
    assert len(win.pages) == 6
    assert win.nav.currentItem().text() == "Projects"
    assert win.windowTitle() == "AI Project Translator"

    win.ctx.cfg["ui_language"] = "zh-CN"
    win.retranslate_ui()
    qapp.processEvents()
    assert i18n.language() == "zh-CN"
    assert win.nav.currentItem().text() == "项目管理"
    assert win.windowTitle() == "AI 项目翻译器"


def test_retranslate_keeps_current_page_and_flushes_old_pages(win, qapp):
    win.goto("设置")
    old_page = win.pages["设置"]
    saved: list[str] = []
    old_page.on_leave = lambda: saved.append("on_leave")     # 页面钩子：切语言前先落库
    win.ctx.cfg["ui_language"] = "en"
    win.retranslate_ui()
    qapp.processEvents()
    assert saved == ["on_leave"]
    assert win.current_page_name() == "设置"
    assert win.pages["设置"] is not old_page
    assert win.nav.currentRow() == win.stack.currentIndex()


def test_retranslate_with_project_open_keeps_project(win, qapp, tmp_path):
    """真实场景：项目开着切语言 —— 界面整体重建后项目仍在、页面仍认得它。"""
    from core.project import Project
    proj = Project.create(tmp_path / "p", name="i18n", src_lang="ja-JP",
                          tgt_lang="zh-CN", provider_id="mock")
    doc = proj.db.upsert_document("source/a.md", "md", "h")
    proj.db.replace_segments(doc, [{"seq": 0, "text": "第一段の原文。",
                                    "is_heading": False, "translatable": True,
                                    "src_hash": "h0"}], proj.cfg_hash())
    root = str(proj.root)
    proj.close()

    win.ctx.open_project(root)
    win.refresh_after_project()
    win.goto("校对编辑器")
    qapp.processEvents()

    win.ctx.cfg["ui_language"] = "en"
    win.retranslate_ui()
    qapp.processEvents()

    assert win.ctx.project is not None, "切语言不该把项目关掉"
    assert len(win.pages) == 6
    assert win.current_page_name() == "校对编辑器"
    combo = win.pages["校对编辑器"].doc_combo
    assert combo.count() == 2, "重建后仍该认得这个文档（「全部文档」+ 文件）"
    assert combo.itemText(0) == "All documents" and combo.itemText(1) == "source/a.md"
    assert win.windowTitle() == "AI Project Translator"
    win.ctx.close_project()


def test_settings_language_combo_switches_immediately(win, qapp):
    win.goto("设置")
    qapp.processEvents()
    page = win.pages["设置"]
    assert [page.f_lang.itemData(i) for i in range(page.f_lang.count())] == ["zh-CN", "en"]
    assert page.f_lang.currentData() == "zh-CN"

    page.f_lang.setCurrentIndex(1)                # 选 English → 立即重建界面
    qapp.processEvents()
    assert i18n.language() == "en"
    assert win.ctx.cfg["ui_language"] == "en"
    assert win.windowTitle() == "AI Project Translator"
    assert win.pages["设置"].f_lang.currentData() == "en"     # 重建后下拉仍回显当前语言

    win.pages["设置"].f_lang.setCurrentIndex(0)   # 切回简体中文
    qapp.processEvents()
    assert i18n.language() == "zh-CN"
    assert win.ctx.cfg["ui_language"] == "zh-CN"
    assert win.windowTitle() == "AI 项目翻译器"


# --- 英语界面的完整性（离屏截图核对时发现：构造器给文案的控件不走 setText）---------
#
# 背景：只包装 setText 之类的 setter 会漏掉 `QLabel("名称")`、`QPushButton("保存设置")`
# 这类"构造器直接吃文案"的路径 —— 词条覆盖率测试看不出来（字符串确实有词条），
# 但界面上就是中文。所以这里做一次真实的控件树巡检。

_CJK = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]")

#: 语言名永远用母语写法（`简体中文` 是数据不是界面文案）
_ALLOWED_UI_TEXT = {"简体中文"}


def _collect_texts(root) -> list[tuple[str, str]]:
    """收集一棵控件树里所有会显示给用户的文本（含表头、下拉条目、占位符、提示）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QListWidget,
        QPlainTextEdit, QTableView, QTableWidget, QTabWidget, QTextEdit,
        QTreeWidget, QWidget,
    )

    out: list[tuple[str, str]] = []

    def add(source: str, text) -> None:
        if isinstance(text, str) and text.strip():
            out.append((source, text))

    add("window.windowTitle", root.windowTitle())
    add("statusBar.message", root.statusBar().currentMessage())
    for w in root.findChildren(QWidget):
        cls = type(w).__name__
        if isinstance(w, QLabel):
            add(f"{cls}.text", w.text())
        if isinstance(w, QAbstractButton):
            add(f"{cls}.text", w.text())
        if isinstance(w, QGroupBox):
            add(f"{cls}.title", w.title())
        if isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit)):
            add(f"{cls}.placeholderText", w.placeholderText())
        if isinstance(w, QComboBox):
            for i in range(w.count()):
                add(f"{cls}.item", w.itemText(i))
        if isinstance(w, QTabWidget):
            for i in range(w.count()):
                add(f"{cls}.tabText", w.tabText(i))
        if isinstance(w, QListWidget):
            for i in range(w.count()):
                add(f"{cls}.item", w.item(i).text())
        if isinstance(w, QTreeWidget):
            stack = [w.topLevelItem(i) for i in range(w.topLevelItemCount())]
            while stack:
                node = stack.pop()
                add(f"{cls}.item", node.text(0))
                stack.extend(node.child(i) for i in range(node.childCount()))
        if isinstance(w, QTableWidget):
            for c in range(w.columnCount()):
                header = w.horizontalHeaderItem(c)
                add(f"{cls}.header", header.text() if header is not None else None)
            for r in range(w.rowCount()):
                for c in range(w.columnCount()):
                    cell = w.item(r, c)
                    add(f"{cls}.cell", cell.text() if cell is not None else None)
        elif isinstance(w, QTableView):
            model = w.model()
            if model is not None:
                for c in range(model.columnCount()):
                    add(f"{cls}.header", model.headerData(
                        c, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole))
                for r in range(model.rowCount()):
                    for c in range(model.columnCount()):
                        add(f"{cls}.cell", model.data(
                            model.index(r, c), Qt.ItemDataRole.DisplayRole))
        add(f"{cls}.toolTip", w.toolTip())
    return out


def test_english_ui_has_no_chinese_left(win, qapp):
    """切到 English 后，六个页面里不应该再看到中文界面文案。"""
    win.ctx.cfg["ui_language"] = "en"
    win.retranslate_ui()
    qapp.processEvents()
    assert i18n.language() == "en"

    for name in list(win.pages):
        win.goto(name)
        qapp.processEvents()
        leftovers = [
            f"{src}: {text!r}" for src, text in _collect_texts(win)
            if _CJK.search(text) and text.strip() not in _ALLOWED_UI_TEXT
        ]
        assert not leftovers, f"「{name}」页仍是中文：{leftovers[:10]}"


def test_english_ui_covers_widget_constructors(en):
    """`QLabel("名称")` 这类构造器路径也要翻（界面上 99 处属于这一类）。"""
    for cls in ("QLabel", "QPushButton", "QCheckBox", "QGroupBox"):
        assert f"{cls}.__init__" in en.wrapped_names()
    assert QLabel("名称").text() == "Name"
    from PySide6.QtWidgets import QCheckBox, QGroupBox, QPushButton
    assert QPushButton("保存设置").text() == "Save settings"
    assert QCheckBox("允许编辑原文").text() == "Allow editing the source"
    assert QGroupBox("项目信息").title() == "Project info"

