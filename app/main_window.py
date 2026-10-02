"""主窗体：侧栏导航 + 页面栈（设计 §9 页面清单）。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractScrollArea, QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow,
    QStackedWidget, QWidget,
)

from app.context import AppContext
from app.pages.progress_page import ProgressPage
from app.pages.projects_page import ProjectsPage
from app.pages.review_page import ReviewPage
from app.pages.settings_pages import ProjectSettingsPage, SettingsPage
from app.pages.workbench_page import WorkbenchPage
from core import i18n


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        # 语言要在建界面之前定下来：翻译层接管的是"设置文本"的动作，
        # 已经写进控件的文本不会自己变（切换语言靠 retranslate_ui 重建，见下）。
        i18n.set_language(ctx.cfg.get("ui_language"))
        i18n.install_message_translation()
        self.resize(1280, 800)
        # 轻提示落到状态栏。修复：Bridge.toast 此前没有任何接收者，
        # Qt 信号无接收者时 emit 是 no-op → 全部操作提示被静默丢弃。
        # 只连一次：状态栏不参与重建（见 _build_ui）。
        self.ctx.bridge.toast.connect(self._on_toast)
        self._build_ui()

    def _build_ui(self) -> None:
        """搭出侧栏 + 页面栈。单独成方法是为了切换界面语言时就地重建（见 retranslate_ui）。

        注意 `self.pages` 的键（"项目管理"…）是**内部标识**，不翻译：
        `goto()`、`retranslate_ui()` 都靠它定位页面；显示在侧栏的那份文本
        由翻译层在 `QListWidgetItem(name)` 时翻掉。
        """
        # 重建时信号会重连：先断掉上一轮的槽，避免一次提示弹两遍。
        # 轻提示只连一次（见 __init__）——它不依赖被重建的控件。
        if getattr(self, "nav", None) is not None:
            try:
                self.nav.currentRowChanged.disconnect(self._switch)
            except (RuntimeError, TypeError):
                pass

        self.pages: dict[str, QWidget] = {
            "项目管理": ProjectsPage(self.ctx, self),
            "项目工作台": WorkbenchPage(self.ctx, self),
            "翻译进度": ProgressPage(self.ctx, self),
            "校对编辑器": ReviewPage(self.ctx, self),
            "项目设置": ProjectSettingsPage(self.ctx, self),
            "设置": SettingsPage(self.ctx, self),
        }

        nav = QListWidget()
        nav.setFixedWidth(140)
        for name in self.pages:
            nav.addItem(QListWidgetItem(name))
        self.nav = nav

        self.stack = QStackedWidget()
        self.stack.setAutoFillBackground(True)
        for name, page in self.pages.items():
            # 页面自绘背景：否则切换页面时可能残留上一页已绘制的内容（用户反馈）
            page.setAutoFillBackground(True)
            self.stack.addWidget(page)

        layout = QHBoxLayout()
        layout.addWidget(nav)
        layout.addWidget(self.stack, 1)
        holder = QWidget()
        holder.setLayout(layout)
        self.setCentralWidget(holder)

        # 标题也在这里设：切换语言会重建界面，标题得跟着换（见 retranslate_ui）
        self.setWindowTitle("AI 项目翻译器")
        nav.currentRowChanged.connect(self._switch)
        nav.setCurrentRow(0)
        self.statusBar().showMessage("就绪")

    def _on_toast(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 8000)

    def _switch(self, row: int) -> None:
        page = self.stack.widget(row)
        if hasattr(page, "on_enter"):
            page.on_enter()
        if self.nav.currentRow() != row:
            # 程序化切页（goto：打开项目后自动进工作台等）要把侧栏选中一起同步，
            # 否则右边已经是工作台、左边还高亮着「项目管理」（用户反馈）。
            # 阻塞信号避免 currentRowChanged 再回调进来绕一圈。
            self.nav.blockSignals(True)
            self.nav.setCurrentRow(row)
            self.nav.blockSignals(False)
        self.stack.setCurrentIndex(row)
        self._repaint_page(page)
        # 侧栏也在同一类残影的覆盖范围内（它同样是 QAbstractScrollArea）
        self._repaint_page(self.nav)

    @staticmethod
    def _repaint_page(page: QWidget) -> int:
        """切页后强制整页重绘，消掉上一页留下的残影（用户反馈）。

        为什么 update() 不够：QStackedWidget 里其它页面只是被隐藏，而
        `QAbstractScrollArea` 的 viewport 带 `WA_OpaquePaintEvent`（Qt 为省性能设的），
        重新显示时 Windows 上偶尔直接复用旧的背景缓存 —— 异步的 `update()` 既不会
        立刻生效、又只作用于页面本身，于是能看到上一页的表头/单元格残影。
        这里：先 toggle 一次 updatesEnabled 把整棵子树标记为需要重绘，
        再同步 repaint 页面本身与其中每个滚动区域的 viewport（返回重绘的区域数，便于测试）。
        """
        page.setUpdatesEnabled(False)
        page.setUpdatesEnabled(True)
        page.repaint()
        # 注意：滚动区域的 viewport 是普通 QWidget，**不会**被 findChildren 当成
        # QAbstractScrollArea 找出来 —— 所以传进来的 widget 自己若是滚动区域
        # （例如侧栏 QListWidget），必须单独算上它自己的 viewport。
        areas = list(page.findChildren(QAbstractScrollArea))
        if isinstance(page, QAbstractScrollArea):
            areas.append(page)
        for area in areas:
            area.viewport().repaint()
        return len(areas) + 1

    def goto(self, name: str) -> None:
        for i, (pname, _) in enumerate(self.pages.items()):
            if pname == name:
                self._switch(i)
                return

    def current_page_name(self) -> str | None:
        """当前页面的内部标识（重建界面后用来回到原处）。"""
        widget = self.stack.currentWidget() if getattr(self, "stack", None) else None
        for name, page in self.pages.items():
            if page is widget:
                return name
        return None

    def retranslate_ui(self) -> None:
        """切换界面语言后就地重建整个界面（设置页的语言下拉调这个）。

        为什么是"重建"而不是"再翻译一遍"：控件上的文本是建界面时写进去的，
        翻译层只负责把**中文源码里的字面量**翻成英文；切回中文时必须重新走一遍
        源码，所以只能重建。重建前先给每个页面 `on_leave()` 的机会 ——
        校对页的译文是防抖自动保存，丢了就白敲了。
        """
        current = self.current_page_name()
        for page in self.pages.values():
            hook = getattr(page, "on_leave", None)
            if callable(hook):
                hook()
        i18n.set_language(self.ctx.cfg.get("ui_language"))
        old = self.takeCentralWidget()
        if old is not None:
            old.setParent(None)
            old.deleteLater()
        self.pages = {}
        self._build_ui()
        if self.ctx.project is not None:
            self.refresh_after_project()
        if current:
            self.goto(current)

    def refresh_after_project(self) -> None:
        for page in self.pages.values():
            if hasattr(page, "on_project"):
                page.on_project()

    def closeEvent(self, event):
        # 先给页面一次落盘机会，再关项目：校对页编辑区是防抖自动保存，
        # 不这样兜一下，关窗前刚敲的最后一句话会丢在计时器里。
        for page in self.pages.values():
            if hasattr(page, "on_leave"):
                page.on_leave()
        self.ctx.close_project()
        super().closeEvent(event)
