"""项目工作台（设计 §9-2）：文件列表 + 标签 + 术语表 + 开始翻译（预检向导）。"""
from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
)

from app.pages.base import CtxPage


class TagsDialog(QDialog):
    """文件标签（设计 FR-04 + 增补设计 §1.8 文件级振假名策略覆盖）。"""

    def __init__(self, parent, tags: dict):
        super().__init__(parent)
        self.setWindowTitle("编辑文件标签")
        lay = QFormLayout(self)
        self.genre = QLineEdit(tags.get("genre", ""))
        self.style = QLineEdit(tags.get("style", ""))
        self.notes = QLineEdit(tags.get("notes", ""))
        self.instruction = QTextEdit(tags.get("instruction", ""))
        self.instruction.setFixedHeight(80)
        self.ruby = QComboBox()
        self.ruby.addItem("继承项目设置", "")
        self.ruby.addItem("drop（丢弃注音）", "drop")
        self.ruby.addItem("keep（保留原假名）", "keep")
        self.ruby.addItem("translate（注音也翻译）", "translate")
        cur = tags.get("ruby_policy", "")
        i = self.ruby.findData(cur)
        self.ruby.setCurrentIndex(i if i >= 0 else 0)
        lay.addRow("作品类型（如：奇幻小说）", self.genre)
        lay.addRow("语言风格（如：网文口语）", self.style)
        lay.addRow("格式注意事项", self.notes)
        lay.addRow("补充翻译指令", self.instruction)
        lay.addRow("振假名策略（文件级覆盖）", self.ruby)
        btn = QPushButton("保存")
        btn.clicked.connect(self.accept)
        lay.addRow(btn)

    def result_tags(self) -> dict:
        tags = {"genre": self.genre.text().strip(),
                "style": self.style.text().strip(),
                "notes": self.notes.text().strip(),
                "instruction": self.instruction.toPlainText().strip()}
        if self.ruby.currentData():
            tags["ruby_policy"] = self.ruby.currentData()
        return {k: v for k, v in tags.items() if v}


class PrecheckDialog(QDialog):
    """开始翻译预检（设计 §7.5 / v0.7 #42）：cfg 比对 + 成本预估 + 术语归纳选项。"""

    def __init__(self, parent, pre: dict):
        super().__init__(parent)
        self.setWindowTitle("开始翻译 · 预检")
        lay = QVBoxLayout(self)
        est = pre.get("estimate")
        lines = [
            f"待翻译段落：{pre['pending']} 段（{pre['chars']} 字符）",
            f"术语表：{pre['glossary_terms']} 条（有效）",
            f"配置过期译文：{pre['stale_translations']} 段（默认保留，可稍后在项目设置中重译）",
            f"上下文滑窗：前 {pre.get('slide', 0)} + 后 {pre.get('slide', 0)} 段 | "
            f"振假名策略：{pre.get('ruby_policy', 'drop')}",
        ]
        if est:
            lines.append(f"token 预估：输入约 {est['tokens_in']}~{int(est['tokens_in'] * 1.35)}，"
                         f"输出约 {est['tokens_out']}")
        else:
            lines.append("没有待翻译段落。")
        lay.addWidget(QLabel("\n".join(lines)))
        new_docs = len(pre.get("terms_extracted_docs") or [])
        self.induce = QCheckBox(f"先自动归纳术语（{new_docs} 个新文件，阶段一本地免费，阶段二调用 AI）")
        self.induce.setChecked(new_docs > 0)
        self.induce.setEnabled(new_docs > 0)
        lay.addWidget(self.induce)
        self.ok_btn = QPushButton("确认开始")
        self.ok_btn.setEnabled(pre["pending"] > 0 or self.induce.isEnabled())
        self.ok_btn.clicked.connect(self.accept)
        lay.addWidget(self.ok_btn)


