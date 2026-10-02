"""本地 AI Provider（用户反馈：AI Provider 设置里要能跑 Ollama 等本地模型）。

三处适配各自锁死，避免以后被"顺手改回去"：
1. 免密钥：本地服务不该被"请先填写 API 密钥"挡住，也不该发空 `Bearer `；
2. 长超时：本地 CPU/小显卡推理慢，默认 600s（公网仍 120s）；
3. 模型列表：`/v1/models` 不可用时回退 Ollama 原生 `/api/tags`。
另外锁死配置迁移：老配置（没有本地预设）载入时补一次，用户删掉后不再回来。
"""
import asyncio
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx                                                   # noqa: E402
import pytest                                                  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox        # noqa: E402

from core import appconfig                                     # noqa: E402
from llm.errors import ServerError                             # noqa: E402
from llm.local_probe import probe_local_services               # noqa: E402
from llm.openai_compat import (                                # noqa: E402
    LOCAL_TIMEOUT, REMOTE_TIMEOUT, OpenAICompatProvider,
)
from llm.provider import Message                               # noqa: E402

REMOTE_URL = "https://api.example.com/v1"
LOCAL_URL = "http://127.0.0.1:11434/v1"


def _cfg(base_url: str, **extra) -> dict:
    return {"id": "x", "name": "X", "model": "m", "base_url": base_url, **extra}


def _chat_json(text: str = "ok") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}],
                                     "usage": {"prompt_tokens": 1, "completion_tokens": 2}})


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_saved_keys(monkeypatch):
    """默认视为"系统凭据管理器里没有密钥"：否则断言会随开发机真实密钥而变。"""
    from storage import secrets
    monkeypatch.setattr(secrets, "get_api_key",
                        lambda pid: (_ for _ in ()).throw(KeyError(pid)))


@pytest.fixture
def ctx(qapp, tmp_path, monkeypatch):
    """真实 AppContext，但全局配置重定向到临时目录（不碰用户 %APPDATA%）。"""
    monkeypatch.setattr(appconfig, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(appconfig, "CONFIG_PATH", tmp_path / "config.json")
    from app.context import AppContext
    return AppContext()


@pytest.fixture
def box(monkeypatch):
    """无头环境弹模态会挂死：把 QMessageBox 全部打桩成记录器。"""
    calls: dict = {"warning": [], "information": [], "question": [], "answer": None}

    def _rec(kind, answer=None):
        def fn(parent, title, text, *a, **kw):
            calls[kind].append((title, text))
            return answer
        return fn

    monkeypatch.setattr(QMessageBox, "warning", _rec("warning"))
    monkeypatch.setattr(QMessageBox, "information", _rec("information"))

    def question(parent, title, text, *a, **kw):
        calls["question"].append((title, text))
        return calls["answer"] if calls["answer"] is not None \
            else QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", question)
    return calls


# ---------------- 预设与配置迁移 ----------------
def test_local_presets_are_free_and_loopback():
    assert len(appconfig.LOCAL_PROVIDERS) >= 2
    ids = [p["id"] for p in appconfig.LOCAL_PROVIDERS]
    assert len(ids) == len(set(ids)), "预设 id 不能重复"
    assert {"ollama", "lmstudio"} <= set(ids)
    for p in appconfig.LOCAL_PROVIDERS:
        assert p["local"] is True
        assert appconfig.is_local_url(p["base_url"]), p
        assert (p["price_in"], p["price_out"]) == (0.0, 0.0), "本地模型不该有价格"
        assert p["type"] == "openai" and p["model"], "预设要能直接选中使用"


def test_default_providers_include_local_presets():
    ids = {p["id"] for p in appconfig.DEFAULT_PROVIDERS}
    assert {p["id"] for p in appconfig.LOCAL_PROVIDERS} <= ids


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:11434/v1", True),
    ("http://localhost:1234/v1", True),
    ("http://192.168.1.9:8080/v1", True),
    ("http://10.0.0.7:8000/v1", True),
    ("http://172.20.3.4:8000/v1", True),
    ("http://host.docker.internal:11434/v1", True),
    ("http://my-box.local:8080/v1", True),
    ("https://api.deepseek.com/v1", False),
    ("https://api.openai.com/v1", False),
    ("http://172.40.1.1:8000/v1", False),   # 不在私有网段
    ("", False),
])
def test_is_local_url(url, expected):
    assert appconfig.is_local_url(url) is expected


