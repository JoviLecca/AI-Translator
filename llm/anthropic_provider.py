"""Anthropic 原生协议 Provider（设计 §11 M3）。"""
from __future__ import annotations

import httpx

from llm.errors import AuthError, BalanceError, ContentPolicyError, NetworkError, \
    RateLimitError, ServerError, classify_http
from llm.provider import ILLMProvider, LLMResult, Message


class AnthropicProvider(ILLMProvider):
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
        self._transport = transport

    async def list_models(self) -> list[str]:
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as c:
                resp = await c.get(f"{self.base_url}/v1/models", headers=headers)
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
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        body: dict = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": temperature,
            "messages": [{"role": m.role, "content": m.content}
                         for m in messages if m.role != "system"],
        }
        if system:
            body["system"] = system
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as c:
                resp = await c.post(f"{self.base_url}/v1/messages", json=body, headers=headers)
        except httpx.TimeoutException as e:
            raise NetworkError(f"请求超时：{e}") from e
        except httpx.TransportError as e:
            raise NetworkError(f"网络错误：{e}") from e
        classify_http(resp.status_code, resp.text)
        try:
            data = resp.json()
            text = "".join(b.get("text", "") for b in data.get("content", [])
                           if b.get("type") == "text")
        except Exception as e:
            raise ServerError(f"响应结构异常：{resp.text[:200]}") from e
        if data.get("stop_reason") in ("refusal", "content_policy"):
            raise ContentPolicyError(f"stop_reason={data.get('stop_reason')}")
        usage = data.get("usage") or {}
        return LLMResult(text=text,
                         tokens_in=int(usage.get("input_tokens", 0) or 0),
                         tokens_out=int(usage.get("output_tokens", 0) or 0),
                         finish=data.get("stop_reason", ""))
