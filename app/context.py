"""UI 上下文：全局配置、当前项目、服务集合、跨线程信号桥（设计 §4）。"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from core.appconfig import add_recent, load_config, save_config
from core.pipeline import ExportService, ImportService, ReviewService, TranslationService
from core.project import Project


class Bridge(QObject):
    """引擎线程 → UI 线程的唯交通道（Qt 信号跨线程自动排队）。"""
    progress = Signal(dict)
    run_done = Signal(dict)
    toast = Signal(str)


class AppContext:
    def __init__(self):
        self.cfg = load_config()
        self.bridge = Bridge()
        self.project: Project | None = None
        # 打开项目后可用
        self.importer: ImportService | None = None
        self.translation: TranslationService | None = None
        self.review: ReviewService | None = None
        self.exporter: ExportService | None = None
        self.run_handle = None

    # ---------- 项目 ----------
    def open_project(self, root: str) -> None:
        self.close_project()
        project = Project.open(root)
        self.project = project
        self.importer = ImportService(project)
        self.translation = TranslationService(project, self.cfg)
        self.review = ReviewService(project)
        self.exporter = ExportService(project)
        self.cfg = add_recent(self.cfg, str(project.root))
        save_config(self.cfg)

    def close_project(self) -> None:
        if self.run_handle and self.run_handle.running:
            self.run_handle.cancel()
            self.run_handle.join(10)
        self.run_handle = None
        if self.project:
            self.project.close()
        self.project = None

    def save_cfg(self) -> None:
        save_config(self.cfg)
