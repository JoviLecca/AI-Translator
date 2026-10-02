"""主窗体切页（反馈 2026-09-29 #7/#8）：侧栏选中要跟着走、切页后要强制重绘。

- #7 切页后能看到上一页的残影（表头/单元格）：`QAbstractScrollArea` 的 viewport 带
  `WA_OpaquePaintEvent`，重新显示时可能复用旧背景缓存，异步 `update()` 治不住；
- #8 打开项目后右侧已经切到工作台，左侧还高亮着「项目管理」（`goto()` 只切了页面栈）。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest                                              # noqa: E402
from PySide6.QtWidgets import (                            # noqa: E402
    QAbstractScrollArea, QApplication,
)

from app import main_window as mw                          # noqa: E402
from core import appconfig                                 # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


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


def test_initial_page_is_projects(win):
    """启动时停在「项目管理」，侧栏与页面栈一致。"""
    assert win.nav.currentRow() == 0 and win.stack.currentIndex() == 0
    assert win.nav.currentItem().text() == "项目管理"


def test_goto_syncs_sidebar_selection(win):
    """goto() 是程序化切页（打开项目后进工作台）—— 侧栏选中必须一起跟上。"""
    win.goto("项目工作台")
    assert win.stack.currentIndex() == 1
    assert win.nav.currentRow() == 1, "右侧已是工作台，侧栏不该还停在项目管理"
    assert win.nav.currentItem().text() == "项目工作台"

    win.goto("校对编辑器")
    assert win.stack.currentIndex() == 3 and win.nav.currentRow() == 3


def test_clicking_sidebar_switches_page(win):
    """反向：点侧栏 → 页面栈跟着切。"""
    win.nav.setCurrentRow(2)
    assert win.stack.currentIndex() == 2
    assert win.stack.currentWidget() is win.pages["翻译进度"]


def test_goto_does_not_recurse_or_reenter(win):
    """goto 同步侧栏时不能因为信号回环而重复进入页面（on_enter 只跑一次）。"""
    page = win.pages["项目工作台"]
    calls = []
    original = page.on_enter
    page.on_enter = lambda: (calls.append(1), original())[1]
    try:
        win.goto("项目工作台")
        win.goto("项目工作台")
    finally:
        page.on_enter = original
    assert len(calls) == 2, f"每次 goto 只应进入一次，实际 {len(calls)} 次"


def test_switch_repaints_target_page(win, monkeypatch):
    """每次切页都要对目标页做强制重绘（伪影的修法在这里）。"""
    seen = []
    monkeypatch.setattr(mw.MainWindow, "_repaint_page",
                        staticmethod(lambda w: seen.append(w) or 1))
    win.goto("校对编辑器")
    win.goto("翻译进度")
    pages_seen = [w for w in seen if w is not win.nav]
    assert pages_seen == [win.pages["校对编辑器"], win.pages["翻译进度"]]


def test_repaint_page_touches_every_scroll_area(win):
    """强制重绘要覆盖目标页内所有滚动区域（表格/文本区的 viewport 才是残影重灾区）。"""
    page = win.pages["项目工作台"]
    areas = page.findChildren(QAbstractScrollArea)
    assert areas, "工作台里应当有滚动区域，否则这条测试没有意义"
    assert mw.MainWindow._repaint_page(page) == len(areas) + 1


def test_switch_repaints_sidebar_too(win, monkeypatch):
    """侧栏本身也是 QAbstractScrollArea，切页时一起重绘（它的选中条也会留残影）。"""
    seen = []
    monkeypatch.setattr(mw.MainWindow, "_repaint_page",
                        staticmethod(lambda w: seen.append(w) or 1))
    win.goto("项目工作台")
    assert seen == [win.pages["项目工作台"], win.nav]
    monkeypatch.undo()
    # 侧栏里确实有可重绘的 viewport（否则上面那次调用没有意义）
    assert win.nav.viewport() is not None
    assert mw.MainWindow._repaint_page(win.nav) >= 2


def test_switch_after_open_project_keeps_sidebar_in_sync(win, tmp_path, qapp):
    """端到端复现用户场景：打开项目后右侧进工作台，左侧也必须是工作台。"""
    from core.project import Project
    proj = Project.create(tmp_path / "p", name="nav", src_lang="ja-JP",
                          tgt_lang="zh-CN", provider_id="mock")
    root = str(proj.root)
    proj.close()

    win.ctx.open_project(root)
    win.refresh_after_project()
    win.goto("项目工作台")
    qapp.processEvents()
    assert win.stack.currentIndex() == 1
    assert win.nav.currentRow() == 1
    win.ctx.close_project()
