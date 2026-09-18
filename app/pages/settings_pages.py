"""设置页（设计 §9-6 全局 / §9-7 项目设置）。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.pages.base import CtxPage
from core.styles import STYLE_PRESETS
from storage import secrets


class SettingsPage(CtxPage):
    """全局设置（反馈 #5/#6/#7 重构）：Provider 增删改、模型列表拉取、密钥管理。

    - 类型按 base_url 自动识别；价格字段已删除（费用统计下线）；
    - 密钥永不明文回显：已配置显示掩码占位，留空保持不变，可一键删除重配。
    """

    needs_project = False

    def __init__(self, ctx, main):
        super().__init__(ctx, main)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Provider 管理"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["id", "名称", "base_url", "model", "密钥"])
        lay.addWidget(self.table, 2)

        form = QFormLayout()
        self.f_id = QLineEdit()
        self.f_name = QLineEdit()
        self.f_base = QLineEdit()
        self.f_model = QComboBox()
        self.f_model.setEditable(True)
        self.f_key = QLineEdit()
        self.f_key.setEchoMode(QLineEdit.EchoMode.Password)
        fetch_btn = QPushButton("获取模型列表")
        del_key_btn = QPushButton("删除密钥")
        model_row = QHBoxLayout()
        model_row.addWidget(self.f_model, 1)
        model_row.addWidget(fetch_btn)
        key_row = QHBoxLayout()
        key_row.addWidget(self.f_key, 1)
        key_row.addWidget(del_key_btn)
        form.addRow("ID", self.f_id)
        form.addRow("名称", self.f_name)
        form.addRow("base_url", self.f_base)
        form.addRow("model", model_row)
        form.addRow("API 密钥", key_row)
        lay.addLayout(form)

        btns = QHBoxLayout()
        add_btn = QPushButton("新增/更新")
        test_btn = QPushButton("连通测试")
        key_status = QPushButton("密钥状态检查")
        del_btn = QPushButton("删除")
        btns.addWidget(add_btn)
        btns.addWidget(test_btn)
        btns.addWidget(key_status)
        btns.addWidget(del_btn)
        btns.addStretch(1)
        lay.addLayout(btns)

        adv = QFormLayout()
        self.conc = QSpinBox()
        self.conc.setRange(1, 16)
        self.ctxn = QSpinBox()
        self.ctxn.setRange(0, 5)
        adv.addRow("并发请求数", self.conc)
        adv.addRow("默认上下文滑窗段数（前 N + 后 N）", self.ctxn)
        lay.addLayout(adv)
        save_btn = QPushButton("保存设置")
        save_btn.clicked.connect(self._save)
        lay.addWidget(save_btn)

        add_btn.clicked.connect(self._upsert)
        test_btn.clicked.connect(self._test)
        del_btn.clicked.connect(self._delete)
        key_status.clicked.connect(self._key_status)
        fetch_btn.clicked.connect(self._fetch_models)
        del_key_btn.clicked.connect(self._delete_key)
        self.table.itemSelectionChanged.connect(self._load_selected)

    # ---------- 展示 ----------
    def on_enter(self):
        self.setEnabled(True)
        self._refresh()
        self.conc.setValue(int(self.ctx.cfg.get("concurrency", 4)))
        self.ctxn.setValue(int(self.ctx.cfg.get("context_segments", 2)))

    def _has_key(self, pid: str) -> bool:
        try:
            return bool(secrets.get_api_key(pid))
        except Exception:
            return False

    def _refresh(self):
        providers = self.ctx.cfg.get("providers", [])
        self.table.setRowCount(len(providers))
        for i, p in enumerate(providers):
            for j, v in enumerate([p.get("id"), p.get("name"), p.get("base_url"),
                                   p.get("model"),
                                   "已配置" if self._has_key(p["id"]) else "未配置"]):
                self.table.setItem(i, j, QTableWidgetItem(str(v)))
        self._sync_key_placeholder()

    def _current_pid(self) -> str:
        return self.f_id.text().strip()

    def _sync_key_placeholder(self):
        pid = self._current_pid()
        if pid and self._has_key(pid):
            self.f_key.setPlaceholderText("●●●●●●●●（已配置，留空保持不变）")
        else:
            self.f_key.setPlaceholderText("输入 API 密钥（存入系统凭据管理器，不回显）")

    def _fill_model_combo(self, pid: str, current: str = ""):
        from core.appconfig import cached_models
        self.f_model.clear()
        models = cached_models(self.ctx.cfg, pid) or []
        if models:
            self.f_model.addItems(models)
        if current:
            self.f_model.setCurrentText(current)

    def _load_selected(self):
        item = self.table.currentItem()
        if not item:
            return
        p = self.ctx.cfg["providers"][item.row()]
        self.f_id.setText(p["id"])
        self.f_name.setText(p["name"])
        self.f_base.setText(p["base_url"])
        self._fill_model_combo(p["id"], p.get("model", ""))
        self._sync_key_placeholder()

    # ---------- 模型列表（反馈 #5） ----------
    def _provider_from_form(self):
        from core.appconfig import detect_provider_type
        pid = self._current_pid()
        if not pid:
            raise RuntimeError("请先填写 Provider ID")
        entry = {"id": pid, "name": self.f_name.text().strip() or pid,
                 "type": detect_provider_type(self.f_base.text().strip()),
                 "base_url": self.f_base.text().strip(),
                 "model": self.f_model.currentText().strip()}
        key = self.f_key.text().strip()
        if not key:
            key = secrets.get_api_key(pid)
        return entry, key

    def _fetch_models(self):
        import asyncio
        from core.appconfig import save_model_cache
        try:
            entry, key = self._provider_from_form()
            provider = _build_provider(entry, key)
            models = asyncio.run(provider.list_models())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "获取模型列表",
                                f"失败：{e}\n可在 model 框手动输入模型名。")
            return
        if not models:
            QMessageBox.information(self, "获取模型列表", "平台未返回模型列表，请手动输入。")
            return
        save_model_cache(self.ctx.cfg, entry["id"], models)
        self.ctx.save_cfg()
        self._fill_model_combo(entry["id"], current=self.f_model.currentText().strip()
                               if self.f_model.currentText() in models else models[0])
        self.ctx.bridge.toast.emit(f"获取到 {len(models)} 个模型")

    # ---------- 增删改 ----------
    def _upsert(self):
        from core.appconfig import detect_provider_type
        pid = self._current_pid()
        if not pid:
            QMessageBox.warning(self, "缺少 ID", "Provider ID 不能为空")
            return
        model = self.f_model.currentText().strip()
        if not model:
            QMessageBox.warning(self, "缺少 model", "请填写或获取 model")
            return
        entry = {"id": pid, "name": self.f_name.text().strip() or pid,
                 "type": detect_provider_type(self.f_base.text().strip()),
                 "base_url": self.f_base.text().strip(), "model": model}
        providers = self.ctx.cfg["providers"]
        for i, p in enumerate(providers):
            if p["id"] == pid:
                entry.setdefault("price_in", p.get("price_in", 0))
                entry.setdefault("price_out", p.get("price_out", 0))
                providers[i] = entry
                break
        else:
            providers.append(entry)
        self.ctx.cfg["providers"] = providers
        self.ctx.save_cfg()
        if self.f_key.text().strip():
            try:
                secrets.set_api_key(pid, self.f_key.text().strip())
                self.f_key.clear()
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "密钥保存失败", str(e))
        self._refresh()

    def _test(self):
        import asyncio
        from llm.provider import Message
        try:
            entry, key = self._provider_from_form()
            provider = _build_provider(entry, key)
            res = asyncio.run(provider.chat([Message("user", "Reply with: ok")]))
            QMessageBox.information(self, "连通测试", f"成功：{res.text[:50]}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "连通测试", f"失败：{e}")

    def _delete(self):
        item = self.table.currentItem()
        if item:
            pid = self.ctx.cfg["providers"][item.row()]["id"]
            if QMessageBox.question(
                    self, "删除 Provider",
                    f"删除 {pid}？其保存在系统凭据管理器中的 API 密钥将一并清除。") \
                    != QMessageBox.StandardButton.Yes:
                return
            self.ctx.cfg["providers"] = [p for p in self.ctx.cfg["providers"] if p["id"] != pid]
            self.ctx.save_cfg()
            secrets.delete_api_key(pid)   # 审查第3轮修复：不留残留凭据
            self._refresh()

    def _delete_key(self):
        pid = self._current_pid()
        if not pid:
            QMessageBox.information(self, "删除密钥", "请先填写或选择 Provider ID")
            return
        if not self._has_key(pid):
            QMessageBox.information(self, "删除密钥", f"{pid} 尚未配置密钥")
            return
        secrets.delete_api_key(pid)
        self.f_key.clear()
        self._refresh()
        self.ctx.bridge.toast.emit(f"已删除 {pid} 的密钥，可重新配置")

    def _key_status(self):
        try:
            secrets.test_backend()
            QMessageBox.information(self, "密钥后端", "系统凭据管理器可用。")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "密钥后端", f"不可用：{e}")

    def _save(self):
        self.ctx.cfg["concurrency"] = self.conc.value()
        self.ctx.cfg["context_segments"] = self.ctxn.value()
        self.ctx.save_cfg()
        self.ctx.bridge.toast.emit("设置已保存")


def _build_provider(entry: dict, key: str):
    """按自动识别的类型构造 Provider（设置页/向导共用）。"""
    ptype = entry.get("type", "openai")
    if ptype == "anthropic":
        from llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(entry, key)
    if ptype == "gemini":
        from llm.gemini_provider import GeminiProvider
        return GeminiProvider(entry, key)
    from llm.openai_compat import OpenAICompatProvider
    return OpenAICompatProvider(entry, key)


class ProjectSettingsPage(CtxPage):
    """项目设置（设计 v0.3 #19 + 增补设计 §1.8/§2.6）。"""

    def __init__(self, ctx, main):
        super().__init__(ctx, main)
        from PySide6.QtWidgets import QCheckBox
        lay = QFormLayout(self)
        self.name = QLineEdit()
        self.src = QLineEdit()
        self.tgt = QLineEdit()
        self.style = QComboBox()
        self.style.addItems(STYLE_PRESETS.values())
        self.custom = QLineEdit()
        self.provider = QComboBox()
        self.ruby = QComboBox()
        self.ruby.addItem("drop（丢弃注音）", "drop")
        self.ruby.addItem("keep（保留原假名）", "keep")
        self.ruby.addItem("translate（注音也翻译）", "translate")
        self.ruby_loose = QCheckBox("宽松识别：全角括号假名（汉字（かんじ））也当注音")
        self.slide = QSpinBox()
        self.slide.setRange(0, 5)
        self.slide.setSpecialValueText("继承全局设置")
        save = QPushButton("保存（振假名策略变更会使译文缓存失效）")
        retrans = QPushButton("重译过期机器译稿")
        lay.addRow("项目名", self.name)
        lay.addRow("源语言", self.src)
        lay.addRow("目标语言", self.tgt)
        lay.addRow("风格", self.style)
        lay.addRow("自定义提示词", self.custom)
        lay.addRow("Provider", self.provider)
        lay.addRow("振假名策略", self.ruby)
        lay.addRow("", self.ruby_loose)
        lay.addRow("上下文滑窗（前 N + 后 N 段）", self.slide)
        lay.addRow(save)
        lay.addRow(retrans)
        save.clicked.connect(self._save)
        retrans.clicked.connect(self._retranslate_stale)

    def on_enter(self):
        super().on_enter()
        p = self.ctx.project
        if not p:
            return
        self.name.setText(p.name)
        self.src.setText(p.src_lang)
        self.tgt.setText(p.tgt_lang)
        self.style.setCurrentIndex(list(STYLE_PRESETS).index(p.style_preset)
                                   if p.style_preset in STYLE_PRESETS else 0)
        self.custom.setText(p.custom_style_prompt)
        i = self.ruby.findData(p.ruby_policy)
        self.ruby.setCurrentIndex(i if i >= 0 else 0)
        self.ruby_loose.setChecked(p.ruby_loose)
        self.slide.setValue(int(p.context_slide if p.context_slide is not None else 0))
        self.provider.blockSignals(True)
        self.provider.clear()
        for pr in self.ctx.cfg.get("providers", []):
            self.provider.addItem(f"{pr['name']}（{pr['model']}）", pr["id"])
        if p.provider_id:
            i = self.provider.findData(p.provider_id)
            if i >= 0:
                self.provider.setCurrentIndex(i)
        self.provider.blockSignals(False)

    def _save(self):
        p = self.ctx.project
        before = p.cfg_hash()
        style_key = list(STYLE_PRESETS)[self.style.currentIndex()]
        slide = self.slide.value() if self.slide.value() > 0 else None
        p.update_config(name=self.name.text().strip(), src_lang=self.src.text().strip(),
                        tgt_lang=self.tgt.text().strip(), style_preset=style_key,
                        custom_style_prompt=self.custom.text().strip(),
                        provider_id=self.provider.currentData() or "",
                        ruby_policy=self.ruby.currentData(),
                        ruby_loose=self.ruby_loose.isChecked(),
                        context_slide=slide)
        if p.cfg_hash() != before:
            pre = self.ctx.translation.precheck()
            QMessageBox.information(
                self, "配置已变更",
                f"配置指纹已变化：{pre['stale_translations']} 段已有译文的缓存已失效。\n"
                "新翻译将采用新配置；如需重译旧的机器译稿，点击「重译过期机器译稿」。\n"
                "（上下文滑窗为质量旋钮，不触发缓存失效；调整后可手动重译刷新质量）")
        self.ctx.bridge.toast.emit("项目设置已保存")

    def _retranslate_stale(self):
        n = self.ctx.translation.mark_stale_retranslate()
        self.ctx.bridge.toast.emit(f"{n} 段过期机器译稿已标记重译")