class WorkbenchPage(CtxPage):
    def __init__(self, ctx, main):
        super().__init__(ctx, main)
        lay = QVBoxLayout(self)

        # 文件区
        file_bar = QHBoxLayout()
        import_btn = QPushButton("导入文件（复制到 source/）")
        import_btn.clicked.connect(self._import)
        tag_btn = QPushButton("编辑标签")
        tag_btn.clicked.connect(self._edit_tags)
        rm_btn = QPushButton("移除文件")
        rm_btn.clicked.connect(self._remove_doc)
        gloss_ext_btn = QPushButton("检查术语表外部修改")
        gloss_ext_btn.clicked.connect(self._reload_glossary)
        for b in (import_btn, tag_btn, rm_btn, gloss_ext_btn):
            file_bar.addWidget(b)
        file_bar.addStretch(1)
        lay.addLayout(file_bar)
        self.doc_table = QTableWidget(0, 5)
        self.doc_table.setHorizontalHeaderLabels(["文件", "格式", "可译段", "状态", "标签"])
        self.doc_table.setColumnWidth(0, 300)
        lay.addWidget(self.doc_table, 3)

        # 术语表区
        gloss_bar = QHBoxLayout()
        gloss_bar.addWidget(QLabel("术语表（glossary.*，可直接在 Excel 编辑）"))
        add_btn = QPushButton("新增术语")
        edit_btn = QPushButton("编辑")
        del_btn = QPushButton("删除")
        merge_btn = QPushButton("合并导入…")
        for b in (add_btn, edit_btn, del_btn, merge_btn):
            gloss_bar.addWidget(b)
        gloss_bar.addStretch(1)
        add_btn.clicked.connect(lambda: self._edit_term(None))
        edit_btn.clicked.connect(lambda: self._edit_term(self._current_term()))
        del_btn.clicked.connect(self._del_term)
        merge_btn.clicked.connect(self._merge_terms)
        lay.addLayout(gloss_bar)
        self.gloss_table = QTableWidget(0, 3)
        self.gloss_table.setHorizontalHeaderLabels(["源语言", "目标语（| 分隔，≤3）", "注释"])
        lay.addWidget(self.gloss_table, 2)

        start_btn = QPushButton("开始翻译")
        start_btn.clicked.connect(self._start)
        lay.addWidget(start_btn)

    # ---------- 文件 ----------
    def on_enter(self):
        super().on_enter()
        if self.ctx.project:
            self._refresh_docs()
            self._refresh_gloss()

    def on_project(self):
        self.on_enter()

    def _refresh_docs(self):
        db = self.ctx.project.db
        docs = db.list_documents()
        self.doc_table.setRowCount(len(docs))
        for i, d in enumerate(docs):
            segs = db.list_segments(doc_id=d["id"], translatable=True)
            done = sum(1 for s in segs if s["status"] in ("machine_translated", "human_edited", "confirmed"))
            try:
                tags = ", ".join(f"{k}={v}" for k, v in json.loads(d["tags"]).items())
            except Exception:
                tags = ""
            vals = [d["path"], d["format"], str(len(segs)),
                    f"{d['status']}（{done}/{len(segs)}）", tags]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setData(1, d["id"])
                self.doc_table.setItem(i, j, item)

    def _current_doc(self):
        item = self.doc_table.currentItem()
        return item.data(1) if item else None

    def _import(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择源文件", str(self.ctx.project.source_dir()),
            "支持的格式 (*.txt *.md *.html *.htm *.docx *.png *.jpg *.jpeg);;全部文件 (*)")
        if not files:
            return
        ok, errs = self.ctx.importer.import_files(files)
        msg = f"导入 {len(ok)} 个文件"
        if errs:
            msg += "\n失败：\n" + "\n".join(f"{e['file']}：{e['error']}" for e in errs)
            QMessageBox.warning(self, "导入结果", msg)
        else:
            self.ctx.bridge.toast.emit(msg)
        self._refresh_docs()

    def _edit_tags(self):
        doc_id = self._current_doc()
        if not doc_id:
            return
        row = self.ctx.project.db.get_document(doc_id)
        tags = json.loads(row["tags"] or "{}")
        dlg = TagsDialog(self, tags)
        if dlg.exec():
            self.ctx.importer.set_tags(doc_id, dlg.result_tags())
            self._refresh_docs()

    def _remove_doc(self):
        doc_id = self._current_doc()
        if doc_id and QMessageBox.question(self, "移除文件", "从项目中移除该文件（source/ 内文件保留）？") == QMessageBox.StandardButton.Yes:
            self.ctx.importer.remove_file(doc_id)
            self._refresh_docs()

    # ---------- 术语表 ----------
    def _refresh_gloss(self):
        entries = self.ctx.project.glossary.entries
        self.gloss_table.setRowCount(len(entries))
        for i, t in enumerate(entries):
            for j, v in enumerate([t.src, "|".join(t.candidates), t.note]):
                self.gloss_table.setItem(i, j, QTableWidgetItem(v))

    def _current_term(self):
        item = self.gloss_table.currentItem()
        if not item:
            return None
        return self.gloss_table.item(item.row(), 0).text()

    def _edit_term(self, src: str | None):
        g = self.ctx.project.glossary
        term = g.get(src) if src else None
        dlg = QDialog(self)
        dlg.setWindowTitle("术语")
        form = QFormLayout(dlg)
        src_edit = QLineEdit(term.src if term else "")
        cand_edit = QLineEdit("|".join(term.candidates) if term else "")
        note_edit = QLineEdit(term.note if term else "")
        form.addRow("源词", src_edit)
        form.addRow("候选（| 分隔，≤3）", cand_edit)
        form.addRow("注释", note_edit)
        save = QPushButton("保存")
        save.clicked.connect(dlg.accept)
        form.addRow(save)
        if dlg.exec():
            try:
                g.add(src_edit.text(), cand_edit.text().split("|"), note_edit.text())
                g.save()
                self._snapshot_terms()
                self._refresh_gloss()
            except ValueError as e:
                QMessageBox.warning(self, "保存失败", str(e))

    def _del_term(self):
        src = self._current_term()
        if src:
            self.ctx.project.glossary.remove(src)
            self.ctx.project.glossary.save()
            self._snapshot_terms()
            self._refresh_gloss()

    def _merge_terms(self):
        f, _ = QFileDialog.getOpenFileName(self, "合并术语表", "", "glossary (*.csv *.txt)")
        if f:
            n = self.ctx.project.glossary.import_merge(f)
            self.ctx.project.glossary.save()
            self._snapshot_terms()
            self._refresh_gloss()
            self.ctx.bridge.toast.emit(f"合并 {n} 条新术语")

    def _reload_glossary(self):
        g = self.ctx.project.glossary
        if g.external_changed():
            g.load()
            self._refresh_gloss()
            self.ctx.bridge.toast.emit("已重新加载外部修改的术语表")
        else:
            self.ctx.bridge.toast.emit("术语表无外部修改")

    def _snapshot_terms(self):
        """保存后落 DB 快照（术语变更影响分析依赖，设计 v0.4 #25）。"""
        db = self.ctx.project.db
        g = self.ctx.project.glossary
        db.save_terms_revision(g.effective_hash(), g.to_payload())
        for t in g.entries:
            db.upsert_term(t.src, t.candidates, t.note, origin="manual", status="approved")

    # ---------- 开始翻译 ----------
    def _start(self):
        pre = self.ctx.translation.precheck()
        dlg = PrecheckDialog(self, pre)
        if not dlg.exec():
            return
        if dlg.induce.isEnabled() and dlg.induce.isChecked():
            if not self._run_induction():
                return  # 归纳失败/暂停 → 不继续烧翻译费
        try:
            self.ctx.run_handle = self.ctx.translation.start(
                on_progress=self.ctx.bridge.progress.emit,
                on_done=self.ctx.bridge.run_done.emit)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "无法启动", str(e))
            return
        self.main.goto("翻译进度")

    def _run_induction(self) -> bool:
        """后台跑术语归纳并弹出审核；返回是否继续后续翻译。"""
        import threading

        from PySide6.QtCore import QObject, Qt as _Qt, Signal
        from PySide6.QtWidgets import QProgressDialog

        from app.pages.terms_dialog import TermsReviewDialog
        from core.term_induction import InductionService

        class _Sig(QObject):
            done = Signal(dict)

        provider = self.ctx.translation.build_provider()
        service = InductionService(self.ctx.project, provider)
        sig = _Sig()
        result_box: dict = {}

        def work():
            try:
                result_box["res"] = service.run()
            except Exception as e:  # noqa: BLE001
                result_box["err"] = str(e)
            sig.done.emit(result_box)

        prog = QProgressDialog("正在归纳术语（阶段一本地统计 → 阶段二 AI 判定）…",
                               None, 0, 0, self)
        prog.setWindowTitle("术语归纳")
        prog.setWindowModality(_Qt.WindowModality.WindowModal)
        sig.done.connect(lambda _: prog.close())
        threading.Thread(target=work, daemon=True).start()
        prog.exec()
        if "err" in result_box:
            QMessageBox.warning(self, "术语归纳失败", result_box["err"])
            return False
        res = result_box.get("res", {})
        if res.get("paused"):
            QMessageBox.warning(self, "术语归纳暂停", f"{res.get('error', '')}\n请修复后重试。")
            return False
        cands = res.get("candidates") or []
        if cands:
            dlg = TermsReviewDialog(
                self, cands,
                on_approve=service.approve,
                on_reject=service.reject,
                toast=self.ctx.bridge.toast.emit)
            dlg.exec()
            self._refresh_gloss()
            self._refresh_docs()
        return True
