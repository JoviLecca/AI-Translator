"""M3 Provider 适配器测试：OpenAI 兼容 / Anthropic / Gemini（httpx.MockTransport）。"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from llm.errors import AuthError, ContentPolicyError, RateLimitError
from llm.provider import Message


def _run(coro):
    return asyncio.run(coro)


def test_openai_compat_ok_and_errors():
    from llm.openai_compat import OpenAICompatProvider

    def handler(request: httpx.Request) -> httpx.Response:
        if "bad" in str(request.url):
            return httpx.Response(401, text="unauthorized")
        if "limit" in str(request.url):
            return httpx.Response(429, text="rate")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

    cfg = {"id": "x", "name": "X", "model": "m", "base_url": "https://api.test/v1",
           "price_in": 1.0, "price_out": 2.0}
    p = OpenAICompatProvider(cfg, "k", transport=httpx.MockTransport(handler))
    res = _run(p.chat([Message("user", "hi")], json_mode=True))
    assert res.text == "ok" and res.tokens_in == 10

    p401 = OpenAICompatProvider({**cfg, "base_url": "https://api.test/bad"}, "k",
                                transport=httpx.MockTransport(handler))
    with pytest.raises(AuthError):
        _run(p401.chat([Message("user", "hi")]))

    p429 = OpenAICompatProvider({**cfg, "base_url": "https://api.test/limit"}, "k",
                                transport=httpx.MockTransport(handler))
    with pytest.raises(RateLimitError):
        _run(p429.chat([Message("user", "hi")]))


def test_anthropic_ok_and_policy():
    from llm.anthropic_provider import AnthropicProvider

    def handler(request: httpx.Request) -> httpx.Response:
        body = httpx.Response(200, json={
            "content": [{"type": "text", "text": "hello"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 7, "output_tokens": 3},
        })
        return body

    cfg = {"id": "a", "name": "A", "model": "claude-x",
           "base_url": "https://api.anthropic.com", "price_in": 3, "price_out": 15}
    p = AnthropicProvider(cfg, "k", transport=httpx.MockTransport(handler))
    msgs = [Message("system", "sys"), Message("user", "hi")]
    res = _run(p.chat(msgs))
    assert res.text == "hello" and res.tokens_out == 3

    def policy_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "text", "text": ""}],
                                         "stop_reason": "refusal", "usage": {}})
    p2 = AnthropicProvider(cfg, "k", transport=httpx.MockTransport(policy_handler))
    with pytest.raises(ContentPolicyError):
        _run(p2.chat(msgs))


def test_gemini_ok_and_policy():
    from llm.gemini_provider import GeminiProvider

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "world"}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 6},
        })

    cfg = {"id": "g", "name": "G", "model": "gemini-x",
           "base_url": "https://generativelanguage.googleapis.com"}
    p = GeminiProvider(cfg, "k", transport=httpx.MockTransport(handler))
    res = _run(p.chat([Message("system", "s"), Message("user", "hi")], json_mode=True))
    assert res.text == "world" and res.tokens_in == 4

    def safety_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": ""}]},
                            "finishReason": "SAFETY"}],
            "usageMetadata": {}})
    p2 = GeminiProvider(cfg, "k", transport=httpx.MockTransport(safety_handler))
    with pytest.raises(ContentPolicyError):
        _run(p2.chat([Message("user", "hi")]))
