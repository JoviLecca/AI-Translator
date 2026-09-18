"""Mock Provider（设计 §13 集成测试）：脚本化翻译/失败/限流/漏段/内容策略。"""
from __future__ import annotations

import json
import re
from typing import Callable

from llm.errors import LLMError
from llm.provider import ILLMProvider, LLMResult, Message

_SEG_RE = re.compile(r"^#(\d+)\n(.*?)(?=\n\n#\d+|\Z)", re.S | re.M)
_TERMS_JSON_RE = re.compile(r'"terms"|候选词')


def default_translator(text: str, terms: dict[str, str] | None = None) -> str:
    for src, tgt in (terms or {}).items():
        text = text.replace(src, tgt)
    return "T:" + text


class MockProvider(ILLMProvider):
    def __init__(self, translator: Callable[[str], str] | None = None,
                 retry_failures: int = 0,
                 auth_fail: bool = False,
                 policy_ids: set[int] | None = None,
                 drop_ids: set[int] | None = None,
                 usage_scale: int = 1,
                 ruby: Callable[[int], dict] | None = None):
        self.id, self.name, self.model = "mock", "Mock", "mock-1"
        self.price_in, self.price_out = 1.0, 2.0
        self.translator = translator or default_translator
        self.retry_failures = retry_failures
        self.auth_fail = auth_fail
        self.policy_ids = policy_ids or set()
        self.drop_ids = drop_ids or set()
        self.usage_scale = usage_scale
        self.ruby = ruby  # translate 策略模拟：seg_id -> {"r0": "译注音"}
        self._dropped: set[int] = set()  # drop_ids 只在首次出现时漏掉（模拟偶发漏段）
        self.calls = 0
        self.last_messages: list[Message] | None = None

    async def chat(self, messages: list[Message], *,
                   json_mode: bool = False, temperature: float = 0.3) -> LLMResult:
        self.calls += 1
        self.last_messages = list(messages)
        if self.auth_fail:
            from llm.errors import AuthError
            raise AuthError("mock auth fail")
        if self.retry_failures > 0:
            self.retry_failures -= 1
            from llm.errors import RateLimitError
            raise RateLimitError("mock rate limit")
        user = messages[-1].content if messages else ""
        system = messages[0].content if messages else ""
        if _TERMS_JSON_RE.search(user):
            # 术语归纳请求：由 translator 直接返回 JSON 文本
            text = self.translator(user)
            return LLMResult(text=text, tokens_in=len(user), tokens_out=len(text))
        segs = [(int(m.group(1)), m.group(2)) for m in _SEG_RE.finditer(user)]
        hit_policy = any(i in self.policy_ids for i, _ in segs)
        if hit_policy:
            from llm.errors import ContentPolicyError
            raise ContentPolicyError("mock content policy")
        drop_now = {i for i, _ in segs if i in self.drop_ids and i not in self._dropped}
        self._dropped |= drop_now
        items = []
        for i, t in segs:
            if i in drop_now:
                continue
            item = {"id": i, "t": self.translator(t)}
            if self.ruby:
                rb = self.ruby(i)
                if rb:
                    item["ruby"] = rb
            items.append(item)
        text = json.dumps({"translations": items}, ensure_ascii=False)
        return LLMResult(
            text=text,
            tokens_in=int(len(user) / 10) * self.usage_scale,
            tokens_out=int(len(text) / 10) * self.usage_scale,
        )
