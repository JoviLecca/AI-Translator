"""校对编辑器（设计 §9-5 / §7.6）：双栏对照、段级对齐、行内编辑、确认流转、导出。

- 数据层 QAbstractTableModel 虚拟化（10 万段不卡，设计 NFR-01/v0.2 #5）；
- 选中一行即源文/译文联动高亮（同一行两栏，FR-09）；
- 搜索替换：预览 + 可撤销（v0.6 #39）；导出：格式/双语模式选择（FR-10）。
"""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QPlainTextEdit,
    QScrollArea, QSplitter, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
    QTabWidget, QTableView, QVBoxLayout, QWidget,
)

from app.pages.base import CtxPage
from core.term_impact import SOURCE_CACHE, SOURCE_NONE, SOURCE_REVISION

STATUS_LABEL = {"pending": "未译", "machine_translated": "机翻", "human_edited": "已修改",
                "confirmed": "已确认", "failed": "失败"}
STATUS_COLOR = {"pending": "#888888", "machine_translated": "#2b6cb0",
                "human_edited": "#b7791f", "confirmed": "#2f855a", "failed": "#c53030"}


class SegmentsModel(QAbstractTableModel):
    COLS = ["段号", "状态", "源文", "译文"]
    ROLE_ID = Qt.ItemDataRole.UserRole + 1
    EDIT_COL = 3

    def __init__(self, on_edit=None):
        super().__init__()
        self.rows: list[dict] = []
        # 落库回调 (seg_id, text) -> None。缺省为 None 时只改内存，
        # 便于测试直接构造模型。
        self._on_edit = on_edit

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
            if value == row["tgt"]:
                return True
            row["tgt"] = value
            # 修复：此前只改内存行、从不写库，任何一次 refresh()（改筛选/搜索/
            # 切文档/切页）都会把人工校对成果丢掉。现在经 ReviewService.edit
            # 落库（置 human_edited 并清除待复核标记）。
            if self._on_edit is not None:
                self._on_edit(row["id"], value)
            row["status"] = "human_edited"
            row["review_flag"] = False
            # 整行刷新，让「状态」列立即从「机翻」变「已修改」
            self.dataChanged.emit(self.index(index.row(), 0),
                                  self.index(index.row(), len(self.COLS) - 1))
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
    """导出对话框（S2：格式选择 + 模式选择；同名文件处理）。"""

    FORMATS = [
        ("跟随源文件（默认）", None),
        ("纯文本 (.txt)", "txt"),
        ("Markdown (.md)", "md"),
        ("HTML (.html)", "html"),
        ("Word (.docx)", "docx"),
    ]
    # 同名文件处理策略（用户反馈：二次导出会静默覆盖已有译文，应给选择）
    CONFLICTS = [
        ("覆盖已有译文（默认）", "overwrite"),
        ("保留两者（自动改名 xxx(2).md）", "keep_both"),
        ("跳过已存在的文件", "skip"),
    ]

    def __init__(self, parent, doc_names: list[str], count_existing=None):
        super().__init__(parent)
        self.setWindowTitle("导出翻译结果到 target/")
        lay = QVBoxLayout(self)
        self.docs = doc_names
        self._count_existing = count_existing
        self.fmt = QComboBox()
        for label, key in self.FORMATS:
            self.fmt.addItem(label, key)
        self.mode = QComboBox()
        self.mode.addItem("target（仅译文）", "target")
        self.mode.addItem("bi_inter（段间交错双语）", "bi_inter")
        self.mode.addItem("bi_table（左右表格双语）", "bi_table")
        self.conflict = QComboBox()
        for label, key in self.CONFLICTS:
            self.conflict.addItem(label, key)
        self.existing_hint = QLabel("")
        self.existing_hint.setWordWrap(True)
        self.force = QCheckBox("强制导出（忽略源文件变更/未完成警告）")
        lay.addWidget(QLabel(f"将导出 {len(doc_names)} 个文档"))
        lay.addWidget(QLabel("导出格式："))
        lay.addWidget(self.fmt)
        lay.addWidget(QLabel("导出模式："))
        lay.addWidget(self.mode)
        lay.addWidget(QLabel("同名文件："))
        lay.addWidget(self.conflict)
        lay.addWidget(self.existing_hint)
        lay.addWidget(self.force)
        btn = QPushButton("导出")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)
        self.fmt.currentIndexChanged.connect(lambda _: self._sync_existing())
        self._sync_existing()

    def _sync_existing(self) -> None:
        """提示 target/ 里已存在多少个本次将要写出的文件，便于用户决定是否覆盖。"""
        if self._count_existing is None:
            self.existing_hint.setText("")
            return
        try:
            n = int(self._count_existing(self.format_key()))
        except Exception:  # noqa: BLE001 提示失败不影响导出
            n = 0
        self.existing_hint.setText(
            f"⚠ target/ 中已有 {n} 个同名文件，将按上面的设置处理。" if n
            else "target/ 中没有同名文件。")

    def mode_key(self) -> str:
        return self.mode.currentData()

    def format_key(self):
        """None = 跟随源文件；str = 指定格式。"""
        return self.fmt.currentData()

    def conflict_key(self) -> str:
        return self.conflict.currentData() or "overwrite"


