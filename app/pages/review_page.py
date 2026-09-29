"""校对编辑器（设计 §9-5 / §7.6）：双栏对照、段级对齐、段级编辑、确认流转、导出。

反馈 2026-09-29 #1（编辑落点）与 #2（编辑区高度可拖拽）定下了「在哪改」的交互：

- **上方表格只负责「选中」**要查看或校对的段落，不再有单元格内联编辑
  （单元格里只能显示一行，长段落根本没法校对）；
- **编辑统一在下方编辑区完成**：右栏译文可直接改（防抖自动保存 + 失焦即存 +
  Ctrl+S），左栏原文默认只读，OCR 识别错误 / 分段错误时勾选「允许编辑原文」；
- 上下两块之间是 QSplitter，**拖动分隔条即可改变编辑区高度**（双击分隔条复位），
  尺寸写进全局配置，下次打开还是你要的比例。

数据层 QAbstractTableModel 虚拟化（10 万段不卡，设计 NFR-01/v0.2 #5）；
选中一行即源文/译文联动高亮（同一行两栏，FR-09）；
搜索替换：预览 + 可撤销（v0.6 #39）；导出：格式/双语模式选择（FR-10）。
"""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, Qt, QTimer
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QPlainTextEdit, QScrollArea, QSplitter, QStyledItemDelegate, QTableWidget,
    QTableWidgetItem, QTabWidget, QTableView, QVBoxLayout, QWidget,
)

from app.pages.base import CtxPage
from core.term_impact import (
    SOURCE_CACHE, SOURCE_NONE, SOURCE_REVISION, segment_term_issues,
)

STATUS_LABEL = {"pending": "未译", "machine_translated": "机翻", "human_edited": "已修改",
                "confirmed": "已确认", "failed": "失败"}
STATUS_COLOR = {"pending": "#888888", "machine_translated": "#2b6cb0",
                "human_edited": "#b7791f", "confirmed": "#2f855a", "failed": "#c53030"}


