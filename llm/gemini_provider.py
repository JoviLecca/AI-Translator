"""Google Gemini 原生协议 Provider（设计 §11 M3）。"""
from __future__ import annotations

import httpx

from llm.errors import AuthError, BalanceError, ContentPolicyError, NetworkError, \
    RateLimitError, ServerError, classify_http
from llm.provider import ILLMProvider, LLMResult, Message


class GeminiProvider(ILLMProvider):
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
        url = f"{self.base_url}/v1beta/models?key={self.api_key}&pageSize=200"
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as c:
                resp = await c.get(url)
        except httpx.TransportError as e:
            raise NetworkError(f"网络错误：{e}") from e
        classify_http(resp.status_code, resp.text)
        try:
            data = resp.json().get("models") or []
            return sorted({m["name"].removeprefix("models/")
                           for m in data if m.get("name")})
        except Exception as e:
            raise ServerError(f"模型列表响应异常：{resp.text[:200]}") from e

    async def chat(self, messages: list[Message], *,
                   json_mode: bool = False, temperature: float = 0.3) -> LLMResult:
        contents = []
        system_parts = [m.content for m in messages if m.role == "system"]
        for m in messages:
            if m.role == "system":
                continue
            contents.append({"role": "model" if m.role == "assistant" else "user",
                             "parts": [{"text": m.content}]})
        body: dict = {
            "contents": contents,
            "generationConfig": {"temperature": temperature},
        }
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        url = (f"{self.base_url}/v1beta/models/{self.model}:generateContent"
               f"?key={self.api_key}")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as c:
                resp = await c.post(url, json=body)
        except httpx.TimeoutException as e:
            raise NetworkError(f"请求超时：{e}") from e
        except httpx.TransportError as e:
            raise NetworkError(f"网络错误：{e}") from e
        classify_http(resp.status_code, resp.text)
        try:
            data = resp.json()
            cand = data["candidates"][0]
            text = "".join(p.get("text", "") for p in
                           cand.get("content", {}).get("parts", []))
        except Exception as e:
            raise ServerError(f"响应结构异常：{resp.text[:200]}") from e
        if cand.get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            raise ContentPolicyError(f"finishReason={cand.get('finishReason')}")
        usage = data.get("usageMetadata") or {}
        return LLMResult(text=text,
                         tokens_in=int(usage.get("promptTokenCount", 0) or 0),
                         tokens_out=int(usage.get("candidatesTokenCount", 0) or 0),
                         finish=cand.get("finishReason", ""))
