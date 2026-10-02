"""OpenAI 兼容协议 Provider（设计 §3）：自定义 base_url 覆盖 DeepSeek/GLM/Qwen/Kimi/OpenAI。

实现决策：直接以 httpx 说 OpenAI chat/completions 协议（未用 openai SDK），
协议完全兼容，且便于测试注入 MockTransport。

本地服务（用户反馈：跑 Ollama 等本地模型）额外三条适配：
1. 无密钥时不发 Authorization 头 —— 本地服务不需要密钥，空 `Bearer ` 反而会被拒；
2. 默认超时 600s（CPU/小显卡推理常常几十秒），公网仍 120s；
3. `/v1/models` 不可用时回退 Ollama 原生 `/api/tags`；
   `response_format` 被本地服务明确拒绝时去掉它重试一次（支持程度参差）。
"""
from __future__ import annotations

import httpx

from llm.errors import AuthError, BalanceError, ContentPolicyError, NetworkError, \
    RateLimitError, RetryableError, ServerError, classify_http
from llm.provider import ILLMProvider, LLMResult, Message

LOCAL_TIMEOUT = 600.0
REMOTE_TIMEOUT = 120.0


def _mentions_response_format(text: str) -> bool:
    t = (text or "").lower()
    return "response_format" in t or "json_object" in t


class OpenAICompatProvider(ILLMProvider):
    def __init__(self, cfg: dict, api_key: str, timeout: float | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.id = cfg["id"]
        self.name = cfg.get("name", cfg["id"])
        self.model = cfg["model"]
        self.base_url = cfg["base_url"].rstrip("/")
        self.api_key = api_key
        self.price_in = float(cfg.get("price_in", 0) or 0)
        self.price_out = float(cfg.get("price_out", 0) or 0)
        # 本地判定：显式 local 标记，或 base_url 指向本机/内网
        from core.appconfig import is_local_url
        self.local = bool(cfg.get("local")) or is_local_url(self.base_url)
        self.timeout = float(timeout) if timeout is not None else (
            LOCAL_TIMEOUT if self.local else REMOTE_TIMEOUT)
        self._transport = transport  # 测试注入

    def _headers(self) -> dict:
        key = (self.api_key or "").strip()
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def list_models(self) -> list[str]:
        """GET /models（OpenAI 兼容协议，反馈 #5）；本地服务回退 Ollama /api/tags。"""
        try:
            return await self._list_models_openai()
        except RetryableError as first:
            if not self.local:
                raise
            try:
                return await self._list_models_ollama()
            except Exception:  # noqa: BLE001 —— 回退也失败时如实报首个错误
                raise first from None

    async def _list_models_openai(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                resp = await client.get(f"{self.base_url}/models", headers=self._headers())
        except httpx.TransportError as e:
            raise NetworkError(f"网络错误：{e}") from e
        classify_http(resp.status_code, resp.text)
        try:
            data = resp.json().get("data") or []
            return sorted({d["id"] for d in data if d.get("id")})
        except Exception as e:
            raise ServerError(f"模型列表响应异常：{resp.text[:200]}") from e

    async def _list_models_ollama(self) -> list[str]:
        base = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                resp = await client.get(f"{base}/api/tags", headers=self._headers())
        except httpx.TransportError as e:
            raise NetworkError(f"网络错误：{e}") from e
        classify_http(resp.status_code, resp.text)
        try:
            data = resp.json().get("models") or []
            return sorted({m["name"] for m in data if m.get("name")})
        except Exception as e:
            raise ServerError(f"模型列表响应异常：{resp.text[:200]}") from e

    async def _post_chat(self, payload: dict) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self.timeout,
                                         transport=self._transport) as client:
                return await client.post(f"{self.base_url}/chat/completions",
                                         json=payload, headers=self._headers())
        except httpx.TimeoutException as e:
            raise NetworkError(f"请求超时：{e}") from e
        except httpx.TransportError as e:
            raise NetworkError(f"网络错误：{e}") from e

    async def chat(self, messages: list[Message], *,
                   json_mode: bool = False, temperature: float = 0.3) -> LLMResult:
        payload: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        resp = await self._post_chat(payload)
        if (json_mode and self.local and resp.status_code == 400
                and _mentions_response_format(resp.text)):
            # 本地服务对 response_format 支持不一：它明确抱怨该字段时去掉重试一次
            payload.pop("response_format", None)
            resp = await self._post_chat(payload)
        classify_http(resp.status_code, resp.text)
        try:
            data = resp.json()
            choice = data["choices"][0]
        except Exception as e:
            raise ServerError(f"响应结构异常：{resp.text[:200]}") from e
        finish = choice.get("finish_reason") or ""
        if finish == "content_filter":
            raise ContentPolicyError("finish_reason=content_filter")
        usage = data.get("usage") or {}
        return LLMResult(
            text=choice.get("message", {}).get("content") or "",
            tokens_in=int(usage.get("prompt_tokens", 0) or 0),
            tokens_out=int(usage.get("completion_tokens", 0) or 0),
            finish=finish,
        )