def test_load_config_seeds_local_presets_once(tmp_path, monkeypatch):
    """老配置没有本地预设：补一次；用户删掉后不再被加回来。"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"providers": [
        {"id": "deepseek", "name": "DeepSeek", "type": "openai",
         "base_url": REMOTE_URL, "model": "deepseek-chat"}]}), encoding="utf-8")
    monkeypatch.setattr(appconfig, "CONFIG_PATH", path)

    cfg = appconfig.load_config()
    ids = [p["id"] for p in cfg["providers"]]
    assert ids[0] == "deepseek", "老配置里的 Provider 不能被顶掉"
    assert {"ollama", "lmstudio"} <= set(ids)

    appconfig.save_config(cfg)
    again = [p["id"] for p in appconfig.load_config()["providers"]]
    assert again == ids, "同一次迁移不能重复追加"

    cfg["providers"] = [p for p in cfg["providers"] if p["id"] != "ollama"]
    appconfig.save_config(cfg)
    assert "ollama" not in [p["id"] for p in appconfig.load_config()["providers"]], \
        "已播种过就不再补，尊重用户删除"


# ---------------- Provider 层 ----------------
def test_local_provider_needs_no_key_and_sends_no_auth_header():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return _chat_json()

    p = OpenAICompatProvider(_cfg(LOCAL_URL), "", transport=httpx.MockTransport(handler))
    res = asyncio.run(p.chat([Message("user", "hi")]))
    assert res.text == "ok"
    assert seen["auth"] is None, "空密钥不该发 `Bearer `（本地服务会直接 401）"
    assert p.local is True


def test_remote_provider_still_sends_key():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return _chat_json()

    p = OpenAICompatProvider(_cfg(REMOTE_URL), "sk-1",
                             transport=httpx.MockTransport(handler))
    asyncio.run(p.chat([Message("user", "hi")]))
    assert seen["auth"] == "Bearer sk-1"
    assert p.local is False


def test_local_timeout_is_longer_but_explicit_wins():
    assert OpenAICompatProvider(_cfg(LOCAL_URL), "").timeout == LOCAL_TIMEOUT
    assert OpenAICompatProvider(_cfg(REMOTE_URL), "k").timeout == REMOTE_TIMEOUT
    assert OpenAICompatProvider(_cfg(LOCAL_URL), "", timeout=30).timeout == 30


def test_list_models_falls_back_to_ollama_tags():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"},
                                                        {"name": "llama3:8b"}]})
        return httpx.Response(404, text="not found")

    p = OpenAICompatProvider(_cfg(LOCAL_URL), "", transport=httpx.MockTransport(handler))
    assert asyncio.run(p.list_models()) == ["llama3:8b", "qwen2.5:7b"]
    assert paths == ["/v1/models", "/api/tags"]


def test_remote_list_models_404_does_not_fall_back():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(404, text="nope")

    p = OpenAICompatProvider(_cfg(REMOTE_URL), "k", transport=httpx.MockTransport(handler))
    with pytest.raises(ServerError):
        asyncio.run(p.list_models())
    assert paths == ["/v1/models"], "公网 Provider 不该去问 /api/tags"


def test_local_chat_retries_without_response_format():
    """本地服务常不支持 response_format：它明确抱怨该字段时去掉重试一次。"""
    seen: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append("response_format" in body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {
                "message": "response_format is not supported by this model"}})
        return _chat_json("{}")

    p = OpenAICompatProvider(_cfg(LOCAL_URL), "", transport=httpx.MockTransport(handler))
    res = asyncio.run(p.chat([Message("user", "hi")], json_mode=True))
    assert seen == [True, False]
    assert res.text == "{}"


def test_remote_chat_does_not_retry_response_format():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "response_format unsupported"}})

    p = OpenAICompatProvider(_cfg(REMOTE_URL), "k", transport=httpx.MockTransport(handler))
    with pytest.raises(ServerError):
        asyncio.run(p.chat([Message("user", "hi")], json_mode=True))
    assert len(calls) == 1


def test_local_chat_does_not_retry_unrelated_400():
    """400 与 response_format 无关时不该偷偷重试（否则会把真错误盖掉）。"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "model 'm' not found"}})

    p = OpenAICompatProvider(_cfg(LOCAL_URL), "", transport=httpx.MockTransport(handler))
    with pytest.raises(ServerError):
        asyncio.run(p.chat([Message("user", "hi")], json_mode=True))
    assert len(calls) == 1


