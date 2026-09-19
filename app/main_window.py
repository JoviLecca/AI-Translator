"""主窗体：侧栏导航 + 页面栈（设计 §9 页面清单）。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow, QStackedWidget, QWidget,
)

from app.context import AppContext
from app.pages.progress_page import ProgressPage
from app.pages.projects_page import ProjectsPage
from app.pages.review_page import ReviewPage
from app.pages.settings_pages import ProjectSettingsPage, SettingsPage
from app.pages.workbench_page import WorkbenchPage


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("AI 项目翻译器")
        self.resize(1280, 800)

        self.pages: dict[str, QWidget] = {
            "项目管理": ProjectsPage(ctx, self),
            "项目工作台": WorkbenchPage(ctx, self),
            "翻译进度": ProgressPage(ctx, self),
            "校对编辑器": ReviewPage(ctx, self),
            "项目设置": ProjectSettingsPage(ctx, self),
            "设置": SettingsPage(ctx, self),
        }

        nav = QListWidget()
        nav.setFixedWidth(140)
        for name in self.pages:
            nav.addItem(QListWidgetItem(name))

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

        # 轻提示落到状态栏。修复：Bridge.toast 此前没有任何接收者，
        # Qt 信号无接收者时 emit 是 no-op → 全部操作提示被静默丢弃。
        self.statusBar().showMessage("就绪")
        self.ctx.bridge.toast.connect(self._on_toast)

        nav.currentRowChanged.connect(self._switch)
        nav.setCurrentRow(0)

    def _on_toast(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 8000)

    def _switch(self, row: int) -> None:
        page = self.stack.widget(row)
        if hasattr(page, "on_enter"):
            page.on_enter()
        self.stack.setCurrentIndex(row)
        page.update()  # 强制重绘，避免残留上一页内容

    def goto(self, name: str) -> None:
        for i, (pname, _) in enumerate(self.pages.items()):
            if pname == name:
                self._switch(i)
                return

    def refresh_after_project(self) -> None:
        for page in self.pages.values():
            if hasattr(page, "on_project"):
                page.on_project()

    def closeEvent(self, event):
        self.ctx.close_project()
        super().closeEvent(event)
