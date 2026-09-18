"""术语审核对话框（设计 §9-3）：候选术语逐条确认/修改/拒绝，入库 glossary。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


class TermsReviewDialog(QDialog):
    def __init__(self, parent, candidates: list[dict], on_approve, on_reject,
                 toast=None):
        super().__init__(parent)
        self.toast_cb = toast
        self.setWindowTitle(f"术语审核（{len(candidates)} 条候选）")
        self.candidates = candidates
        self.on_approve = on_approve
        self.on_reject = on_reject
        self.resize(720, 480)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("勾选=采纳入库（写入 glossary 并在翻译时强制一致）；"
                             "双击单元格可修改；未勾选的候选将被拒绝。"))
        self.table = QTableWidget(len(candidates), 5)
        self.table.setHorizontalHeaderLabels(["采纳", "源词", "候选（| 分隔）", "注释", "频次"])
        for i, c in enumerate(candidates):
            cb = QCheckBox()
            cb.setChecked(True)
            self.table.setCellWidget(i, 0, cb)
            for j, v in enumerate([c["src"], "|".join(c["candidates"]), c.get("note", ""),
                                   str(c.get("occurrences", 1))], start=1):
                item = QTableWidgetItem(v)
                if j == 4:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(i, j, item)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(self._edit)
        lay.addWidget(self.table, 1)
        btns = QHBoxLayout()
        all_btn = QPushButton("全选")
        none_btn = QPushButton("全不选")
        ok_btn = QPushButton("确认入库")
        reject_btn = QPushButton("全部拒绝")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn.clicked.connect(lambda: self._set_all(False))
        ok_btn.clicked.connect(self._apply)
        reject_btn.clicked.connect(self._reject_all)
        for b in (all_btn, none_btn, reject_btn, ok_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)

    def _set_all(self, on: bool) -> None:
        for i in range(len(self.candidates)):
            self.table.cellWidget(i, 0).setChecked(on)

    def _edit(self, row: int, col: int) -> None:
        if col == 4:
            return
        item = self.table.item(row, col)
        text, ok = QInputDialog.getText(self, "修改", item.text(), text=item.text())
        if ok:
            item.setText(text)

    def _collect(self):
        approved, rejected = [], []
        for i, c in enumerate(self.candidates):
            src = self.table.item(i, 1).text().strip()
            cands = [x.strip() for x in self.table.item(i, 2).text().split("|") if x.strip()][:3]
            note = self.table.item(i, 3).text().strip()
            if self.table.cellWidget(i, 0).isChecked() and src and cands:
                approved.append((src, cands, note))
            else:
                rejected.append(c["src"])
        return approved, rejected

    def _apply(self) -> None:
        approved, rejected = self._collect()
        n = self.on_approve(approved) if approved else 0
        if rejected:
            self.on_reject(rejected)
        if self.toast_cb:
            self.toast_cb(f"已入库 {n} 条，拒绝 {len(rejected)} 条")
        self.accept()

    def _reject_all(self) -> None:
        self.on_reject([c["src"] for c in self.candidates])
        self.accept()