# ---------------- 本机服务探测 ----------------
def test_probe_reports_only_running_services():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == 11434:
            return httpx.Response(200, json={"data": [{"id": "qwen2.5:7b"}]})
        raise httpx.ConnectError("connection refused", request=request)

    found = asyncio.run(probe_local_services(transport=httpx.MockTransport(handler)))
    assert [f["id"] for f in found] == ["ollama"]
    assert found[0]["models"] == ["qwen2.5:7b"]
    assert found[0]["base_url"] == "http://127.0.0.1:11434/v1"


def test_probe_falls_back_to_ollama_tags():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port != 11434:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma2:9b"}]})
        return httpx.Response(404, text="nf")

    found = asyncio.run(probe_local_services(transport=httpx.MockTransport(handler)))
    assert [f["models"] for f in found] == [["gemma2:9b"]]


def test_probe_returns_empty_when_nothing_runs():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    assert asyncio.run(probe_local_services(transport=httpx.MockTransport(handler))) == []


# ---------------- 设置页 ----------------
@pytest.fixture
def settings_page(ctx):
    from app.pages.settings_pages import SettingsPage
    page = SettingsPage(ctx, None)
    page.on_enter()
    return page


def test_settings_key_column_marks_local_as_not_needed(settings_page):
    table = settings_page.table
    rows = {table.item(i, 0).text(): table.item(i, 4).text()
            for i in range(table.rowCount())}
    assert rows["ollama"] == "不需要（本地）"
    assert rows["deepseek"] == "未配置"


def test_settings_add_local_preset_writes_config(settings_page, ctx, monkeypatch):
    saved: list[dict] = []
    monkeypatch.setattr(ctx, "save_cfg", lambda: saved.append(dict(ctx.cfg)))
    # 先模拟"用户此前删掉了这个预设"（默认配置里本来就带本地预设）
    ctx.cfg["providers"] = [p for p in ctx.cfg["providers"] if p["id"] != "lmstudio"]
    settings_page._refresh()
    settings_page.local_combo.setCurrentIndex(
        settings_page.local_combo.findData("lmstudio"))
    settings_page._add_local_preset()
    ids = [p["id"] for p in ctx.cfg["providers"]]
    assert ids.count("lmstudio") == 1 and saved, "一键添加要落库"
    # 再加一次不重复
    settings_page._add_local_preset()
    assert [p["id"] for p in ctx.cfg["providers"]].count("lmstudio") == 1


def test_settings_provider_from_form_flags_local_url(settings_page, ctx):
    settings_page.f_id.setText("mybox")
    settings_page.f_name.setText("My Box")
    settings_page.f_base.setText("http://192.168.1.50:8000/v1")
    settings_page.f_model.setCurrentText("qwen2.5:7b")
    entry, key = settings_page._provider_from_form()
    assert entry["local"] is True and key == "", "本地地址免密钥"
    settings_page.f_id.setText("deepseek")
    settings_page.f_base.setText("https://api.deepseek.com/v1")
    entry2, _ = settings_page._provider_from_form()
    assert "local" not in entry2


