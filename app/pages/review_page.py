"""校对编辑器（设计 §9-5 / §7.6）：双栏对照、段级对齐、行内编辑、确认流转、导出。

- 数据层 QAbstractTableModel 虚拟化（10 万段不卡，设计 NFR-01/v0.2 #5）；
- 选中一行即源文/译文联动高亮（同一行两栏，FR-09）；
- 搜索替换：预览 + 可撤销（v0.6 #39）；导出：格式/双语模式选择（FR-10）。
"""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QSplitter, QStyledItemDelegate, QTableView, QVBoxLayout, QWidget,
)

from app.pages.base import CtxPage

STATUS_LABEL = {"pending": "未译", "machine_translated": "机翻", "human_edited": "已修改",
                "confirmed": "已确认", "failed": "失败"}
STATUS_COLOR = {"pending": "#888888", "machine_translated": "#2b6cb0",
                "human_edited": "#b7791f", "confirmed": "#2f855a", "failed": "#c53030"}


class SegmentsModel(QAbstractTableModel):
    COLS = ["段号", "状态", "源文", "译文"]
    ROLE_ID = Qt.ItemDataRole.UserRole + 1
    EDIT_COL = 3

    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLS)

    def headerData(self, s, orient, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orient == Qt.Orientation.Horizontal:
            return self.COLS[s]
        return None

    def flags(self, index):
        fl = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == self.EDIT_COL:
            fl |= Qt.ItemFlag.ItemIsEditable
        return fl

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        col = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == 0:
                return f"#{row['seq']}"
            if col == 1:
                label = STATUS_LABEL.get(row["status"], row["status"])
                if row["review_flag"]:
                    label += " ⚑"
                return label
            if col == 2:
                return row["src"]
            return row["tgt"]
        if role == self.ROLE_ID:
            return row["id"]
        if role == Qt.ItemDataRole.ForegroundRole and col == 1:
            from PySide6.QtGui import QColor
            return QColor(STATUS_COLOR.get(row["status"], "#000000"))
        if role == Qt.ItemDataRole.ToolTipRole:
            return (f"{row['doc_path']} · 段 #{row['seq']}\n\n源文：\n{row['src']}\n\n"
                    f"译文：\n{row['tgt']}")
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if index.column() == self.EDIT_COL and role == Qt.ItemDataRole.EditRole:
            row = self.rows[index.row()]
            row["tgt"] = value
            self.dataChanged.emit(index, index)
            return True
        return False


class _RowHeightCapDelegate(QStyledItemDelegate):
    """行高自适应内容但设上限（反馈 #1）：更长内容看底部详情栏或悬停提示。"""

    MAX_H = 150

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        from PySide6.QtCore import QSize
        return QSize(size.width(), min(size.height(), self.MAX_H))


class ReplaceDialog(QDialog):
    def __init__(self, parent, review):
        super().__init__(parent)
        self.review = review
        self.setWindowTitle("搜索替换（仅译文，预览后执行，可撤销）")
        lay = QVBoxLayout(self)
        form = QHBoxLayout()
        self.find_edit = QLineEdit()
        self.replace_edit = QLineEdit()
        self.cs = QCheckBox("区分大小写")
        self.rx = QCheckBox("正则")
        find_btn = QPushButton("预览")
        form.addWidget(QLabel("查找"))
        form.addWidget(self.find_edit, 2)
        form.addWidget(QLabel("替换为"))
        form.addWidget(self.replace_edit, 2)
        form.addWidget(self.cs)
        form.addWidget(self.rx)
        form.addWidget(find_btn)
        lay.addLayout(form)
        self.result = QLabel("先输入查找内容并点击预览。")
        self.result.setWordWrap(True)
        lay.addWidget(self.result)
        btns = QHBoxLayout()
        self.apply_btn = QPushButton("执行替换")
        undo_btn = QPushButton("撤销上次")
        close_btn = QPushButton("关闭")
        self.apply_btn.setEnabled(False)
        btns.addWidget(self.apply_btn)
        btns.addWidget(undo_btn)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        lay.addLayout(btns)
        find_btn.clicked.connect(self._preview)
        self.apply_btn.clicked.connect(self._apply)
        undo_btn.clicked.connect(self._undo)
        close_btn.clicked.connect(self.reject)
        self._preview_obj = None

    def _preview(self):
        try:
            self._preview_obj = self.review.search_replace(
                self.find_edit.text(), self.replace_edit.text(),
                case_sensitive=self.cs.isChecked(), regex=self.rx.isChecked())
        except Exception as e:  # noqa: BLE001
            self.result.setText(f"正则错误：{e}")
            return
        n = len(self._preview_obj.matches)
        sample = "\n".join(f"#{mid}: {before[:40]} → {after[:40]}"
                           for mid, _doc, before, after in self._preview_obj.matches[:5])
        self.result.setText(f"命中 {n} 段" + (f"：\n{sample}" if sample else ""))
        self.apply_btn.setEnabled(n > 0)

    def _apply(self):
        if self._preview_obj:
            n = self._preview_obj.apply()
            self.result.setText(f"已替换 {n} 段（可撤销）。")

    def _undo(self):
        label = self.review.undo_last()
        self.result.setText(f"已撤销：{label}" if label else "没有可撤销的操作。")


class ExportDialog(QDialog):
    def __init__(self, parent, doc_names: list[str]):
        super().__init__(parent)
        self.setWindowTitle("导出翻译结果到 target/")
        lay = QVBoxLayout(self)
        self.docs = doc_names
        self.mode = QComboBox()
        # 模式键放 itemData，展示文案与解析解耦（中文标签含全角括号，不能按空格切）
        self.mode.addItem("target（仅译文，按原格式）", "target")
        self.mode.addItem("bi_inter（段间交错双语）", "bi_inter")
        self.mode.addItem("bi_table（左右表格双语）", "bi_table")
        self.force = QCheckBox("强制导出（忽略源文件变更/未完成警告）")
        lay.addWidget(QLabel(f"将导出 {len(doc_names)} 个文档"))
        lay.addWidget(self.mode)
        lay.addWidget(self.force)
        btn = QPushButton("导出")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)

    def mode_key(self) -> str:
        return self.mode.currentData()


