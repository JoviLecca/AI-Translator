"""全局配置（设计 §10）：Provider 列表、并发、上下文段数、最近项目。

存放于 %APPDATA%/AITranslator/config.json；API 密钥绝不写入此文件。
价格单位：每百万 token（USD），可编辑，仅用于成本预估与统计。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "AITranslator"
CONFIG_PATH = CONFIG_DIR / "config.json"

# 本地运行的推理服务预设（用户反馈：希望能直接跑 Ollama 等本地模型）。
# local=True 的 Provider：免密钥、超时更长（本地 CPU/小显卡常常几十秒才出结果）、
# 模型列表在 /v1/models 不可用时回退问 Ollama 原生 /api/tags。
LOCAL_PROVIDERS = [
    {"id": "ollama", "name": "Ollama（本地）", "type": "openai", "local": True,
     "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b",
     "price_in": 0.0, "price_out": 0.0},
    {"id": "lmstudio", "name": "LM Studio（本地）", "type": "openai", "local": True,
     "base_url": "http://127.0.0.1:1234/v1", "model": "local-model",
     "price_in": 0.0, "price_out": 0.0},
    {"id": "llamacpp", "name": "llama.cpp server（本地）", "type": "openai", "local": True,
     "base_url": "http://127.0.0.1:8080/v1", "model": "local-model",
     "price_in": 0.0, "price_out": 0.0},
    {"id": "vllm", "name": "vLLM（本地）", "type": "openai", "local": True,
     "base_url": "http://127.0.0.1:8000/v1", "model": "local-model",
     "price_in": 0.0, "price_out": 0.0},
]

DEFAULT_PROVIDERS = [
    {"id": "deepseek", "name": "DeepSeek", "type": "openai",
     "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
     "price_in": 0.27, "price_out": 1.10},
    {"id": "zhipu", "name": "智谱 GLM", "type": "openai",
     "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash",
     "price_in": 0.0, "price_out": 0.0},
    {"id": "moonshot", "name": "Kimi", "type": "openai",
     "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k",
     "price_in": 1.68, "price_out": 1.68},
    {"id": "openai", "name": "OpenAI", "type": "openai",
     "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
     "price_in": 0.15, "price_out": 0.60},
] + LOCAL_PROVIDERS

DEFAULTS = {
    "providers": DEFAULT_PROVIDERS,
    "concurrency": 4,
    "context_segments": 2,
    # 界面语言（core/i18n：zh-CN / en）；设置页可切换，切换后立即重建界面
    "ui_language": "zh-CN",
    "recent_projects": [],
    # 上次使用的 Provider / model：新建项目向导据此预选，
    # 避免每建一个新项目都要重选 Provider、重填 API 密钥
    "last_provider_id": "",
    "last_model": "",
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    else:
        cfg = {}
    merged = dict(DEFAULTS)
    merged.update(cfg)
    if not cfg.get("providers"):
        merged["providers"] = [dict(p) for p in DEFAULT_PROVIDERS]
    elif not cfg.get("local_presets_seeded"):
        # 老配置里没有本地 Provider 预设：补一次（用户反馈：本地模型入口要现成可用）。
        # 只按 id 补缺失项、只补一次 —— 用户删掉后不会再被加回来。
        have = {p.get("id") for p in merged["providers"]}
        for p in LOCAL_PROVIDERS:
            if p["id"] not in have:
                merged["providers"] = list(merged["providers"]) + [dict(p)]
        merged["local_presets_seeded"] = True
    return merged


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


def get_provider(cfg: dict, provider_id: str) -> dict:
    for p in cfg.get("providers", []):
        if p["id"] == provider_id:
            return p
    raise KeyError(f"未找到 Provider：{provider_id}")


def is_local(entry: dict) -> bool:
    """本地运行的 Provider（免密钥、长超时、模型列表可回退原生接口）。"""
    return bool(entry.get("local")) or is_local_url(entry.get("base_url", ""))


def local_preset(provider_id: str) -> dict | None:
    """按 id 取本地预设（设置页一键添加用）。"""
    for p in LOCAL_PROVIDERS:
        if p["id"] == provider_id:
            return dict(p)
    return None


def is_local_url(base_url: str) -> bool:
    """base_url 是否指向本机/内网地址（本地推理服务的判定依据）。

    覆盖 localhost、*.local、host.docker.internal 与 loopback/私有网段 IP
    （192.168.x.x、10.x.x.x、172.16~31.x.x）；公网域名一律 False。
    """
    import ipaddress
    from urllib.parse import urlparse
    text = base_url if "//" in (base_url or "") else f"http://{base_url or ''}"
    host = (urlparse(text).hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def detect_provider_type(base_url: str) -> str:
    """按 base_url 自动识别协议类型（反馈 #6：表单不再暴露类型字段）。"""
    b = (base_url or "").lower()
    if "anthropic.com" in b:
        return "anthropic"
    if "googleapis.com" in b:
        return "gemini"
    return "openai"


MODEL_CACHE_TTL = 24 * 3600


def cached_models(cfg: dict, provider_id: str) -> list[str] | None:
    """24h 内的模型列表缓存（反馈 #5）。"""
    entry = (cfg.get("model_cache") or {}).get(provider_id)
    if not entry:
        return None
    import time
    if time.time() - entry.get("ts", 0) > MODEL_CACHE_TTL:
        return None
    return entry.get("models") or None


def save_model_cache(cfg: dict, provider_id: str, models: list[str]) -> None:
    """保存模型列表缓存；上限 50 条，超出时按最旧淘汰（审查第4轮修复）。"""
    import time
    cache = cfg.setdefault("model_cache", {})
    cache[provider_id] = {"models": models, "ts": time.time()}
    if len(cache) > 50:
        oldest = sorted(cache.items(), key=lambda x: x[1].get("ts", 0))
        for k, _ in oldest[:len(cache) - 50]:
            cache.pop(k, None)


def add_recent(cfg: dict, root: str) -> dict:
    recents = [r for r in cfg.get("recent_projects", []) if r != root]
    recents.insert(0, root)
    cfg["recent_projects"] = recents[:10]
    return cfg
