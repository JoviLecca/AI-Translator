"""设置页（设计 §9-6 全局 / §9-7 项目设置）。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.pages.base import CtxPage
from core import i18n
from core.appconfig import LOCAL_PROVIDERS, is_local, is_local_url
from core.langs import fill_combo
from core.styles import STYLE_PRESETS
from storage import secrets

LOCAL_HINT = ("本地模型不需要 API 密钥：先在本机启动服务（如 ollama serve、LM Studio 的 "
              "Local Server），再点「检测本机服务」自动添加，或直接选预设一键添加。"
              "本地推理较慢，建议把并发请求数降到 1~2。")


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

        # 本地模型入口（用户反馈）：Ollama / LM Studio / llama.cpp / vLLM 预设 + 探测本机服务
        local_row = QHBoxLayout()
        local_row.addWidget(QLabel("本地模型"))
        self.local_combo = QComboBox()
        for p in LOCAL_PROVIDERS:
            self.local_combo.addItem(f"{p['name']} · {p['base_url']}", p["id"])
        self.local_combo.setMinimumWidth(320)
        add_local_btn = QPushButton("一键添加")
        detect_local_btn = QPushButton("检测本机服务")
        local_row.addWidget(self.local_combo, 1)
        local_row.addWidget(add_local_btn)
        local_row.addWidget(detect_local_btn)
        lay.addLayout(local_row)
        self.local_hint = QLabel(LOCAL_HINT)
        self.local_hint.setWordWrap(True)
        self.local_hint.setStyleSheet("color:#718096;")
        lay.addWidget(self.local_hint)

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

        # 界面语言（用户反馈）：切换后立即生效 —— 主窗体按新语言重建整个界面，
        # 重建前会先让各页面 on_leave() 把未落库的编辑存掉。
        ui = QFormLayout()
        self.f_lang = QComboBox()
        for code, name in i18n.UI_LANGUAGES:
            # 语言名按母语写法显示，不进词条（翻译它会让人找不到自己的语言）
            self.f_lang.addItem(name, code)
        self.f_lang.currentIndexChanged.connect(self._on_language_changed)
        ui.addRow("界面语言", self.f_lang)
        self.lang_hint = QLabel("切换后界面立即按新语言重建；校对页里未保存的编辑会先落库。")
        self.lang_hint.setWordWrap(True)
        self.lang_hint.setStyleSheet("color:#718096;")
        ui.addRow("", self.lang_hint)
        lay.addLayout(ui)

        save_btn = QPushButton("保存设置")
        save_btn.clicked.connect(self._save)
        lay.addWidget(save_btn)

        add_btn.clicked.connect(self._upsert)
        test_btn.clicked.connect(self._test)
        del_btn.clicked.connect(self._delete)
        key_status.clicked.connect(self._key_status)
        fetch_btn.clicked.connect(self._fetch_models)
        del_key_btn.clicked.connect(self._delete_key)
        add_local_btn.clicked.connect(self._add_local_preset)
        detect_local_btn.clicked.connect(self._detect_local)
        self.table.itemSelectionChanged.connect(self._load_selected)

    # ---------- 展示 ----------
    def on_enter(self):
        self.setEnabled(True)
        self._refresh()
        self.conc.setValue(int(self.ctx.cfg.get("concurrency", 4)))
        self.ctxn.setValue(int(self.ctx.cfg.get("context_segments", 2)))
        self._sync_lang_combo()

    def _sync_lang_combo(self):
        """把下拉对齐到当前语言（切页/重建后回来时用）。"""
        idx = self.f_lang.findData(i18n.normalize_language(self.ctx.cfg.get("ui_language")))
        if idx >= 0 and idx != self.f_lang.currentIndex():
            self.f_lang.blockSignals(True)  # 只是回显，别触发重建
            self.f_lang.setCurrentIndex(idx)
            self.f_lang.blockSignals(False)

    def _on_language_changed(self, _index: int = -1):
        """界面语言切换（用户反馈）：立即落库并重建界面。"""
        code = self.f_lang.currentData()
        if not code or i18n.normalize_language(self.ctx.cfg.get("ui_language")) == code:
            return
        self.ctx.cfg["ui_language"] = code
        self.ctx.save_cfg()
        rebuild = getattr(self.main, "retranslate_ui", None)
        if callable(rebuild):
            rebuild()

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
                                   self._key_cell(p)]):
                self.table.setItem(i, j, QTableWidgetItem(str(v)))
        self._sync_key_placeholder()

    def _key_cell(self, entry: dict) -> str:
        """密钥列文案：本地服务不需要密钥（用户反馈）。"""
        if is_local(entry):
            return "不需要（本地）"
        return "已配置" if self._has_key(entry["id"]) else "未配置"

    def _current_pid(self) -> str:
        return self.f_id.text().strip()

    def _existing_entry(self, pid: str) -> dict | None:
        return next((p for p in self.ctx.cfg.get("providers", []) if p.get("id") == pid), None)

    def _sync_key_placeholder(self):
        pid = self._current_pid()
        entry = self._existing_entry(pid)
        if entry is not None and is_local(entry):
            self.f_key.setPlaceholderText("本地服务不需要密钥（留空即可）")
        elif pid and self._has_key(pid):
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
        base = self.f_base.text().strip()
        old = self._existing_entry(pid)
        entry = {"id": pid, "name": self.f_name.text().strip() or pid,
                 "type": detect_provider_type(base),
                 "base_url": base,
                 "model": self.f_model.currentText().strip()}
        if is_local_url(base) or (old is not None and old.get("local")):
            entry["local"] = True
        key = self.f_key.text().strip()
        if not key:
            try:
                key = secrets.get_api_key(pid)
            except Exception:  # noqa: BLE001 —— 本地服务没有密钥也能拉模型/连通测试
                key = ""
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
        old = self._existing_entry(pid)
        if is_local_url(entry["base_url"]) or (old is not None and old.get("local")):
            entry["local"] = True
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

    # ---------- 本地模型（用户反馈） ----------
    def _select_row(self, row: int):
        if 0 <= row < self.table.rowCount():
            self.table.setCurrentCell(row, 0)

    def _add_local_preset(self):
        """把选中的本地预设加进 Provider 列表（已存在则只选中它）。"""
        from core.appconfig import local_preset
        preset = local_preset(self.local_combo.currentData())
        if not preset:
            return
        providers = self.ctx.cfg.setdefault("providers", [])
        for i, p in enumerate(providers):
            if p.get("id") == preset["id"]:
                self._refresh()
                self._select_row(i)
                self.ctx.bridge.toast.emit(f"{preset['name']} 已在列表中")
                return
        providers.append(preset)
        self.ctx.cfg["providers"] = providers
        self.ctx.save_cfg()
        self._refresh()
        self._select_row(len(providers) - 1)
        self.ctx.bridge.toast.emit(f"已添加 {preset['name']}，可先「获取模型列表」再开始翻译")

    def _detect_local(self):
        """探测本机正在运行的本地推理服务（并发探测，1 秒超时）。"""
        import asyncio

        from core.appconfig import local_preset, save_model_cache
        from llm.local_probe import probe_local_services
        try:
            found = asyncio.run(probe_local_services())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "检测本机服务", f"检测失败：{e}")
            return
        if not found:
            QMessageBox.information(
                self, "检测本机服务",
                "没有检测到本机运行的本地推理服务。\n"
                "请先启动服务（例如 ollama serve，或 LM Studio 的 Local Server）后重试；"
                "也可以直接选预设「一键添加」，手动填写地址与模型名。")
            return
        known = {p.get("id") for p in self.ctx.cfg.get("providers", [])}
        missing = [f for f in found if f["id"] not in known]
        lines = [f"· {f['name']}：{f['base_url']}"
                 f"（{'已在列表' if f['id'] in known else '可添加'}，"
                 f"{'模型 ' + str(len(f['models'])) + ' 个' if f['models'] else '未返回模型列表'}）"
                 for f in found]
        text = "检测到以下本地服务：\n" + "\n".join(lines)
        if missing and QMessageBox.question(
                self, "检测本机服务",
                f"{text}\n\n是否把其中 {len(missing)} 个加入 Provider 列表？") \
                == QMessageBox.StandardButton.Yes:
            providers = self.ctx.cfg.setdefault("providers", [])
            for f in missing:
                preset = local_preset(f["id"]) or {
                    "id": f["id"], "name": f["name"], "type": "openai", "local": True,
                    "base_url": f["base_url"], "model": f.get("model", ""),
                    "price_in": 0.0, "price_out": 0.0}
                if f["models"]:
                    preset["model"] = f["models"][0]
                    save_model_cache(self.ctx.cfg, f["id"], f["models"])
                providers.append(preset)
            self.ctx.cfg["providers"] = providers
            self.ctx.save_cfg()
            self._refresh()
            self._select_row(len(providers) - len(missing))
            self.ctx.bridge.toast.emit(f"已添加 {len(missing)} 个本地服务，可直接开始翻译")
            return
        QMessageBox.information(self, "检测本机服务", text)


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
        lay = QFormLayout(self)
        self.name = QLineEdit()
        # 语言用下拉框（显示「代码（中文名）」，itemData 存代码）：
        # 原来让用户手填 ja-JP 这类编码，不人性化（用户反馈）
        self.src = QComboBox()
        self.tgt = QComboBox()
        self.src.setMinimumWidth(220)
        self.tgt.setMinimumWidth(220)
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
        fill_combo(self.src, p.src_lang)
        fill_combo(self.tgt, p.tgt_lang)
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
            # 合成串里不放中文（`（…）` 会让英语界面要么漏翻、要么触发过宽的全角括号规则）
            self.provider.addItem(f"{pr['name']} · {pr['model']}", pr["id"])
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
        p.update_config(name=self.name.text().strip(), src_lang=self.src.currentData(),
                        tgt_lang=self.tgt.currentData(), style_preset=style_key,
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
