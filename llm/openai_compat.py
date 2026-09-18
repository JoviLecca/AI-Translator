"""OpenAI 兼容协议 Provider（设计 §3）：自定义 base_url 覆盖 DeepSeek/GLM/Qwen/Kimi/OpenAI。

实现决策：直接以 httpx 说 OpenAI chat/completions 协议（未用 openai SDK），
协议完全兼容，且便于测试注入 MockTransport。
"""
from __future__ import annotations

import httpx

from llm.errors import AuthError, BalanceError, ContentPolicyError, NetworkError, \
    RateLimitError, ServerError, classify_http
from llm.provider import ILLMProvider, LLMResult, Message


class OpenAICompatProvider(ILLMProvider):
    def __init__(self, cfg: dict, api_key: str, timeout: float = 120.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.id = cfg["id"]
        self.name = cfg.get("name", cfg["id"])
        self.model = cfg["model"]
        self.base_url = cfg["base_url"].rstrip("/")
        self.api_key = api_key
        self.price_in = float(cfg.get("price_in", 0) or 0)
        self.price_out = float(cfg.get("price_out", 0) or 0)
        self.timeout = timeout
        self._transport = transport  # 测试注入

    async def list_models(self) -> list[str]:
        """GET /models（OpenAI 兼容协议，反馈 #5）。"""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                resp = await client.get(f"{self.base_url}/models", headers=headers)
        except httpx.TransportError as e:
            raise NetworkError(f"网络错误：{e}") from e
        classify_http(resp.status_code, resp.text)
        try:
            data = resp.json().get("data") or []
            return sorted({d["id"] for d in data if d.get("id")})
        except Exception as e:
            raise ServerError(f"模型列表响应异常：{resp.text[:200]}") from e

    async def chat(self, messages: list[Message], *,
                   json_mode: bool = False, temperature: float = 0.3) -> LLMResult:
        payload: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
                resp = await client.post(f"{self.base_url}/chat/completions",
                                         json=payload, headers=headers)
        except httpx.TimeoutException as e:
            raise NetworkError(f"请求超时：{e}") from e
        except httpx.TransportError as e:
            raise NetworkError(f"网络错误：{e}") from e
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