class ReviewPage(CtxPage):
    def __init__(self, ctx, main):
        super().__init__(ctx, main)
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        self.doc_combo = QComboBox()
        self.filter = QComboBox()
        self.filter.addItems(["all", "machine_translated", "human_edited", "confirmed",
                              "failed", "pending"])
        self.filter.setCurrentText("all")
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索源文/译文…")
        refresh_btn = QPushButton("刷新")
        replace_btn = QPushButton("搜索替换…")
        top.addWidget(QLabel("文档"))
        top.addWidget(self.doc_combo, 2)
        top.addWidget(QLabel("状态"))
        top.addWidget(self.filter)
        top.addWidget(self.search, 2)
        top.addWidget(refresh_btn)
        top.addWidget(replace_btn)
        lay.addLayout(top)

        self.model = SegmentsModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                   QAbstractItemView.EditTrigger.EditKeyPressed)
        # 反馈 #1：自动换行 + 行高自适应（有上限），长段完整查看靠底部详情栏/悬停
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.setItemDelegate(_RowHeightCapDelegate(self.table))
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 92)

        # 反馈 #1：底部详情栏（双栏大视图，随选中联动）
        panel = QWidget()
        play = QVBoxLayout(panel)
        play.setContentsMargins(0, 4, 0, 0)
        self.detail_header = QLabel("选中段落后在此查看全文")
        self.detail_src = QPlainTextEdit()
        self.detail_tgt = QPlainTextEdit()
        for w in (self.detail_src, self.detail_tgt):
            w.setReadOnly(True)
            w.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.detail_src)
        split.addWidget(self.detail_tgt)
        play.addWidget(self.detail_header)
        play.addWidget(split, 1)

        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.addWidget(self.table)
        vsplit.addWidget(panel)
        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 1)
        lay.addWidget(vsplit, 1)
        self.table.selectionModel().currentRowChanged.connect(
            lambda *_: self._show_detail())

        bottom = QHBoxLayout()
        confirm_btn = QPushButton("确认选中（Ctrl+Enter）")
        confirm_doc_btn = QPushButton("确认当前文档全部")
        retrans_btn = QPushButton("标记重译选中")
        consist_btn = QPushButton("术语一致性报表…")
        ruby_btn = QPushButton("注音编辑…")
        export_btn = QPushButton("导出…")
        for b in (confirm_btn, confirm_doc_btn, retrans_btn, consist_btn, ruby_btn, export_btn):
            bottom.addWidget(b)
        bottom.addStretch(1)
        self.count_label = QLabel("")
        bottom.addWidget(self.count_label)
        lay.addLayout(bottom)

        confirm_btn.clicked.connect(self._confirm_selected)
        confirm_doc_btn.clicked.connect(self._confirm_doc)
        retrans_btn.clicked.connect(self._retranslate)
        consist_btn.clicked.connect(self._consistency)
        ruby_btn.clicked.connect(self._edit_ruby)
        export_btn.clicked.connect(self._export)
        refresh_btn.clicked.connect(self.refresh)
        replace_btn.clicked.connect(self._replace)
        self.filter.currentTextChanged.connect(lambda _: self.refresh())
        self.search.textChanged.connect(lambda _: self.refresh())
        self.doc_combo.currentIndexChanged.connect(lambda _: self.refresh())
        sc = QShortcut(QKeySequence("Ctrl+Return"), self)
        sc.activated.connect(self._confirm_and_next)

    # ---------- 数据 ----------
    def on_enter(self):
        super().on_enter()
        if self.ctx.project:
            self._rebuild_doc_combo()
            self.refresh()

    def on_project(self):
        self.on_enter()

    def _rebuild_doc_combo(self):
        self.doc_combo.blockSignals(True)
        self.doc_combo.clear()
        self.doc_combo.addItem("全部文档", None)
        for d in self.ctx.project.db.list_documents():
            self.doc_combo.addItem(d["path"], d["id"])
        self.doc_combo.blockSignals(False)

    def refresh(self):
        if not self.ctx.review:
            return
        doc_id = self.doc_combo.currentData()
        rows = self.ctx.review.segments(
            doc_id=doc_id, status_filter=self.filter.currentText(),
            search=self.search.text().strip() or None)
        self.model.set_rows(rows)
        self.count_label.setText(f"{len(rows)} 段")
        self._show_detail()

    def _show_detail(self):
        """底部详情栏：当前段的双栏全文与定位信息（反馈 #1/#4）。"""
        idx = self.table.currentIndex()
        if not idx.isValid() or not self.model.rows:
            self.detail_header.setText("选中段落后在此查看全文")
            self.detail_src.clear()
            self.detail_tgt.clear()
            return
        row = self.model.rows[idx.row()]
        status = STATUS_LABEL.get(row["status"], row["status"])
        if row["review_flag"]:
            status += " ⚑待复核"
        self.detail_header.setText(f"当前：{row['doc_path']} 第 {row['seq']} 段（{status}）")
        self.detail_src.setPlainText(row["src"])
        self.detail_tgt.setPlainText(row["tgt"])

    # ---------- 操作 ----------
    def _selected_rows(self) -> list[dict]:
        return [self.model.rows[i.row()] for i in self.table.selectionModel().selectedRows()]

    def _confirm_selected(self):
        for r in self._selected_rows():
            self.ctx.review.confirm(r["id"])
        self.refresh()

    def _confirm_and_next(self):
        idx = self.table.currentIndex()
        if idx.isValid():
            row = self.model.rows[idx.row()]
            if row["tgt"]:
                self.ctx.review.confirm(row["id"])
                if idx.row() + 1 < len(self.model.rows):
                    self.table.selectRow(idx.row() + 1)
            self.refresh()

    def _confirm_doc(self):
        doc_id = self.doc_combo.currentData()
        if doc_id:
            n = self.ctx.review.confirm_document(doc_id)
            self.ctx.bridge.toast.emit(f"已确认 {n} 段")
            self.refresh()

    def _retranslate(self):
        ids = [r["id"] for r in self._selected_rows()]
        if ids:
            self.ctx.review.mark_retranslate(ids)
            self.refresh()

    def _replace(self):
        ReplaceDialog(self, self.ctx.review).exec()
        self.refresh()

    def _edit_ruby(self):
        """注音内联编辑（增补设计 R3）：编辑译文中各注音槽的译注音。"""
        from PySide6.QtWidgets import QDialog, QFormLayout, QLineEdit as QLE

        from adapters.ruby import RUBY_TOKEN_FULL
        idx = self.table.currentIndex()
        if not idx.isValid():
            self.ctx.bridge.toast.emit("请先选中一个段落")
            return
        row = self.model.rows[idx.row()]
        tokens = sorted(set(RUBY_TOKEN_FULL.findall(row["tgt"])))
        src_entries = {e.get("token"): e for e in (row.get("ruby_src") or [])}
        if not tokens:
            self.ctx.bridge.toast.emit("该段无振假名注音槽")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"注音编辑（{len(tokens)} 个槽位）")
        form = QFormLayout(dlg)
        edits: dict[str, QLE] = {}
        for tok in tokens:
            entry = src_entries.get(tok) or {}
            le = QLE(row.get("ruby_map", {}).get(tok, ""))
            le.setPlaceholderText(f"译注音（原文注音：{entry.get('rt', '?')}，基词：{entry.get('base', '?')}）")
            form.addRow(tok, le)
            edits[tok] = le
        save = QPushButton("保存")
        save.clicked.connect(dlg.accept)
        form.addRow(save)
        if dlg.exec():
            new_map = {tok: le.text().strip() for tok, le in edits.items() if le.text().strip()}
            self.ctx.review.set_ruby_map(row["id"], new_map)
            self.ctx.bridge.toast.emit(f"已保存 {len(new_map)} 条译注音")
            self.refresh()

    def _consistency(self):
        """术语一致性报表（设计 §7.4 译后检查 / M3）。"""
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

        from core.consistency import export_report_csv, term_consistency_report

        rows = term_consistency_report(self.ctx.project)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"术语一致性报表（{sum(1 for r in rows if r['status'] == 'missing')}"
                           " 条未命中）")
        dlg.resize(760, 480)
        lay = QVBoxLayout(dlg)
        table = QTableWidget(len(rows), 5)
        table.setHorizontalHeaderLabels(["源词", "候选", "源文段数", "候选命中", "状态"])
        for i, r in enumerate(rows):
            for j, v in enumerate([r["src"], r["candidates"], str(r["src_hits"]),
                                   str(r["candidate_hits"]),
                                   "✓ 一致" if r["status"] == "ok" else "✗ 未命中"]):
                table.setItem(i, j, QTableWidgetItem(v))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(table, 1)
        btns = QHBoxLayout()
        export_csv = QPushButton("导出 CSV（target/）")
        close = QPushButton("关闭")
        btns.addWidget(export_csv)
        btns.addStretch(1)
        btns.addWidget(close)
        lay.addLayout(btns)
        export_csv.clicked.connect(lambda: (
            export_report_csv(rows, self.ctx.project.target_dir() / "术语一致性报表.csv"),
            self.ctx.bridge.toast.emit("已导出 target/术语一致性报表.csv")))
        close.clicked.connect(dlg.reject)
        dlg.exec()

    def _export(self):
        docs = self.ctx.project.db.list_documents()
        names = {d["id"]: d["path"] for d in docs}
        dlg = ExportDialog(self, list(names.values()))
        if not dlg.exec():
            return
        doc_ids = list(names.keys())
        results = self.ctx.exporter.export(doc_ids, mode=dlg.mode_key(), force=dlg.force.isChecked())
        problems = [f"{r['path']}：{'；'.join(r['warnings'])}" for r in results if not r["ok"]]
        warns = [f"{r['path']}：{'；'.join(r['warnings'])}" for r in results
                 if r["ok"] and r["warnings"]]
        msg = f"成功导出 {sum(1 for r in results if r['ok'])} 个文件到 target/"
        if problems or warns:
            QMessageBox.warning(self, "导出完成（有提示）",
                                msg + "\n\n" + "\n".join(problems + warns))
        else:
            self.ctx.bridge.toast.emit(msg)