class TermImpactDialog(QDialog):
    """术语变更影响（设计 §7.4 M2）：改了术语译名后批量订正旧译文。

    反查「仍在使用旧候选」的已译段落，提供：

    - **全局替换为新候选**：把旧候选换成本术语的新首选候选，记录撤销栈、可撤销；
    - **标记重译**：把这些段置回待翻译，下次「开始翻译」重新生成（其余段不动）。

    表格本身就是「受影响段落预览」，替换前再确认一次 —— 满足设计 §7.6 对全局替换的
    要求：先列出受影响段落、确认后执行、全程可撤销。
    """

    COLS = ["术语", "旧候选 → 新候选", "文档", "段号", "译文片段（旧候选处）"]

    def __init__(self, parent, service, on_changed=None):
        super().__init__(parent)
        self.service = service
        self.on_changed = on_changed     # 替换/标记后回调，让校对页刷新状态列
        self.affected: list[dict] = []
        self.setWindowTitle("术语变更影响")
        self.resize(860, 460)
        lay = QVBoxLayout(self)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.table, 1)

        btns = QHBoxLayout()
        self.replace_btn = QPushButton("全局替换为新候选")
        self.retrans_btn = QPushButton("标记重译")
        self.undo_btn = QPushButton("撤销上次替换")
        refresh_btn = QPushButton("刷新")
        close_btn = QPushButton("关闭")
        for b in (self.replace_btn, self.retrans_btn, self.undo_btn, refresh_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        lay.addLayout(btns)
        self.replace_btn.clicked.connect(self._replace_all)
        self.retrans_btn.clicked.connect(self._mark_retranslate)
        self.undo_btn.clicked.connect(self._undo)
        refresh_btn.clicked.connect(self.reload)
        close_btn.clicked.connect(self.reject)
        self.reload()

    # ---------- 数据 ----------
    def reload(self) -> None:
        # 外部（Excel）改过术语表就先重载并补齐变更历史 —— 否则 compare 不出差异
        if self.service.project.glossary.external_changed():
            self.service.project.reload_glossary()
        self.affected = self.service.analyze()
        self.table.setRowCount(len(self.affected))
        for i, it in enumerate(self.affected):
            seg = self.service.project.db.get_segment(it["seg_id"])
            doc = (self.service.project.db.get_document(it["doc_id"])
                   if it.get("doc_id") else None)
            tgt = (seg["tgt_text"] if seg else "") or ""
            pos = tgt.find(it["old"])
            snippet = (tgt[max(0, pos - 20): pos + len(it["old"]) + 20]
                       if pos >= 0 else tgt[:60])
            vals = [it["term"],
                    f"{it['old']} → {it['new'] or '（该术语已删除）'}",
                    doc["path"] if doc else "",
                    str(seg["seq"]) if seg else "",
                    snippet]
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem(v))
        for col, width in ((0, 110), (1, 190), (2, 200), (3, 55)):
            self.table.setColumnWidth(col, width)
        has_rows = bool(self.affected)
        self.replace_btn.setEnabled(has_rows)
        self.retrans_btn.setEnabled(has_rows)
        self.undo_btn.setEnabled(self.service.can_undo())
        self.hint.setText(self._hint_text(has_rows))

    def _hint_text(self, has_rows: bool) -> str:
        source = self.service.history_source()
        note = {
            SOURCE_REVISION: "（对比依据：上一份术语表快照）",
            SOURCE_CACHE: "（对比依据：上次在软件内保存的术语缓存" \
                          "—— 历史快照不足时的兜底）",
        }.get(source, "")
        if has_rows:
            docs = len({it.get("doc_id") for it in self.affected})
            return (f"共 {len(self.affected)} 处译文仍在使用旧候选（涉及 {docs} 个文档）。"
                    "「全局替换」把旧候选换成新首选候选（可撤销）；"
                    "「标记重译」把这些段置回待翻译，下次开始翻译时用新术语重新生成。"
                    f"{note}")
        if source == SOURCE_NONE:
            return ("没有可对比的旧术语状态：本项目既没有术语快照、也没有术语缓存。"
                    "在软件内增删改术语会自动留快照；在 Excel 里改过之后重新加载"
                    "（工作台「检查术语表外部修改」）或重开项目也会补上快照，"
                    "之后即可比出「哪些旧译文还在用旧译名」。")
        return f"没有检测到受影响的段落：术语变更后，旧候选已不出现在任何译文里。{note}"

    # ---------- 动作 ----------
    def _replace_all(self) -> None:
        if QMessageBox.question(
                self, "全局替换",
                f"将把 {len(self.affected)} 处译文中的旧候选替换为新首选候选。\n"
                "该操作可撤销。要继续吗？"
        ) != QMessageBox.StandardButton.Yes:
            return
        n = self.service.replace_all(self.affected)
        QMessageBox.information(self, "全局替换",
                                f"已替换 {n} 段（可用「撤销上次替换」回退）。")
        self._after_change()

    def _mark_retranslate(self) -> None:
        n = self.service.mark_retranslate(self.affected)
        QMessageBox.information(
            self, "标记重译",
            f"已把 {n} 段置回「未翻译」。下次「开始翻译」时会用新术语重新生成，"
            "其余段落不受影响。")
        self._after_change()

    def _undo(self) -> None:
        n = self.service.undo()
        QMessageBox.information(self, "撤销",
                                f"已撤销 {n} 段替换。" if n else "没有可撤销的替换。")
        self._after_change()

    def _after_change(self) -> None:
        self.reload()
        if self.on_changed is not None:
            self.on_changed()


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

        self.model = SegmentsModel(self._persist_edit)
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

        # 反馈 #1 + S4：底部详情栏（双栏大视图 + 源图标签页，随选中联动）
        panel = QWidget()
        play = QVBoxLayout(panel)
        play.setContentsMargins(0, 4, 0, 0)
        self.detail_header = QLabel("选中段落后在此查看全文")
        self.detail_src = QPlainTextEdit()
        self.detail_tgt = QPlainTextEdit()
        for w in (self.detail_src, self.detail_tgt):
            w.setReadOnly(True)
            w.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        text_split = QSplitter(Qt.Orientation.Horizontal)
        text_split.addWidget(self.detail_src)
        text_split.addWidget(self.detail_tgt)
        text_tab = QWidget()
        t_lay = QVBoxLayout(text_tab)
        t_lay.setContentsMargins(0, 0, 0, 0)
        t_lay.addWidget(text_split, 1)

        # S4：源图标签页（img 格式文档显示原始图片）
        self.image_label = QLabel("（此段非图片来源，或图片文件不存在）")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_scroll = QScrollArea()
        image_scroll.setWidget(self.image_label)
        image_scroll.setWidgetResizable(True)

        self.detail_tabs = QTabWidget()
        self.detail_tabs.addTab(text_tab, "源文译文")
        self.detail_tabs.addTab(image_scroll, "源图")

        play.addWidget(self.detail_header)
        play.addWidget(self.detail_tabs, 1)

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
        self.confirm_doc_btn = confirm_doc_btn
        retrans_btn = QPushButton("标记重译选中")
        consist_btn = QPushButton("术语一致性报表…")
        impact_btn = QPushButton("术语变更影响…")
        ruby_btn = QPushButton("注音编辑…")
        export_btn = QPushButton("导出…")
        for b in (confirm_btn, confirm_doc_btn, retrans_btn, consist_btn, impact_btn,
                  ruby_btn, export_btn):
            bottom.addWidget(b)
        bottom.addStretch(1)
        self.count_label = QLabel("")
        bottom.addWidget(self.count_label)
        lay.addLayout(bottom)

        confirm_btn.clicked.connect(self._confirm_selected)
        confirm_doc_btn.clicked.connect(self._confirm_doc)
        retrans_btn.clicked.connect(self._retranslate)
        consist_btn.clicked.connect(self._consistency)
        impact_btn.clicked.connect(self._term_impact)
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

    def _persist_edit(self, seg_id: int, text: str) -> None:
        """表格内联编辑落库（ReviewService.edit：human_edited + 清除待复核）。"""
        if self.ctx.review is not None:
            self.ctx.review.edit(seg_id, text)

    def refresh(self):
        self._sync_confirm_btn()
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
        """底部详情栏：当前段的双栏全文与定位信息（反馈 #1/#4 + S4 源图预览）。"""
        idx = self.table.currentIndex()
        if not idx.isValid() or not self.model.rows:
            self.detail_header.setText("选中段落后在此查看全文")
            self.detail_src.clear()
            self.detail_tgt.clear()
            self.image_label.clear()
            self.image_label.setText("（此段非图片来源，或图片文件不存在）")
            return
        row = self.model.rows[idx.row()]
        status = STATUS_LABEL.get(row["status"], row["status"])
        if row["review_flag"]:
            status += " ⚑待复核"
        self.detail_header.setText(f"当前：{row['doc_path']} 第 {row['seq']} 段（{status}）")
        self.detail_src.setPlainText(row["src"])
        self.detail_tgt.setPlainText(row["tgt"])
        # S4：源图预览
        self._load_source_image(row)

    def _load_source_image(self, row: dict):
        """S4：img 格式文档加载原始图片到源图标签页。"""
        doc_path = row.get("doc_path", "")
        if not doc_path.startswith("source/") or not self.ctx.project:
            self.image_label.setText("（此段非图片来源）")
            return
        # 查文档格式
        doc = self.ctx.project.db.get_document_by_path(doc_path)
        if doc is None or doc["format"] != "img":
            self.image_label.setText("（此段非图片来源）")
            return
        img_path = self.ctx.project.root / doc_path
        if not img_path.exists():
            self.image_label.setText(f"（源图缺失：{img_path.name}）")
            return
        pixmap = QPixmap(str(img_path))
        if pixmap.isNull():
            self.image_label.setText(f"（无法加载图片：{img_path.name}）")
            return
        # 缩放到标签页可用区域（保持宽高比）
        max_w = max(400, self.detail_tabs.width() - 20)
        max_h = max(300, self.detail_tabs.height() - 20)
        scaled = pixmap.scaled(max_w, max_h,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        self.image_label.setPixmap(scaled)

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
        """确认整个文档；文档下拉为「全部文档」时即确认全部文档。

        原实现是 `if doc_id:` —— 而文档下拉的默认值就是「全部文档」(data=None)，
        所以点这个按钮**永远静默无操作**（用户反馈：按键无效）。
        """
        doc_id = self.doc_combo.currentData()
        scope = self.doc_combo.currentText()
        todo = self._confirmable_count(doc_id)
        if todo == 0:
            QMessageBox.information(self, "确认全部",
                                    f"「{scope}」范围内没有待确认的段落。")
            return
        if QMessageBox.question(
                self, "确认全部",
                f"将把「{scope}」范围内 {todo} 段标记为「已确认」。\n要继续吗？"
        ) != QMessageBox.StandardButton.Yes:
            return
        n = (self.ctx.review.confirm_all() if doc_id is None
             else self.ctx.review.confirm_document(doc_id))
        self.ctx.bridge.toast.emit(f"已确认 {n} 段")
        self.refresh()

    def _confirmable_count(self, doc_id: int | None = None) -> int:
        """待确认段数（确认动作会覆盖 machine_translated / human_edited）。"""
        kw = {"translatable": True,
              "status_in": ("machine_translated", "human_edited")}
        db = self.ctx.project.db
        if doc_id is None:
            return len(db.list_segments(**kw))
        return len(db.list_segments(doc_id=doc_id, **kw))

    def _sync_confirm_btn(self) -> None:
        """按钮文案跟随文档下拉，避免用户以为它只作用于当前文档。"""
        self.confirm_doc_btn.setText(
            "确认当前文档全部" if self.doc_combo.currentData() is not None
            else "确认全部文档")

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

    def _term_impact(self):
        """术语变更影响分析（设计 §7.4 M2）：改术语译名后批量订正旧译文。"""
        service = self.ctx.term_impact
        if service is None:
            return
        TermImpactDialog(self, service, on_changed=self.refresh).exec()

    def _count_existing_targets(self, format_key) -> int:
        """本次导出将要写出的文件中，target/ 里已存在多少个（供对话框提示）。"""
        n = 0
        for d in self.ctx.project.db.list_documents():
            try:
                if self.ctx.exporter.target_path(d, format_key).exists():
                    n += 1
            except Exception:  # noqa: BLE001
                continue
        return n

    def _export(self):
        docs = self.ctx.project.db.list_documents()
        names = {d["id"]: d["path"] for d in docs}
        dlg = ExportDialog(self, list(names.values()),
                           count_existing=self._count_existing_targets)
        if not dlg.exec():
            return
        doc_ids = list(names.keys())
        self._run_export(doc_ids, dlg)

    def _run_export(self, doc_ids: list[int], dlg) -> None:
        """带进度显示与完成提示的导出。

        用户反馈：导出过程没有任何进度、完成也没有明显提示（原来只往状态栏
        发一条容易被错过的消息），文档多或含图片 OCR 时不知道是否还在跑。
        """
        from PySide6.QtWidgets import QApplication, QProgressDialog

        total = len(doc_ids)
        stop = {"flag": False}
        prog = QProgressDialog("准备导出…", "取消", 0, max(1, total), self)
        prog.setWindowTitle("导出翻译结果")
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)      # 立即显示
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        # 实测 QProgressDialog 在窗口尚未显示时调用 cancel() 不发 canceled，
        # 所以直接连自建取消按钮的 clicked，确保点了就一定停。
        cancel_btn = QPushButton("取消")
        prog.setCancelButton(cancel_btn)
        cancel_btn.clicked.connect(lambda: stop.update(flag=True))
        prog.setValue(0)
        prog.show()

        def on_progress(done: int, tot: int, path: str) -> None:
            prog.setMaximum(max(1, tot))
            prog.setValue(done)
            prog.setLabelText(f"正在导出 {done + 1}/{tot}：{path}" if path
                              else f"已完成 {done}/{tot}")
            # 让进度条与「取消」按钮真正响应（导出在 UI 线程同步执行）
            QApplication.processEvents()

        was_cancelled = False
        try:
            results = self.ctx.exporter.export(
                doc_ids, mode=dlg.mode_key(), force=dlg.force.isChecked(),
                output_format=dlg.format_key(), on_conflict=dlg.conflict_key(),
                on_progress=on_progress, cancel_check=lambda: stop["flag"])
            # 先取取消状态再关闭对话框：QProgressDialog.close() 自己也会发 canceled
            was_cancelled = stop["flag"]
        except Exception as e:  # noqa: BLE001
            prog.close()
            QMessageBox.warning(self, "导出失败", str(e))
            return
        prog.close()

        ok = sum(1 for r in results if r["ok"])
        failed = [r for r in results if not r["ok"]]
        skipped = [r for r in results if r.get("skipped")]
        overwritten = [r for r in results if r["ok"] and r.get("overwrote")]
        warned = [r for r in results if r["ok"] and r["warnings"] and not r.get("skipped")]
        lines = []
        if was_cancelled:
            lines += ["已取消导出：未处理的文档保持原状。", ""]
        lines.append(f"成功导出 {ok} 个文件到 target/（共 {total} 个文档）")
        if overwritten:
            lines.append(f"其中 {len(overwritten)} 个覆盖了同名旧文件。")
        if skipped:
            lines.append(f"跳过 {len(skipped)} 个已存在的文件（未覆盖）。")
        if failed:
            lines += ["", "失败："] + [
                f"· {r['path']}：{'；'.join(r['warnings'])}" for r in failed]
        if warned:
            lines += ["", "提示："] + [
                f"· {r['path']}：{'；'.join(r['warnings'])}" for r in warned]

        # 完成提示：系统提示音 + 弹窗（用户反馈：提示要看得见/听得见）
        QApplication.beep()
        body = "\n".join(lines)
        if failed:
            QMessageBox.warning(self, "导出完成（有失败）", body)
        elif warned or was_cancelled:
            QMessageBox.information(self, "导出完成（有提示）", body)
        else:
            QMessageBox.information(self, "导出完成", body)
        self.ctx.bridge.toast.emit(f"已导出 {ok} 个文件到 target/")
