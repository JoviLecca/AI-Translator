"""翻译进度页（设计 §9-4）：进度、计数、token 与费用、取消。"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPushButton, QProgressBar, QVBoxLayout

from app.pages.base import CtxPage


class ProgressPage(CtxPage):
    def __init__(self, ctx, main):
        super().__init__(ctx, main)
        lay = QVBoxLayout(self)
        self.bar = QProgressBar()
        self.info = QLabel("尚未开始翻译。")
        self.cost = QLabel("")
        cancel_btn = QPushButton("取消（未开始批次保留，可续跑）")
        cancel_btn.clicked.connect(self._cancel)
        lay.addWidget(self.bar)
        lay.addWidget(self.info)
        lay.addWidget(self.cost)
        lay.addWidget(cancel_btn)
        lay.addStretch(1)
        ctx.bridge.progress.connect(self._on_progress)
        ctx.bridge.run_done.connect(self._on_done)

    def on_enter(self):
        super().on_enter()
        if self.ctx.run_handle:
            run = self.ctx.project.db.get_run(self.ctx.run_handle.run_id)
            if run:
                self._update(run["done"], run["failed"], 0, run["total"],
                             run["tokens_in"], run["tokens_out"], run["cost"])

    def _on_progress(self, ev: dict):
        if ev.get("event") == "segment":
            self._update(ev.get("done", 0), ev.get("failed", 0), ev.get("tm", 0),
                         ev.get("total", 1), 0, 0, 0.0)

    def _update(self, done, failed, tm, total, tin, tout, cost):
        total = max(1, total)
        self.bar.setMaximum(total)
        self.bar.setValue(done + failed)
        self.info.setText(f"完成 {done}（含翻译记忆 {tm}）· 失败 {failed} · 共 {total} 段")
        if tin or tout:
            self.cost.setText(f"tokens: {tin} in / {tout} out")

    def _on_done(self, result: dict):
        if result.get("paused"):
            self.info.setText(f"已暂停：{result.get('error', '')}\n修复配置后可从断点继续。")
        elif result.get("cancelled"):
            self.info.setText("已取消。未开始的段落保持待翻译，可随时续跑。")
        else:
            self.info.setText(
                f"完成：{result.get('done', 0)} 段（TM 复用 {result.get('tm', 0)}），"
                f"失败 {result.get('failed', 0)}。前往校对编辑器检查。")
        self.cost.setText(f"tokens: {result.get('tokens_in', 0)} in / "
                          f"{result.get('tokens_out', 0)} out")

    def _cancel(self):
        if self.ctx.run_handle and self.ctx.run_handle.running:
            self.ctx.run_handle.cancel()
