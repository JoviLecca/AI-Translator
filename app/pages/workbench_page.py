"""项目工作台（设计 §9-2）：文件列表 + 标签 + 术语表 + 开始翻译（预检向导）。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout,
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
        scope = (f"本次范围：选中的 {pre.get('doc_count', 0)} 个文件"
                 if pre.get("doc_ids")
                 else f"本次范围：全部 {pre.get('doc_count', 0)} 个文件")
        lines = [
            scope,
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
            lines.append("该范围内没有待翻译段落。")
        if pre.get("glossary_reloaded"):
            lines.append("⚠ 检测到术语表被外部修改，已自动重新加载（本次翻译按新术语表执行）。")
        lay.addWidget(QLabel("\n".join(lines)))
        # 术语归纳选项：原实现只在「存在未归纳文件」时可勾选，导致中断后重跑、
        # 或想重新归纳时无法选择（用户反馈）。现在始终可勾选：
        #   有新文件 → 增量归纳；无新文件 → 强制重新归纳全部文件。
        new_docs = len(pre.get("terms_extracted_docs") or [])
        self.induce_all = new_docs == 0
        if new_docs:
            label = f"先自动归纳术语（{new_docs} 个新文件，阶段一本地免费，阶段二调用 AI）"
        else:
            label = "重新归纳全部文件的术语（阶段一本地免费，阶段二调用 AI，可能产生新候选）"
        self.induce = QCheckBox(label)
        self.induce.setChecked(True)
        lay.addWidget(self.induce)
        self.ok_btn = QPushButton("确认开始")
        self.ok_btn.clicked.connect(self.accept)
        lay.addWidget(self.ok_btn)
        self._pending = pre["pending"]
        self.induce.toggled.connect(self._sync_ok)
        self._sync_ok()

    def _sync_ok(self) -> None:
        """没有待译段落时，至少要有术语归纳任务才允许继续。"""
        self.ok_btn.setEnabled(self._pending > 0 or self.induce.isChecked())


class WorkbenchPage(CtxPage):
    def __init__(self, ctx, main):
        super().__init__(ctx, main)
        lay = QVBoxLayout(self)

        # 文件区
        file_bar = QHBoxLayout()
        import_btn = QPushButton("导入文件（复制到 source/）")
        import_btn.clicked.connect(self._import)
        folder_btn = QPushButton("导入文件夹…")
        folder_btn.clicked.connect(self._import_folder)
        tag_btn = QPushButton("编辑标签")
        tag_btn.clicked.connect(self._edit_tags)
        rm_btn = QPushButton("移除文件")
        rm_btn.clicked.connect(self._remove_doc)
        gloss_ext_btn = QPushButton("检查术语表外部修改")
        gloss_ext_btn.clicked.connect(self._reload_glossary)
        for b in (import_btn, folder_btn, tag_btn, rm_btn, gloss_ext_btn):
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

        btn_row = QHBoxLayout()
        start_btn = QPushButton("开始翻译（全部文件）")
        start_btn.clicked.connect(lambda: self._start(None))
        sel_btn = QPushButton("翻译选中文件")
        sel_btn.clicked.connect(self._start_selected)
        btn_row.addWidget(start_btn)
        btn_row.addWidget(sel_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

    def _start_selected(self):
        """只翻译文件列表中选中的那个文件（用户反馈：此前只能全局翻译）。"""
        doc_id = self._current_doc()
        if not doc_id:
            QMessageBox.information(self, "翻译选中文件",
                                    "请先在上方文件列表中点选一个文件。")
            return
        self._start([doc_id])

    # ---------- 文件 ----------
    def on_enter(self):
        super().on_enter()
        if self.ctx.project:
            self._refresh_docs()
            self._refresh_gloss()

    def on_project(self):
        self.on_enter()

    @staticmethod
    def _doc_state(segs) -> tuple[str, str]:
        """每个文件的翻译状态（用户反馈：此前无法判断哪些文件已译、哪些没译）。

        判定标准（可译段口径）：
          未翻译          —— 没有任何段落有译文
          翻译中 done/total —— 部分段落有译文
          已翻译待校对     —— 全部段落有译文，但尚有未确认段
          已校对完成       —— 全部段落 confirmed
        """
        total = len(segs)
        if total == 0:
            return "无内容", "#888888"
        done = sum(1 for s in segs
                   if s["status"] in ("machine_translated", "human_edited", "confirmed"))
        confirmed = sum(1 for s in segs if s["status"] == "confirmed")
        if done == 0:
            return f"未翻译 0/{total}", "#c53030"
        if done < total:
            return f"翻译中 {done}/{total}", "#b7791f"
        if confirmed == total:
            return f"已校对完成 {confirmed}/{total}", "#2f855a"
        return f"已翻译待校对 {done}/{total}", "#2b6cb0"

    def _refresh_docs(self):
        db = self.ctx.project.db
        docs = db.list_documents()
        self.doc_table.setRowCount(len(docs))
        for i, d in enumerate(docs):
            segs = db.list_segments(doc_id=d["id"], translatable=True)
            state, color = self._doc_state(segs)
            try:
                tags = ", ".join(f"{k}={v}" for k, v in json.loads(d["tags"]).items())
            except Exception:
                tags = ""
            vals = [d["path"], d["format"], str(len(segs)), state, tags]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setData(1, d["id"])
                if j == 3:
                    item.setForeground(QColor(color))
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

    def _import_folder(self):
        """S3：文件夹导入（递归扫描 + 自然排序 + 批量导入）。"""
        d = QFileDialog.getExistingDirectory(self, "选择包含源文件的文件夹")
        if not d:
            return
        from adapters import _FORMAT_BY_EXT
        folder = Path(d)
        files = []
        for root, dirs, names in os.walk(folder):
            dirs.sort()
            for name in sorted(names):
                ext = Path(name).suffix.lower()
                if ext in _FORMAT_BY_EXT:
                    files.append(Path(root) / name)
        # 自然排序（page_2 < page_10）
        import re as _re
        def nat_key(p: Path) -> list:
            return [int(s) if s.isdigit() else s.lower()
                    for s in _re.split(r"(\d+)", p.stem)]
        files.sort(key=nat_key)
        if not files:
            QMessageBox.information(self, "导入文件夹", "该文件夹内没有支持的文件格式")
            return
        reply = QMessageBox.question(
            self, "导入文件夹",
            f"在 {folder} 中找到 {len(files)} 个支持文件。\n确定全部导入？")
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, errs = self.ctx.importer.import_files(files)
        msg = f"导入 {len(ok)} 个文件"
        if errs:
            msg += f"（{len(errs)} 个失败）"
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
            # 重载并补齐变更历史 —— 术语变更影响分析靠相邻快照 diff 才比得出差异
            changed = self.ctx.project.reload_glossary()
            self._refresh_gloss()
            self.ctx.bridge.toast.emit(
                "已重新加载外部修改的术语表（可在校对页用「术语变更影响…」订正旧译文）"
                if changed else "已重新加载术语表（内容无变化）")
        else:
            self.ctx.bridge.toast.emit("术语表无外部修改")

    def _snapshot_terms(self):
        """保存后落 DB 快照（术语变更影响分析依赖，设计 v0.4 #25）。

        统一走 Project.snapshot_terms()：内容没变时不写重复快照，
        否则会把「上一次真实变更」挤出对比窗口（analyze 只看最近两份）。
        """
        self.ctx.project.snapshot_terms()

    # ---------- 开始翻译 ----------
    def _start(self, doc_ids: list[int] | None = None):
        """doc_ids=None → 全部文件；给出列表 → 只翻译这些文件。"""
        pre = self.ctx.translation.precheck(doc_ids)
        dlg = PrecheckDialog(self, pre)
        if not dlg.exec():
            return
        if dlg.induce.isChecked():
            # 「重新归纳全部文件」需要显式给出全部 id（run(None) 只处理未归纳文件）
            all_ids = ([d["id"] for d in self.ctx.project.db.list_documents()]
                       if dlg.induce_all else None)
            if not self._run_induction(all_ids):
                return  # 归纳失败/暂停 → 不继续烧翻译费
        if pre["pending"] == 0:
            self.ctx.bridge.toast.emit("术语归纳已完成（所选范围内没有待翻译段落）")
            self._refresh_docs()
            return
        try:
            self.ctx.run_handle = self.ctx.translation.start(
                on_progress=self.ctx.bridge.progress.emit,
                on_done=self.ctx.bridge.run_done.emit,
                doc_ids=doc_ids)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "无法启动", str(e))
            return
        self.main.goto("翻译进度")

    def _run_induction(self, doc_ids: list[int] | None = None) -> bool:
        """后台跑术语归纳并弹出审核；返回是否继续后续翻译。

        doc_ids=None → 增量（只处理未归纳的文件）；给出列表 → 归纳这些文件。
        """
        import threading

        from PySide6.QtCore import QObject, Qt as _Qt, Signal
        from PySide6.QtWidgets import QProgressDialog, QPushButton

        from app.pages.terms_dialog import TermsReviewDialog
        from core.term_induction import InductionService

        class _Sig(QObject):
            progress = Signal(int, int, str)   # done, total, 文案
            done = Signal(dict)

        try:
            provider = self.ctx.translation.build_provider()
        except Exception as e:  # noqa: BLE001
            # 未配置密钥时 secrets.get_api_key 抛 KeyError。原来这一句在 try 之外，
            # 异常会冒泡到 Qt 事件循环，用户看不到任何提示（静默失败）。
            QMessageBox.warning(
                self, "无法归纳术语",
                f"{e}\n\n请先在「设置」页配置该 Provider 的 API 密钥。")
            return False
        service = InductionService(self.ctx.project, provider)
        sig = _Sig()
        result_box: dict = {}
        cancel = threading.Event()

        def on_prog(ev: dict) -> None:
            done = int(ev.get("done") or 0)
            total = int(ev.get("total") or 0)
            if total <= 0:
                sig.progress.emit(0, 0, "阶段一：本地统计候选词（免费）…")
            elif done <= 0:
                sig.progress.emit(0, total,
                                  f"共 {total} 个候选词，开始分批送 AI 判定…")
            else:
                sig.progress.emit(done, total,
                                  f"AI 判定中：{done}/{total} 个候选词（可点「取消」中止）")

        def work():
            try:
                result_box["res"] = service.run(
                    doc_ids=doc_ids, on_progress=on_prog, cancel_check=cancel.is_set)
            except Exception as e:  # noqa: BLE001
                result_box["err"] = str(e)
            sig.done.emit(result_box)

        # 反馈修复：原来是 QProgressDialog(..., None, 0, 0, ...) —— 没有取消按钮、
        # 无限转圈、且 service.run() 根本没接 on_progress。候选多时要分多批串行调 AI，
        # 界面全程毫无反馈，用户只能关窗；而关窗后线程仍在跑、结果被丢弃，
        # 于是「没有术语表」。现在：确定进度 + 真正可用的取消 + 关窗明确告知。
        prog = QProgressDialog("阶段一：本地统计候选词（免费）…", "取消", 0, 0, self)
        prog.setWindowTitle("术语归纳")
        prog.setWindowModality(_Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)      # 立即显示，不让用户以为没反应
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        prog.canceled.connect(cancel.set)
        # 显式用自己的取消按钮：实测 QProgressDialog 在窗口尚未显示时调用 cancel()
        # 不会发出 canceled 信号，所以直接连按钮的 clicked，
        # 确保用户点了「取消」就一定会停下来（不再继续消耗 API）。
        _cancel_btn = QPushButton("取消")
        prog.setCancelButton(_cancel_btn)
        _cancel_btn.clicked.connect(cancel.set)

        def _on_prog(done: int, total: int, text: str) -> None:
            prog.setLabelText(text)
            if total > 0:
                prog.setMaximum(total)
                prog.setValue(done)

        # 注意：QProgressDialog.close() 自身会发出 canceled 信号（Qt 的
        # closeEvent 里 emit canceled()，已实测）。所以必须用 finished 标志把
        # 「正常完成后的自动关闭」和「用户真的点了取消」区分开 ——
        # 否则每次归纳成功都会被误判为已取消，永远不会弹出术语审核。
        state = {"finished": False}

        def _on_done(_res) -> None:
            state["finished"] = True
            prog.close()

        sig.progress.connect(_on_prog)
        sig.done.connect(_on_done)
        thread = threading.Thread(target=work, daemon=True, name="induction")
        thread.start()
        prog.exec()

        if "err" in result_box:
            QMessageBox.warning(self, "术语归纳失败", result_box["err"])
            return False
        res = result_box.get("res") or {}
        # 没拿到「已完成」的结果 = 用户取消或关窗（exec 会在关窗时提前返回）。
        # 实测：close() 会触发 canceled，但程序化 cancel() 的行为不完全一致，
        # 所以判定同时依赖 result_box 与 finished 标志，不单独依赖 canceled 信号。
        if res.get("cancelled") or not state["finished"] or not res:
            thread.join(timeout=5.0)   # 让工作线程在批次边界停下
            QMessageBox.information(
                self, "术语归纳已取消",
                "已取消本次归纳，未生成术语表。\n"
                "之后可再次勾选「先自动归纳术语」重新归纳。")
            self._refresh_docs()
            return False
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
        else:
            self.ctx.bridge.toast.emit("归纳完成，未发现新的术语候选")
        return True
