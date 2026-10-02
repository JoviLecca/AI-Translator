"""探测本机正在运行的本地推理服务（用户反馈：本地模型入口）。

设置页「检测本机服务」用它：按 `LOCAL_PROVIDERS` 预设并发探测
`{base_url}/models`（OpenAI 兼容协议），失败时回退 Ollama 原生 `/api/tags`。
只做探测、不写配置，超时 1 秒（用户点了按钮就该秒回，不能在界面上卡 6 秒）。
"""
from __future__ import annotations

import asyncio

import httpx

from core.appconfig import LOCAL_PROVIDERS


def _ollama_root(base_url: str) -> str:
    """OpenAI 兼容地址 → Ollama 原生地址（去掉尾部 /v1）。"""
    base = (base_url or "").rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _models_from_openai(payload) -> list[str]:
    data = (payload or {}).get("data") or []
    return sorted({d["id"] for d in data if isinstance(d, dict) and d.get("id")})


def _models_from_ollama(payload) -> list[str]:
    data = (payload or {}).get("models") or []
    return sorted({m["name"] for m in data if isinstance(m, dict) and m.get("name")})


async def _probe_one(client: httpx.AsyncClient, entry: dict) -> dict | None:
    base = entry["base_url"].rstrip("/")
    models: list[str] = []
    try:
        resp = await client.get(f"{base}/models")
        if resp.status_code == 200:
            models = _models_from_openai(resp.json())
        else:
            raise ValueError(resp.status_code)
    except Exception:  # noqa: BLE001 —— 探测失败就是"不在运行"，不区分原因
        try:
            resp = await client.get(f"{_ollama_root(base)}/api/tags")
            if resp.status_code != 200:
                return None
            models = _models_from_ollama(resp.json())
        except Exception:  # noqa: BLE001
            return None
    return {"id": entry["id"], "name": entry["name"], "base_url": base,
            "model": entry.get("model", ""), "models": models}


async def probe_local_services(timeout: float = 1.0,
                               transport: httpx.AsyncBaseTransport | None = None,
                               presets: list[dict] | None = None) -> list[dict]:
    """并发探测本地预设，返回当前可用的服务（保持预设顺序）。"""
    entries = list(presets if presets is not None else LOCAL_PROVIDERS)
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        results = await asyncio.gather(*(_probe_one(client, e) for e in entries))
    return [r for r in results if r]