def test_settings_detect_local_adds_found_services(settings_page, ctx, box,
                                                   monkeypatch):
    import llm.local_probe as lp
    monkeypatch.setattr(lp, "probe_local_services", lambda *a, **kw: asyncio.sleep(0, result=[
        {"id": "ollama", "name": "Ollama（本地）", "base_url": LOCAL_URL,
         "model": "qwen2.5:7b", "models": ["qwen2.5:7b", "llama3:8b"]}]))
    ctx.cfg["providers"] = [p for p in ctx.cfg["providers"] if p["id"] != "ollama"]
    box["answer"] = QMessageBox.StandardButton.Yes
    settings_page._detect_local()
    added = [p for p in ctx.cfg["providers"] if p["id"] == "ollama"]
    assert len(added) == 1
    assert added[0]["model"] == "qwen2.5:7b", "探测到的模型应直接填好，省一步手填"
    assert ctx.cfg["model_cache"]["ollama"]["models"] == ["qwen2.5:7b", "llama3:8b"]


def test_settings_detect_local_reports_nothing_found(settings_page, box, monkeypatch):
    import llm.local_probe as lp
    monkeypatch.setattr(lp, "probe_local_services", lambda *a, **kw: asyncio.sleep(0, result=[]))
    settings_page._detect_local()
    assert box["information"], "没检测到服务要明确告诉用户怎么办"
    assert "ollama serve" in box["information"][0][1]


# ---------------- 新建向导 ----------------
@pytest.fixture
def wizard(ctx):
    from app.pages.projects_page import NewProjectWizard
    return NewProjectWizard(ctx, None)


def test_wizard_allows_local_provider_without_key(wizard, box, monkeypatch):
    import app.pages.settings_pages as sp

    class FakeProvider:
        async def list_models(self):
            return ["qwen2.5:7b"]

    monkeypatch.setattr(sp, "_build_provider", lambda entry, key: FakeProvider())
    wizard.provider_combo.setCurrentIndex(wizard.provider_combo.findData("ollama"))
    wizard.key_edit.clear()
    wizard._fetch_models_wiz()
    assert not box["warning"], "本地 Provider 不该被密钥校验挡住"
    assert wizard.model_combo.currentText() == "qwen2.5:7b"


def test_wizard_still_requires_key_for_remote_provider(wizard, box, monkeypatch):
    wizard.provider_combo.setCurrentIndex(wizard.provider_combo.findData("deepseek"))
    wizard.key_edit.clear()
    monkeypatch.setattr("storage.secrets.get_api_key",
                        lambda pid: (_ for _ in ()).throw(KeyError(pid)))
    wizard._fetch_models_wiz()
    assert box["warning"] and "请先填写 API 密钥" in box["warning"][0][1]


def test_wizard_local_connectivity_test_needs_no_key(wizard, monkeypatch):
    import app.pages.settings_pages as sp

    class FakeProvider:
        async def chat(self, messages, **kw):
            class R:
                text = "ok"
            return R()

    monkeypatch.setattr(sp, "_build_provider", lambda entry, key: FakeProvider())
    wizard.provider_combo.setCurrentIndex(wizard.provider_combo.findData("ollama"))
    wizard.key_edit.clear()
    wizard._test_conn()
    assert "连通成功" in wizard.test_out.text(), wizard.test_out.text()


def test_wizard_remote_connectivity_test_still_needs_key(wizard):
    wizard.provider_combo.setCurrentIndex(wizard.provider_combo.findData("deepseek"))
    wizard.key_edit.clear()
    wizard._test_conn()
    assert wizard.test_out.text() == "请先填写密钥"


def test_wizard_key_placeholder_mentions_local(wizard):
    wizard.provider_combo.setCurrentIndex(wizard.provider_combo.findData("ollama"))
    assert "不需要密钥" in wizard.key_edit.placeholderText()
    wizard.provider_combo.setCurrentIndex(wizard.provider_combo.findData("deepseek"))
    assert "API 密钥" in wizard.key_edit.placeholderText()
