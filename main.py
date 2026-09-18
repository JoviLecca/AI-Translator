"""AI 项目翻译器 · 桌面端入口。"""
from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app.context import AppContext
    from app.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("AI 项目翻译器")
    ctx = AppContext()
    win = MainWindow(ctx)
    win.show()
    return app.exec()


def selftest() -> int:
    """离屏冒烟：全部页面可实例化（设计 §13 UI 冒烟）。"""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app.context import AppContext
    from app.main_window import MainWindow

    app = QApplication([])
    ctx = AppContext()
    win = MainWindow(ctx)
    assert len(win.pages) == 6, win.pages.keys()
    print("UI selftest: ok,", len(win.pages), "pages")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    raise SystemExit(main())
