"""页面基类：统一的"无项目禁用"行为。"""
from __future__ import annotations

from PySide6.QtWidgets import QWidget


class CtxPage(QWidget):
    """需要打开项目才能操作的页面。"""

    needs_project = True

    def __init__(self, ctx, main):
        super().__init__()
        self.ctx = ctx
        self.main = main

    def on_enter(self):
        enabled = self.ctx.project is not None or not self.needs_project
        self.setEnabled(enabled)

    def on_project(self):
        self.on_enter()