class SegmentsModel(QAbstractTableModel):
    COLS = ["段号", "状态", "源文", "译文"]
    ROLE_ID = Qt.ItemDataRole.UserRole + 1
    EDIT_COL = 3
    SRC_COL = 2

    def __init__(self, on_edit=None, on_src_edit=None):
        super().__init__()
        self.rows: list[dict] = []
        self._by_id: dict[int, int] = {}
        # 落库回调 (seg_id, text) -> None。缺省为 None 时只改内存，
        # 便于测试直接构造模型。
        self._on_edit = on_edit
        self._on_src_edit = on_src_edit

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        # 段 id → 行号：刷新后恢复选中、编辑区落库后定位都靠它，
        # 免得 10 万段时反复线性扫描（NFR-01）
        self._by_id = {r["id"]: i for i, r in enumerate(rows)}
        self.endResetModel()

    def row_of(self, seg_id: int) -> int | None:
        return self._by_id.get(seg_id)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLS)

    def headerData(self, s, orient, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orient == Qt.Orientation.Horizontal:
            return self.COLS[s]
        return None

    def flags(self, index):
        # 反馈 2026-09-29 #1：表格只负责选中段落，不再内联编辑 —— 编辑一律在下方编辑区。
        # （用户报的「编辑时段落缩成一行」就是单元格内联编辑器只能容一行导致的。）
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

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
        # 反馈 2026-09-29 #3：不再提供单元格悬停提示。
        # 原来鼠标停在某段上会在光标旁弹出「源文 + 译文」大框，既挡视线又和下方
        # 编辑区重复 —— 看全文就在下方编辑区看（那里还能改），这里保持干净。
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        """写入译文（现由下方编辑区调用；表格本身已不可内联编辑）。"""
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

    def set_source(self, index, value) -> bool:
        """写入原文（反馈 2026-09-29 #1：OCR 识别错误 / 分段错误修正）。

        原文变了译文就可能不再对应，所以顺手置「⚑ 待复核」提醒再看一眼；
        该标记在确认该段时清除（`ReviewService.confirm`）。
        """
        row = self.rows[index.row()]
        if value == row["src"]:
            return True
        row["src"] = value
        if self._on_src_edit is not None:
            self._on_src_edit(row["id"], value)
        row["review_flag"] = True
        self.dataChanged.emit(self.index(index.row(), 0),
                              self.index(index.row(), len(self.COLS) - 1))
        return True


class _RowHeightCapDelegate(QStyledItemDelegate):
    """行高自适应内容但设上限（反馈 #1）：超过上限的部分在下方编辑区看全文。

    注意：`sizeHint` 里做的是**带自动换行的文本排版**，单次约 30µs；它被
    `resizeRowsToContents()` 逐行调用，所以绝不能放进拖动路径（见 ReviewPage
    的「行高重算策略」）。
    """

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
    """术语变更影响（设计 §7.4 M2）：术语新增/修改/删除后，批量订正旧译文。

    反查需要订正的已译段落，分两类（见 core/term_impact.py 的 kind）：

    - **旧译名仍在用**：把旧候选换成本术语的新首选候选，记录撤销栈、可撤销；
    - **新术语未体现**：源文含术语、译文里没有任何当前候选 —— 没有旧串可机械替换，
      交给「标记重译」（把这些段置回待翻译，下次「开始翻译」按新术语重新生成）。

    表格本身就是「受影响段落预览」，替换前再确认一次 —— 满足设计 §7.6 对全局替换的
    要求：先列出受影响段落、确认后执行、全程可撤销。
    """

    COLS = ["术语", "类型", "需要变成", "文档", "段号", "译文片段"]

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
            if it["kind"] == "old":
                kind, change = "旧译名仍在用", f"{it['old']} → {it['new'] or '（该术语已删除）'}"
                pos = tgt.find(it["old"])
                snippet = (tgt[max(0, pos - 20): pos + len(it["old"]) + 20]
                           if pos >= 0 else tgt[:60])
            else:
                kind, change = "新术语未体现", f"应含「{it['new']}」"
                snippet = tgt[:60]
            vals = [it["term"], kind, change, doc["path"] if doc else "",
                    str(seg["seq"]) if seg else "", snippet]
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem(v))
        for col, width in ((0, 100), (1, 105), (2, 160), (3, 170), (4, 50)):
            self.table.setColumnWidth(col, width)
        has_rows = bool(self.affected)
        old_rows = sum(1 for it in self.affected if it["kind"] == "old")
        self.replace_btn.setEnabled(old_rows > 0)      # 「新术语未体现」没有旧串可机械替换
        self.retrans_btn.setEnabled(has_rows)
        self.undo_btn.setEnabled(self.service.can_undo())
        self.hint.setText(self._hint_text(has_rows, old_rows))

    def _hint_text(self, has_rows: bool, old_rows: int = 0) -> str:
        source = self.service.history_source()
        note = {
            SOURCE_REVISION: "（对比依据：上一份术语表快照）",
            SOURCE_CACHE: "（对比依据：上次在软件内保存的术语缓存" \
                          "—— 历史快照不足时的兜底）",
        }.get(source, "")
        if has_rows:
            missing = len(self.affected) - old_rows
            docs = len({it.get("doc_id") for it in self.affected})
            bits = [f"共 {len(self.affected)} 处译文需要订正（涉及 {docs} 个文档）："]
            if old_rows:
                bits.append(f"· {old_rows} 处仍在用**旧译名** —— 「全局替换」可换成新首选候选"
                            "（可撤销）；")
            if missing:
                bits.append(f"· {missing} 处**没用上新术语** —— 没有旧串可替换，"
                            "请「标记重译」（下次按新术语重新生成）或在校对页逐段改；")
            bits.append("校对页里选中这类段落时，编辑区下方也会直接提示并可一键处理。")
            bits.append(note)
            return "\n".join(b for b in bits if b)
        if source == SOURCE_NONE:
            return ("没有可对比的旧术语状态：本项目既没有术语快照、也没有术语缓存。"
                    "在软件内增删改术语会自动留快照；在 Excel 里改过之后重新加载"
                    "（工作台「检查术语表外部修改」）或重开项目也会补上快照，"
                    "之后即可比出「哪些旧译文还在用旧译名 / 还没用上新术语」。")
        return ("没有检测到需要订正的段落：术语改过之后，旧译名已不再出现，"
                f"源文含新术语的段落也都用上了约定译名。{note}")

    # ---------- 动作 ----------
    def _replace_all(self) -> None:
        n_old = sum(1 for it in self.affected if it["kind"] == "old")
        if QMessageBox.question(
                self, "全局替换",
                f"将把 {n_old} 处译文中的旧译名替换为新首选候选。\n"
                "「新术语未体现」的那些段没有旧串可替换，不会被改动"
                "（请用「标记重译」或逐段修改）。\n该操作可撤销。要继续吗？"
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
    """校对编辑器主页面（反馈 2026-09-29 #1/#2：表格只选中 + 下方编辑 + 可拖拽高度）。"""

    # 默认上下比例（表格 430 / 编辑区 400）：编辑区现在是主编辑面，一开就占一半
    DEFAULT_VSPLIT = (430, 400)
    AUTOSAVE_MS = 700          # 译文/原文输入防抖自动保存
    HANDLE_QSS = (
        "QSplitter::handle:vertical { background: #cbd5e0; }"
        "QSplitter::handle:vertical:hover { background: #90a4ae; }"
        "QSplitter::handle:horizontal { background: #e2e8f0; }"
        "QSplitter::handle:horizontal:hover { background: #90a4ae; }")

    def __init__(self, ctx, main):
        super().__init__(ctx, main)
        lay = QVBoxLayout(self)

        # 编辑区状态（反馈 2026-09-29 #1）
        self._cur_id: int | None = None      # 编辑区当前载入的段 id
        self._cur_row: dict | None = None
        self._dirty_tgt = False
        self._dirty_src = False
        self._loading = False                # 程序化填文本时屏蔽 textChanged
        self._suppress_toggle = False        # 程序化勾选「允许编辑原文」时屏蔽信号
        self._src_auto = False               # 当前是否处于「OCR 自动放开原文」状态
        # 用户是否已就「原文可编辑」表过态（上次会话存过偏好即算表过态）
        self._src_manual = "review_allow_src_edit" in self.ctx.cfg
        # 术语提示（反馈 2026-09-29 #6）
        self._term_issues: list[dict] = []          # 当前段的术语问题
        self._term_ignored: set[tuple[int, str]] = set()   # (段 id, 术语) 本次会话忽略
        self._old_terms: dict | None = None         # 术语的「上一版候选」，每次刷新取一次

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

        self.model = SegmentsModel(self._persist_edit, self._persist_src_edit)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # 反馈 2026-09-29 #1：表格不再内联编辑 —— 它只负责「选中要校对/编辑的段落」，
        # 编辑一律在下方编辑区（原来单元格内联编辑只能容一行，长段没法校对）。
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # 反馈 #1：自动换行，长段在下方编辑区看全文
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setItemDelegate(_RowHeightCapDelegate(self.table))
        # 反馈 2026-09-29 #4：行高改成「我们自己按需重算」而不是交给
        # ResizeToContents 自动管（原因见下方 _recalc_row_heights 的注释）。
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 92)
        self._row_width = 0                    # 上次算行高时的视口宽度
        self._text_cols_width = 0              # 上次算行高时两个文本列的总宽
        self._measured_rows: set[int] = set()  # 已经量过行高的行（其余用默认单行高）
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.setInterval(60)     # 宽度连续变化时只在停下来后量一次
        self._height_timer.timeout.connect(lambda: self._measure_visible_rows(with_margin=True))
        self.table.viewport().installEventFilter(self)
        self.table.verticalScrollBar().valueChanged.connect(
            lambda *_: self._measure_visible_rows())   # 滚动时补量新露出来的行
        # 用户拖动列宽（或拉伸列随窗口变化）也要重算行高
        self.table.horizontalHeader().sectionResized.connect(
            lambda *_: self._on_column_resized())

        # 反馈 #1 + S4：下方编辑区（双栏大视图 + 源图标签页，随选中联动）
        panel = QWidget()
        play = QVBoxLayout(panel)
        play.setContentsMargins(0, 4, 0, 0)
        self.detail_header = QLabel("选中段落后在下方编辑（表格仅用于选择段落）")
        self.detail_header.setWordWrap(True)

        # ---------- 左栏：源文（默认只读，OCR / 分段错误可放开） ----------
        self.detail_src = QPlainTextEdit()
        self.detail_src.setReadOnly(True)
        self.detail_src.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.src_edit_check = QCheckBox("允许编辑原文")
        self.src_edit_check.setToolTip(
            "导入文档的原文默认只读：导出时会重新解析源文件，改原文不会进入导出结果。\n"
            "OCR 识别错误、或分段不对时勾选它即可修正原文；修正后该段会标 ⚑ 待复核，\n"
            "要按新原文重译请再点「标记重译选中」。")
        self.src_state = QLabel("只读")
        src_head = QHBoxLayout()
        src_head.setContentsMargins(0, 0, 0, 0)
        src_head.addWidget(QLabel("源文（原文）"))
        src_head.addWidget(self.src_edit_check)
        src_head.addWidget(self.src_state)
        src_head.addStretch(1)
        src_box = QWidget()
        src_lay = QVBoxLayout(src_box)
        src_lay.setContentsMargins(0, 0, 0, 0)
        src_lay.setSpacing(2)
        src_lay.addLayout(src_head)
        src_lay.addWidget(self.detail_src, 1)

        # ---------- 右栏：译文（可直接编辑） ----------
        self.detail_tgt = QPlainTextEdit()
        self.detail_tgt.setReadOnly(False)
        self.detail_tgt.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.detail_tgt.setPlaceholderText("在此编辑译文（自动保存）…")
        tgt_head = QHBoxLayout()
        tgt_head.setContentsMargins(0, 0, 0, 0)
        tgt_head.addWidget(QLabel("译文（在此编辑，自动保存）"))
        tgt_head.addStretch(1)
        tgt_box = QWidget()
        tgt_lay = QVBoxLayout(tgt_box)
        tgt_lay.setContentsMargins(0, 0, 0, 0)
        tgt_lay.setSpacing(2)
        tgt_lay.addLayout(tgt_head)
        tgt_lay.addWidget(self.detail_tgt, 1)

        text_split = QSplitter(Qt.Orientation.Horizontal)
        text_split.addWidget(src_box)
        text_split.addWidget(tgt_box)
        text_split.setChildrenCollapsible(False)
        text_split.setHandleWidth(8)
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

        # 编辑区标题栏：定位信息 + 保存状态 + 保存按钮
        self.edit_state = QLabel("")
        self.save_btn = QPushButton("保存 (Ctrl+S)")
        self.save_btn.setEnabled(False)
        head = QHBoxLayout()
        head.addWidget(self.detail_header, 1)
        head.addWidget(self.edit_state)
        head.addWidget(self.save_btn)
        play.addLayout(head)
        play.addWidget(self.detail_tabs, 1)

        # 原文可编辑时才出现的提示：改动的作用范围（反馈 2026-09-29 #1 的「分情况」说明）
        self.detail_hint = QLabel(
            "⚠ 原文改动只在本软件内生效（用于修正 OCR / 分段错误并按新原文重新送翻）；"
            "导出会重新解析源文件，仍以源文件为准。")
        self.detail_hint.setWordWrap(True)
        self.detail_hint.setStyleSheet("color:#b7791f;")
        self.detail_hint.setVisible(False)
        play.addWidget(self.detail_hint)

        # 反馈 2026-09-29 #6：段落内术语提示 —— 新增/改过术语后，本段译文没跟上就当场提示
        # 并给出「替换 / 标记重译 / 忽略」三个出口，不用再去翻批量报表。
        self.term_bar = QWidget()
        term_row = QHBoxLayout(self.term_bar)
        term_row.setContentsMargins(0, 0, 0, 0)
        self.term_hint = QLabel("")
        self.term_hint.setWordWrap(True)
        self.term_hint.setStyleSheet("color:#b7791f;")
        self.term_replace_btn = QPushButton("替换")
        self.term_retrans_btn = QPushButton("标记重译本段")
        self.term_ignore_btn = QPushButton("本次忽略")
        self.term_ignore_btn.setToolTip("本段不再提示这个术语（只影响本次会话的提示，不改数据）")
        term_row.addWidget(self.term_hint, 1)
        term_row.addWidget(self.term_replace_btn)
        term_row.addWidget(self.term_retrans_btn)
        term_row.addWidget(self.term_ignore_btn)
        self.term_bar.setVisible(False)
        play.addWidget(self.term_bar)

        # ---------- 上下分栏：拖动分隔条即可改变编辑区高度（反馈 2026-09-29 #2） ----------
        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.addWidget(self.table)
        vsplit.addWidget(panel)
        vsplit.setChildrenCollapsible(False)
        vsplit.setHandleWidth(10)         # 默认手柄太细，抓不住
        # 拖动时两块**实时跟随光标**（Qt 默认值，写出来是因为它决定了"有没有跟手动画"）
        vsplit.setOpaqueResize(True)
        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 2)
        # 手柄画成可见的浅色条，鼠标移上去变深 —— 明确告诉用户「这里能拖」
        vsplit.setStyleSheet(self.HANDLE_QSS)
        self.table.setMinimumHeight(90)
        panel.setMinimumHeight(140)
        self.vsplit = vsplit
        lay.addWidget(vsplit, 1)
        vsplit.handle(1).setToolTip("拖动调整编辑区高度（双击恢复默认比例）")
        vsplit.handle(1).installEventFilter(self)
        self._restore_layout()

        self.table.selectionModel().currentRowChanged.connect(
            lambda *_: self._show_detail())
        # 双击某段：直接把光标送进译文编辑框（保留「双击就改」的习惯）
        self.table.doubleClicked.connect(lambda *_: self.detail_tgt.setFocus())

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
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(
            lambda: self._flush(announce=True))
        self.save_btn.clicked.connect(lambda: self._flush(announce=True))
        self.term_replace_btn.clicked.connect(self._term_replace_current)
        self.term_retrans_btn.clicked.connect(self._term_retranslate_current)
        self.term_ignore_btn.clicked.connect(self._term_ignore_current)

        # 编辑区自动保存：防抖 + 失焦即存（eventFilter）+ 切段/刷新/切页前落库
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.AUTOSAVE_MS)
        self._save_timer.timeout.connect(self._flush)
        self.detail_tgt.textChanged.connect(lambda: self._on_edit_changed("tgt"))
        self.detail_src.textChanged.connect(lambda: self._on_edit_changed("src"))
        for w in (self.detail_src, self.detail_tgt):
            w.installEventFilter(self)
        self.src_edit_check.toggled.connect(self._on_src_editable_toggled)
        self._set_src_check(bool(self.ctx.cfg.get("review_allow_src_edit", False)))
        self._apply_src_readonly()
        self._set_save_state("")

        # 拖动分隔条后把比例记到全局配置（防抖，别每像素写一次盘）
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(400)
        self._layout_timer.timeout.connect(self._save_layout)
        vsplit.splitterMoved.connect(lambda *_: self._layout_timer.start())

    # ---------- 布局（反馈 2026-09-29 #2：编辑区高度可拖拽 + 记住） ----------
    def _restore_layout(self) -> None:
        sizes = (self.ctx.cfg.get("review_layout") or {}).get("vsplit")
        if not (isinstance(sizes, list) and len(sizes) == 2
                and all(isinstance(s, int) and s > 0 for s in sizes)):
            sizes = list(self.DEFAULT_VSPLIT)
        self.vsplit.setSizes(sizes)

    def _save_layout(self) -> None:
        self.ctx.cfg["review_layout"] = {"vsplit": self.vsplit.sizes()}
        self.ctx.save_cfg()

    # ---------- 行高重算策略（反馈 2026-09-29 #4 + 超大项目规模修正） ----------
    # 症状：拖上下分隔条不跟手（每次鼠标移动 88 段 24ms、2000 段 570ms，一帧只有 16.7ms）。
    # 根因：纵向表头用 ResizeToContents 时，**视口尺寸一变**就把每一行的 sizeHint 全算一遍
    #       （每行都要做一次带自动换行的文本排版，实测约 120µs/行），而纵向拖动并不改变
    #       换行宽度，这些重算纯属浪费。
    # 但"改成每次刷新全量重算"在超大项目上同样不能要：20000 段一次刷新 7.5 秒
    #       （100000 段更不敢想），而校对页改一次筛选、敲一个搜索字都会刷新。
    # 所以现在是两段式：
    #   ① 表头 Fixed + **默认单行高**兜底 —— 不需要"先量完全表"才有布局；
    #   ② 只量**看得见的那一段**（视口可见行，防抖后再补量上下各 VISIBLE_MARGIN 行，
    #      且分片进行）。代价从 O(总段数) 降到 O(可见行数)：20000 段刷新 ~10ms。
    #   ③ 只有**宽度真的变了**（窗口缩放/列宽变化）才作废已量结果，因为只有宽度影响换行。
    # 取舍：还没滚到的行先按单行高显示（长段落在表格里会被截断，全文在下方编辑区），
    #       滚到附近时立刻补量为真实高度。
    VISIBLE_MARGIN = 40        # 视口外上下各多量多少行（滚动时就不会"先截断再撑开"）
    MEASURE_CHUNK = 300        # 一次防抖最多补量多少行，剩下的下一拍接着量

    def _visible_rows(self) -> tuple[int, int]:
        """当前视口内的行区间 [first, last]（空表返回 (0, -1)）。"""
        n = len(self.model.rows)
        if n == 0:
            return 0, -1
        first = self.table.rowAt(0)
        last = self.table.rowAt(max(0, self.table.viewport().height() - 1))
        if first < 0:                      # 还没布局出来，先给一小段
            first = 0
        if last < 0:
            last = min(n - 1, first + self.VISIBLE_MARGIN)
        return max(0, first), min(n - 1, last)

    def _measure_rows(self, first: int, last: int, cap: int | None = None) -> bool:
        """量 [first, last] 里还没量过的行高；返回是否因为 cap 提前收手。"""
        todo = [r for r in range(first, last + 1) if r not in self._measured_rows]
        capped = cap is not None and len(todo) > cap
        if capped:
            todo = todo[:cap]
        for r in todo:
            self.table.resizeRowToContents(r)
        self._measured_rows.update(todo)
        return capped

    def _measure_visible_rows(self, with_margin: bool = False) -> None:
        """量可见行的行高（增量：只量没量过的，通常只有几行）。

        `with_margin=True` 时顺带补量视口上下各 VISIBLE_MARGIN 行，并且分片 ——
        单次调用始终是毫秒级，剩下一拍由防抖计时器接着补。
        """
        if not self.model.rows:
            return
        self._row_width = self.table.viewport().width()
        self._text_cols_width = self._text_columns_width()
        first, last = self._visible_rows()
        self._measure_rows(first, last)
        if not with_margin:
            return
        more = self._measure_rows(max(0, first - self.VISIBLE_MARGIN),
                                  min(len(self.model.rows) - 1, last + self.VISIBLE_MARGIN),
                                  cap=self.MEASURE_CHUNK)
        if more:
            self._schedule_row_heights()   # 还没补完，下一拍继续

    def _text_columns_width(self) -> int:
        """两个文本列的总宽 —— 它决定自动换行，变了才需要重量行高。"""
        return (self.table.columnWidth(SegmentsModel.SRC_COL)
                + self.table.columnWidth(SegmentsModel.EDIT_COL))

    def _schedule_row_heights(self) -> None:
        self._height_timer.start()

    def _on_column_resized(self) -> None:
        """用户拖了列宽：换行宽度变了就得作废已量行高（防抖后重量可见区间）。

        只有**文本列**的宽度真的变了才算 —— Stretch 列在每次布局时都会重算并触发
        `sectionResized`，不比对的话纵向拖动会把量好的行高反复清掉。
        """
        if self._text_columns_width() == self._text_cols_width:
            return
        self._text_cols_width = self._text_columns_width()
        self._measured_rows.clear()
        self._schedule_row_heights()

    def _reload_row_heights(self) -> None:
        """数据换了：作废已量结果，重新量可见区间（O(可见行数)，与总段数无关）。"""
        self._height_timer.stop()
        if not self.model.rows:
            self._measured_rows.clear()
            return
        self._measured_rows.clear()
        self.table.verticalHeader().setDefaultSectionSize(self._default_row_height())
        self._measure_visible_rows(with_margin=True)

    def _default_row_height(self) -> int:
        """没量过的行的兜底高度：按单行文字算（多出来的部分滚到附近时会补量）。"""
        return self.table.fontMetrics().height() + 8

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonDblClick and obj is self.vsplit.handle(1):
            self.vsplit.setSizes(list(self.DEFAULT_VSPLIT))   # 双击分隔条复位
            return True
        if event.type() == QEvent.Type.Resize and obj is self.table.viewport():
            if self.table.viewport().width() != self._row_width:
                # 宽度变了 → 换行宽度变了，已量的行高全部作废（等尺寸停下来再重量，
                # 免得拖窗口时每个像素都量一遍）
                self._height_timer.stop()
                self._measured_rows.clear()
                self._row_width = self.table.viewport().width()
                self.table.verticalHeader().setDefaultSectionSize(self._default_row_height())
                self._schedule_row_heights()
            else:
                # 只是变高/变矮（拖分隔条、上下缩放）：补量新露出来的那几行即可
                self._measure_visible_rows()
        elif (event.type() == QEvent.Type.FocusOut
                and obj in (self.detail_src, self.detail_tgt)):
            self._flush()          # 点到别处即落库，不等防抖计时器
        return super().eventFilter(obj, event)

    # ---------- 编辑区：加载 / 保存（反馈 2026-09-29 #1） ----------
    def _on_edit_changed(self, which: str) -> None:
        if self._loading or self._cur_id is None:
            return
        if which == "tgt":
            self._dirty_tgt = True
        else:
            self._dirty_src = True
        self._set_save_state("未保存…", dirty=True)
        self._save_timer.start()

    def _set_save_state(self, text: str, dirty: bool = False) -> None:
        self.edit_state.setText(text)
        self.edit_state.setStyleSheet(
            "color:#b7791f;" if dirty else ("color:#2f855a;" if text else ""))
        self.save_btn.setEnabled(dirty)

    def _flush(self, announce: bool = False) -> bool:
        """把编辑区里未落库的改动写进库（切段/刷新/切页/失焦/防抖都会调）。"""
        self._save_timer.stop()
        if self.ctx.review is None or self._cur_id is None:
            return False
        if not (self._dirty_tgt or self._dirty_src):
            if announce:
                self.ctx.bridge.toast.emit("没有需要保存的改动")
            return False
        seg_id = self._cur_id
        if self._dirty_tgt:
            self._commit_tgt(seg_id, self.detail_tgt.toPlainText())
        if self._dirty_src:
            self._commit_src(seg_id, self.detail_src.toPlainText())
        self._dirty_tgt = self._dirty_src = False
        self._set_save_state(f"已保存 {self._now()}")
        if announce:
            self.ctx.bridge.toast.emit("已保存到当前段落")
        return True

    @staticmethod
    def _now() -> str:
        from datetime import datetime
        return datetime.now().strftime("%H:%M:%S")

    def _commit_tgt(self, seg_id: int, text: str) -> None:
        r = self.model.row_of(seg_id)
        if r is None:                      # 该段已不在当前筛选结果里
            self.ctx.review.edit(seg_id, text)
            return
        self.model.setData(self.model.index(r, SegmentsModel.EDIT_COL), text)
        self.table.resizeRowToContents(r)  # 文本变了，这一行的换行高度跟着变
        self._measured_rows.add(r)

    def _commit_src(self, seg_id: int, text: str) -> None:
        r = self.model.row_of(seg_id)
        if r is None:
            self.ctx.review.edit_src(seg_id, text)
            return
        self.model.set_source(self.model.index(r, SegmentsModel.SRC_COL), text)
        self.table.resizeRowToContents(r)
        self._measured_rows.add(r)
        self._update_detail_header(self.model.rows[r])   # ⚑ 待复核立刻显示出来

    def _load_into_editors(self, row: dict) -> None:
        self._loading = True
        try:
            self.detail_src.setPlainText(row["src"])
            self.detail_tgt.setPlainText(row["tgt"])
        finally:
            self._loading = False
        self._dirty_src = self._dirty_tgt = False
        self._set_save_state("")
        self.detail_src.moveCursor(self.detail_src.textCursor().MoveOperation.Start)
        self.detail_tgt.moveCursor(self.detail_tgt.textCursor().MoveOperation.Start)

    # ---------- 原文可编辑性（反馈 2026-09-29 #1：导入文档只读，OCR 自动放开） ----------
    def _set_src_check(self, checked: bool) -> None:
        self._suppress_toggle = True
        try:
            self.src_edit_check.setChecked(checked)
        finally:
            self._suppress_toggle = False

    def _on_src_editable_toggled(self, checked: bool) -> None:
        if self._suppress_toggle:
            return
        # 用户一旦手动拨过这个开关，就按用户的来（并记住偏好，下次会话也照办）：
        # 自动逻辑只负责「没表过态时」的默认值。
        self._src_auto = False
        self._src_manual = True
        self.ctx.cfg["review_allow_src_edit"] = bool(checked)
        self.ctx.save_cfg()
        self._apply_src_readonly()

    def _apply_src_readonly(self) -> None:
        editable = self.src_edit_check.isChecked()
        self.detail_src.setReadOnly(not editable)
        self.src_state.setText("可编辑（仅本软件内）" if editable else "只读")
        self.src_state.setStyleSheet("color:#b7791f;" if editable else "color:#718096;")
        self.detail_hint.setVisible(editable)

    def _sync_src_editable(self, row: dict | None) -> None:
        """按当前段的文档类型决定原文默认是否可改（反馈 2026-09-29 #1 的「分情况」）。

        - 用户手动表态过（含上次会话存下的偏好）→ 完全听用户的；
        - 否则：**OCR 文档自动放开**（识别错误得能改），普通导入文档保持只读
          （导出会重新解析源文件，改原文不会进导出结果）。
        """
        if not self._src_manual:
            doc = None
            if row is not None and self.ctx.project is not None:
                try:
                    doc = self.ctx.project.db.get_document(row["doc_id"])
                except Exception:  # noqa: BLE001 拿不到文档信息不影响校对
                    doc = None
            is_ocr = bool(doc is not None and doc["format"] == "img")
            if is_ocr and not self.src_edit_check.isChecked():
                self._src_auto = True
                self._set_src_check(True)
            elif not is_ocr and self._src_auto:
                self._src_auto = False
                self._set_src_check(False)
        self._apply_src_readonly()


    # ---------- 数据 ----------
    def on_enter(self):
        super().on_enter()
        if self.ctx.project:
            self._rebuild_doc_combo()
            self.refresh()

    def on_project(self):
        self.on_enter()

    def on_leave(self):
        """离开校对页 / 关窗前落库（主窗体在 closeEvent 里调用）。"""
        self._flush()

    def hideEvent(self, event):
        self._flush()          # 切到别的页面也把编辑区未保存的改动写下去
        super().hideEvent(event)

    def _rebuild_doc_combo(self):
        self.doc_combo.blockSignals(True)
        self.doc_combo.clear()
        self.doc_combo.addItem("全部文档", None)
        for d in self.ctx.project.db.list_documents():
            self.doc_combo.addItem(d["path"], d["id"])
        self.doc_combo.blockSignals(False)

    def _persist_edit(self, seg_id: int, text: str) -> None:
        """编辑区译文落库（ReviewService.edit：human_edited + 清除待复核）。"""
        if self.ctx.review is not None:
            self.ctx.review.edit(seg_id, text)

    def _persist_src_edit(self, seg_id: int, text: str) -> None:
        """编辑区原文落库（反馈 2026-09-29 #1：OCR / 分段错误修正）。"""
        if self.ctx.review is not None:
            self.ctx.review.edit_src(seg_id, text)

    def refresh(self):
        """重新按筛选条件取段。

        反馈 2026-09-29 #1：刷新前先把编辑区改动落库，刷新后**恢复原来的选中行** ——
        否则每敲一次搜索、每切一次筛选，用户正在校对的段就被顶掉了。
        """
        self._flush()
        keep = self._cur_id
        self._sync_confirm_btn()
        if not self.ctx.review:
            return
        doc_id = self.doc_combo.currentData()
        rows = self.ctx.review.segments(
            doc_id=doc_id, status_filter=self.filter.currentText(),
            search=self.search.text().strip() or None)
        self.model.set_rows(rows)
        self.count_label.setText(f"{len(rows)} 段")
        self._old_terms = None         # 术语可能刚改过，重新取一次「上一版候选」
        self._reload_row_heights()     # 换了数据：作废已量行高，只重量可见区间
        self._select_seg(keep)

    def _select_seg(self, seg_id: int | None) -> None:
        """按段 id 选中对应行（找不到就清空编辑区）。"""
        r = self.model.row_of(seg_id) if seg_id is not None else None
        if r is None:
            self._show_detail()
            return
        self.table.selectRow(r)
        self._show_detail()        # 选中行没变时 currentRowChanged 不发信号

    def _show_detail(self):
        """下方编辑区：当前段的双栏全文与定位信息（反馈 #1/#4 + S4 源图预览）。

        可编辑与自动保存见「反馈 2026-09-29 #1」，高度拖拽见 #2。
        """
        idx = self.table.currentIndex()
        if not idx.isValid() or not self.model.rows:
            self._flush()          # 编辑区还停在上一段时要先落库
            self._cur_id = None
            self._cur_row = None
            self.detail_header.setText("选中段落后在下方编辑（表格仅用于选择段落）")
            self._loading = True
            try:
                self.detail_src.clear()
                self.detail_tgt.clear()
            finally:
                self._loading = False
            self._dirty_src = self._dirty_tgt = False
            self._set_save_state("")
            self.detail_tgt.setReadOnly(True)      # 没选中段落时不给编辑
            self.detail_tgt.setPlaceholderText("先在上方表格里选中一个段落…")
            self.image_label.clear()
            self.image_label.setText("（此段非图片来源，或图片文件不存在）")
            return
        row = self.model.rows[idx.row()]
        if row["id"] != self._cur_id:
            self._flush()          # 切段前把上一段的改动写下去
            self._cur_id = row["id"]
            self._load_into_editors(row)
        elif not (self._dirty_tgt or self._dirty_src):
            # 还是同一段、但内容被别处改过（刷新 / 术语批量替换 / 外部改动）：
            # 没有未保存的改动就跟上库里的最新文本 —— 否则编辑区留着旧文本，
            # 用户下一次改动一落库就把别处的修正覆盖掉了。
            self._load_into_editors(row)
        self._cur_row = row
        self._update_detail_header(row)
        self._sync_src_editable(row)
        self._update_term_hint(row)      # 反馈 2026-09-29 #6：段落内术语提示
        self.detail_tgt.setReadOnly(False)
        # S4：源图预览
        self._load_source_image(row)

    def _update_detail_header(self, row: dict) -> None:
        status = STATUS_LABEL.get(row["status"], row["status"])
        if row["review_flag"]:
            status += " ⚑待复核"
        self.detail_header.setText(
            f"当前：{row['doc_path']} 第 {row['seq']} 段（{status}）"
            "　—　右侧可直接编辑译文，自动保存")
        self.detail_tgt.setPlaceholderText("在此编辑译文（自动保存）…")

    # ---------- 段落内术语提示（反馈 2026-09-29 #6） ----------
    def _old_term_state(self) -> dict:
        """术语的「上一版候选」（用于发现译文里残留的旧译名）。

        每次 refresh() 取一次并缓存 —— 段落内提示每次换段都要判断，不能每段都查库。
        """
        if self._old_terms is None:
            svc = getattr(self.ctx, "term_impact", None)
            try:
                self._old_terms = svc.old_state()[0] if svc is not None else {}
            except Exception:  # noqa: BLE001 拿不到历史就只做「新术语」判断
                self._old_terms = {}
        return self._old_terms

    def _update_term_hint(self, row: dict | None) -> None:
        """本段的术语提示：译文里还留着旧译名 / 源文有术语但译文没跟上。"""
        self._term_issues = []
        if row is None or self.ctx.project is None:
            self.term_bar.setVisible(False)
            return
        try:
            entries = self.ctx.project.glossary.entries
        except Exception:  # noqa: BLE001 术语表读不到不影响校对
            entries = []
        issues = [i for i in segment_term_issues(row["src"], row["tgt"], entries,
                                                 self._old_term_state())
                  if (row["id"], i["term"]) not in self._term_ignored]
        self._term_issues = issues
        if not issues:
            self.term_bar.setVisible(False)
            return
        it = issues[0]
        if it["kind"] == "old":
            text = (f"⚑ 术语：本段应把旧译名「{it['old']}」改成「{it['new']}」"
                    f"（术语「{it['term']}」改过译名）。")
        else:
            text = (f"⚑ 术语：本段含「{it['term']}」，约定译作「{it['new']}」，"
                    "但译文里没有出现（新增/改过的术语，需要订正或重译）。")
        if len(issues) > 1:
            text += f"　另有 {len(issues) - 1} 个术语待核对。"
        self.term_hint.setText(text)
        self.term_replace_btn.setVisible(it["kind"] == "old")
        self.term_replace_btn.setText(f"替换为「{it['new']}」")
        self.term_bar.setVisible(True)

    def _term_replace_current(self) -> None:
        """把本段译文里的旧译名替换为新候选（写进编辑区并立即落库）。"""
        if self._cur_row is None or not self._term_issues:
            return
        it = self._term_issues[0]
        if it["kind"] != "old" or not it["old"] or not it["new"]:
            return
        text = self.detail_tgt.toPlainText()
        new_text = text.replace(it["old"], it["new"])
        if new_text != text:
            self.detail_tgt.setPlainText(new_text)   # 触发 dirty → _flush 落库
            self._flush()
            self.ctx.bridge.toast.emit(
                f"已把「{it['old']}」替换为「{it['new']}」（可用「搜索替换」批量处理同类段）")
        row = self._cur_row
        row["tgt"] = self.detail_tgt.toPlainText()
        self._update_term_hint(row)

    def _term_retranslate_current(self) -> None:
        """本段标记重译：下次「开始翻译」会用新术语重新生成。"""
        if self._cur_row is None:
            return
        self._flush()
        self.ctx.review.mark_retranslate([self._cur_row["id"]])
        self.ctx.bridge.toast.emit("已把本段标记为待重译（下次「开始翻译」按新术语重新生成）")
        self.refresh()

    def _term_ignore_current(self) -> None:
        """本段不再提示这个术语（只影响本次会话的提示，不改数据）。"""
        if self._cur_row is None or not self._term_issues:
            return
        for it in self._term_issues:
            self._term_ignored.add((self._cur_row["id"], it["term"]))
        self._update_term_hint(self._cur_row)

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
        """确认选中的段落（反馈 2026-09-29 #5：确认不了必须说明原因，不能静默无反应）。"""
        self._flush()          # 先把编辑区改动落库，再确认（否则确认的是旧文本）
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "确认选中", "先在表格里选中要确认的段落。")
            return
        done = sum(1 for r in rows if self.ctx.review.confirm(r["id"]))
        self.refresh()
        if done:
            msg = f"已确认 {done} 段"
            if done < len(rows):
                msg += f"；{len(rows) - done} 段没确认（{self._confirm_block_reason(rows)}）"
            self.ctx.bridge.toast.emit(msg)
        else:
            QMessageBox.information(
                self, "没有段落被确认",
                f"选中的 {len(rows)} 段都没能确认：{self._confirm_block_reason(rows)}。\n\n"
                "可确认的条件是「已有译文且还没有确认」：\n"
                "· 未译 / 失败的段落 —— 请先「开始翻译」，或直接在下方编辑区填写译文"
                "（填写后会自动变成「已修改」，再确认即可）；\n"
                "· 已经是「已确认」的段落不需要重复确认。")

    @staticmethod
    def _confirm_block_reason(rows: list[dict]) -> str:
        """没能确认的原因（按实际情况拼，别再让用户猜）。"""
        empty = sum(1 for r in rows if not (r["tgt"] or "").strip())
        already = sum(1 for r in rows if r["status"] == "confirmed")
        bits = []
        if empty:
            bits.append(f"{empty} 段还没有译文")
        if already:
            bits.append(f"{already} 段已经是「已确认」")
        return "、".join(bits) or "没有可确认的段落"

    def _confirm_and_next(self):
        """Ctrl+Enter：确认当前段并跳到下一段。

        确认不了时**留在原地并说明原因**（原来对空译文段是静默什么都不做，
        用户以为按键坏了；见反馈 2026-09-29 #5）。
        """
        self._flush()
        idx = self.table.currentIndex()
        if not idx.isValid():
            self.ctx.bridge.toast.emit("先在表格里选中一个段落，再按 Ctrl+Enter")
            return
        row = self.model.rows[idx.row()]
        if self.ctx.review.confirm(row["id"]):
            self.ctx.bridge.toast.emit(f"第 {row['seq']} 段已确认")
            advance = True
        elif not (row["tgt"] or "").strip():
            self.ctx.bridge.toast.emit(
                f"第 {row['seq']} 段还没有译文，无法确认 —— 请在下方编辑区填写后再按 Ctrl+Enter")
            advance = False        # 留在原地，方便就地补译文
        else:
            self.ctx.bridge.toast.emit(f"第 {row['seq']} 段已经是「已确认」")
            advance = True
        if advance and idx.row() + 1 < len(self.model.rows):
            self.table.selectRow(idx.row() + 1)
        self.refresh()

    def _confirm_doc(self):
        """确认整个文档；文档下拉为「全部文档」时即确认全部文档。

        原实现是 `if doc_id:` —— 而文档下拉的默认值就是「全部文档」(data=None)，
        所以点这个按钮**永远静默无操作**（用户反馈：按键无效）。
        """
        doc_id = self.doc_combo.currentData()
        scope = self.doc_combo.currentText()
        self._flush()          # 编辑区里未落库的改动不能漏掉
        todo = self._confirmable_count(doc_id)
        if todo == 0:
            QMessageBox.information(
                self, "确认全部",
                f"「{scope}」范围内没有可确认的段落。\n\n"
                "可确认 = 已有译文且还没确认；未译 / 失败的段落请先「开始翻译」。")
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
        """待确认段数（口径与 ReviewService.confirm 完全一致，避免"说有 N 段却一段没确认"）。"""
        return self.ctx.review.confirmable_count(doc_id)

    def _sync_confirm_btn(self) -> None:
        """按钮文案跟随文档下拉，避免用户以为它只作用于当前文档。"""
        self.confirm_doc_btn.setText(
            "确认当前文档全部" if self.doc_combo.currentData() is not None
            else "确认全部文档")

    def _retranslate(self):
        self._flush()          # 编辑区改动先落库，别被「标记重译」覆盖掉
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
        self._flush()          # 注音编辑读的是最新译文
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
