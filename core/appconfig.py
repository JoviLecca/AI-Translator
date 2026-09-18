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
]

DEFAULTS = {
    "providers": DEFAULT_PROVIDERS,
    "concurrency": 4,
    "context_segments": 2,
    "recent_projects": [],
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
    import time
    cfg.setdefault("model_cache", {})[provider_id] = {"models": models, "ts": time.time()}


def add_recent(cfg: dict, root: str) -> dict:
    recents = [r for r in cfg.get("recent_projects", []) if r != root]
    recents.insert(0, root)
    cfg["recent_projects"] = recents[:10]
    return cfg
