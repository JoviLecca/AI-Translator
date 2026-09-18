"""项目管理页（设计 §9-1）：最近项目卡片 + 新建向导（三步，含密钥连通性测试）。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWizard,
    QWizardPage,
)

from app.pages.base import CtxPage
from core.appconfig import DEFAULT_PROVIDERS
from core.project import Project, ProjectError
from core.styles import STYLE_PRESETS
from storage import secrets

LANGS = ["zh-CN", "en-US", "ja-JP", "ko-KR", "fr-FR", "de-DE", "ru-RU", "es-ES"]


class NewProjectWizard(QWizard):
    def __init__(self, ctx, main, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.main = main
        self.setWindowTitle("新建翻译项目")
        self._data = {}

        # 第 1 步：名称与目录
        p1 = QWizardPage()
        p1.setTitle("项目信息")
        name_edit = QLineEdit()
        dir_edit = QLineEdit()
        browse = QPushButton("选择目录…")
        browse.clicked.connect(lambda: self._pick_dir(dir_edit))
        lay1 = QVBoxLayout()
        lay1.addWidget(QLabel("项目名称："))
        lay1.addWidget(name_edit)
        lay1.addWidget(QLabel("项目文件夹（将自动创建 source/ target/）："))
        h1 = QHBoxLayout()
        h1.addWidget(dir_edit, 1)
        h1.addWidget(browse)
        lay1.addLayout(h1)
        p1.setLayout(lay1)
        self.p1_name, self.p1_dir = name_edit, dir_edit

        # 第 2 步：语言与风格（含振假名策略，增补设计 §1.8）
        p2 = QWizardPage()
        p2.setTitle("语言方向与风格")
        self.src_combo = QComboBox(); self.src_combo.addItems(LANGS); self.src_combo.setCurrentText("zh-CN")
        self.tgt_combo = QComboBox(); self.tgt_combo.addItems(LANGS); self.tgt_combo.setCurrentText("en-US")
        self.style_combo = QComboBox(); self.style_combo.addItems(STYLE_PRESETS.values())
        self.style_combo.setCurrentIndex(0)
        self.custom_edit = QTextEdit()
        self.custom_edit.setPlaceholderText("自定义风格提示词（选「自定义」时生效）")
        self.custom_edit.setFixedHeight(80)
        from PySide6.QtWidgets import QCheckBox
        self.ruby_combo = QComboBox()
        self.ruby_combo.addItem("自动（按目标语言：日语→保留，其他→丢弃）", None)
        self.ruby_combo.addItem("drop（丢弃注音）", "drop")
        self.ruby_combo.addItem("keep（保留原假名）", "keep")
        self.ruby_combo.addItem("translate（注音也翻译）", "translate")
        self.ruby_loose = QCheckBox("宽松识别全角括号假名注音")
        lay2 = QVBoxLayout()
        for label, w in (("源语言", self.src_combo), ("目标语言", self.tgt_combo),
                         ("翻译风格", self.style_combo)):
            lay2.addWidget(QLabel(label))
            lay2.addWidget(w)
        lay2.addWidget(QLabel("自定义提示词："))
        lay2.addWidget(self.custom_edit)
        lay2.addWidget(QLabel("振假名（注音）策略："))
        lay2.addWidget(self.ruby_combo)
        lay2.addWidget(self.ruby_loose)
        p2.setLayout(lay2)

        # 第 3 步：Provider 与密钥（反馈 #5/#7：模型可拉取选择、密钥占位提示）
        p3 = QWizardPage()
        p3.setTitle("AI 服务与密钥")
        self.provider_combo = QComboBox()
        for p in self.ctx.cfg.get("providers", DEFAULT_PROVIDERS):
            self.provider_combo.addItem(p.get("name", p["id"]), p["id"])
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        fetch_btn = QPushButton("获取模型列表")
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(fetch_btn)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        del_key_btn = QPushButton("删除密钥")
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(del_key_btn)
        self.test_btn = QPushButton("连通性测试")
        self.test_btn.clicked.connect(self._test_conn)
        self.test_out = QLabel("")
        lay3 = QVBoxLayout()
        lay3.addWidget(QLabel("Provider："))
        lay3.addWidget(self.provider_combo)
        lay3.addWidget(QLabel("model（可点击右侧按钮从平台拉取）："))
        lay3.addLayout(model_row)
        lay3.addWidget(QLabel("API 密钥（存入系统凭据管理器，不写入项目文件）："))
        lay3.addLayout(key_row)
        h3 = QHBoxLayout()
        h3.addWidget(self.test_btn)
        h3.addWidget(self.test_out, 1)
        lay3.addLayout(h3)
        p3.setLayout(lay3)
        self.provider_combo.currentIndexChanged.connect(lambda _: self._sync_provider())
        fetch_btn.clicked.connect(lambda: self._fetch_models_wiz())
        del_key_btn.clicked.connect(self._delete_key_wiz)
        self._sync_provider()

        self.addPage(p1)
        self.addPage(p2)
        self.addPage(p3)

    def _pick_dir(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择项目位置")
        if d:
            edit.setText(str(Path(d)))

    def _pid(self) -> str:
        return self.provider_combo.currentData()

    def _provider_entry(self) -> dict | None:
        for p in self.ctx.cfg.get("providers", DEFAULT_PROVIDERS):
            if p["id"] == self._pid():
                return p
        return None

    def _sync_provider(self):
        """切换 Provider：model 下拉预填（缓存/当前值），密钥框显示占位提示。"""
        from core.appconfig import cached_models
        entry = self._provider_entry()
        self.model_combo.clear()
        if entry:
            models = cached_models(self.ctx.cfg, entry["id"]) or []
            if models:
                self.model_combo.addItems(models)
            self.model_combo.setCurrentText(entry.get("model", ""))
        try:
            has_key = bool(secrets.get_api_key(self._pid()))
        except Exception:
            has_key = False
        self.key_edit.setPlaceholderText(
            "●●●●●●●●（已配置，留空保持不变）" if has_key
            else "输入 API 密钥（存入系统凭据管理器）")

    def _fetch_models_wiz(self):
        import asyncio
        from core.appconfig import detect_provider_type, save_model_cache
        from app.pages.settings_pages import _build_provider
        entry = self._provider_entry()
        if not entry:
            return
        form_entry = {"id": entry["id"], "name": entry.get("name", entry["id"]),
                      "type": detect_provider_type(entry.get("base_url", "")),
                      "base_url": entry.get("base_url", ""),
                      "model": self.model_combo.currentText().strip()}
        key = self.key_edit.text().strip()
        if not key:
            try:
                key = secrets.get_api_key(entry["id"])
            except Exception:
                key = ""
        if not key:
            QMessageBox.warning(self, "获取模型列表", "请先填写 API 密钥")
            return
        try:
            models = asyncio.run(_build_provider(form_entry, key).list_models())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "获取模型列表", f"失败：{e}\n可手动输入模型名。")
            return
        if not models:
            QMessageBox.information(self, "获取模型列表", "平台未返回模型列表，请手动输入。")
            return
        save_model_cache(self.ctx.cfg, entry["id"], models)
        self.ctx.save_cfg()
        cur = self.model_combo.currentText().strip()
        self.model_combo.clear()
        self.model_combo.addItems(models)
        self.model_combo.setCurrentText(cur if cur in models else
                                        (entry.get("model") if entry.get("model") in models
                                         else models[0]))

    def _delete_key_wiz(self):
        pid = self._pid()
        try:
            had = bool(secrets.get_api_key(pid))
        except Exception:
            had = False
        if not had:
            QMessageBox.information(self, "删除密钥", f"{pid} 尚未配置密钥")
            return
        secrets.delete_api_key(pid)
        self.key_edit.clear()
        self._sync_provider()
        QMessageBox.information(self, "删除密钥", f"已删除 {pid} 的密钥，可重新配置。")

    def _test_conn(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.test_out.setText("请先填写密钥")
            return
        try:
            from core.pipeline import TranslationService
            from core.project import Project as P
            # 临时构造 service 需要 project；直接用 provider 原型测试
            from core.appconfig import get_provider
            from llm.openai_compat import OpenAICompatProvider
            from llm.provider import Message
            import asyncio
            pcfg = get_provider(self.ctx.cfg, self._pid())
            provider = OpenAICompatProvider(pcfg, key)
            res = asyncio.run(provider.chat([Message("user", "Reply with: ok")]))
            self.test_out.setText(f"连通成功：{res.text[:30]}")
        except Exception as e:  # noqa: BLE001
            self.test_out.setText(f"失败：{e}")

    def accept(self):
        name = self.p1_name.text().strip() or "未命名项目"
        root = Path(self.p1_dir.text().strip()) / name if self.p1_dir.text().strip() else Path(".") / name
        try:
            style_key = list(STYLE_PRESETS)[self.style_combo.currentIndex()]
            ruby_policy = self.ruby_combo.currentData()
            if ruby_policy is None:
                ruby_policy = "keep" if self.tgt_combo.currentText().lower().startswith("ja") \
                    else "drop"
            project = Project.create(
                root, name=name, src_lang=self.src_combo.currentText(),
                tgt_lang=self.tgt_combo.currentText(), style_preset=style_key,
                custom_style_prompt=self.custom_edit.toPlainText().strip(),
                provider_id=self._pid(),
                model=self.model_combo.currentText().strip(),
                ruby_policy=ruby_policy, ruby_loose=self.ruby_loose.isChecked())
            # create 返回的实例持有项目文件锁，必须先释放再交给上下文打开，
            # 否则 open_project 二次加锁触发 ProjectLockError（新建失败但目录已建）
            project.close()
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, "新建失败", str(e))
            return
        key = self.key_edit.text().strip()
        if key:
            try:
                secrets.set_api_key(self._pid(), key)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "密钥保存失败", f"{e}\n密钥未保存，请到设置页补填。")
        self.ctx.open_project(str(project.root))
        self.main.refresh_after_project()
        super().accept()


class ProjectsPage(CtxPage):
    needs_project = False

    def __init__(self, ctx, main):
        super().__init__(ctx, main)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("最近项目"))
        self.listw = QListWidget()
        lay.addWidget(self.listw, 1)
        row = QHBoxLayout()
        new_btn = QPushButton("新建项目")
        open_btn = QPushButton("打开现有项目")
        del_btn = QPushButton("移出列表")
        row.addWidget(new_btn)
        row.addWidget(open_btn)
        row.addWidget(del_btn)
        row.addStretch(1)
        lay.addLayout(row)
        new_btn.clicked.connect(self._new)
        open_btn.clicked.connect(self._open)
        del_btn.clicked.connect(self._remove)
        self.listw.itemDoubleClicked.connect(lambda _: self._open())

    def on_enter(self):
        self.setEnabled(True)
        self.listw.clear()
        for p in self.ctx.cfg.get("recent_projects", []):
            self.listw.addItem(QListWidgetItem(p))

    def _new(self):
        wiz = NewProjectWizard(self.ctx, self.main, self)
        wiz.exec()
        if self.ctx.project:
            self.main.refresh_after_project()
            self.main.goto("项目工作台")

    def _open(self):
        item = self.listw.currentItem()
        if not item:
            d = QFileDialog.getExistingDirectory(self, "选择项目文件夹")
            if not d:
                return
            root = d
        else:
            root = item.text()
        try:
            self.ctx.open_project(root)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "打开失败", str(e))
            return
        self.main.refresh_after_project()
        self.main.goto("项目工作台")

    def _remove(self):
        item = self.listw.currentItem()
        if item:
            self.ctx.cfg["recent_projects"] = [p for p in self.ctx.cfg["recent_projects"]
                                               if p != item.text()]
            self.ctx.save_cfg()
            self.on_enter()
