"""主窗体：侧栏导航 + 页面栈（设计 §9 页面清单）。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QStackedWidget,
    QWidget,
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
        for name, page in self.pages.items():
            self.stack.addWidget(page)

        layout = QHBoxLayout()
        layout.addWidget(nav)
        layout.addWidget(self.stack, 1)
        holder = QWidget()
        holder.setLayout(layout)
        self.setCentralWidget(holder)

        nav.currentRowChanged.connect(self._switch)
        nav.setCurrentRow(0)

        title = QLabel()
        self._title = title

    def _switch(self, row: int) -> None:
        page = self.stack.widget(row)
        if hasattr(page, "on_enter"):
            page.on_enter()
        self.stack.setCurrentIndex(row)

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
