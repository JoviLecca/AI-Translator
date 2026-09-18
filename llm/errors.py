"""LLM 错误分类（设计 v0.6）：决定重试、暂停还是段级失败。

- RetryableError（429/5xx/网络）→ 指数退避重试；
- FatalRunError（401/402 密钥失效、余额不足）→ 立即暂停 Run，不空烧重试；
- SegmentFailError（内容策略等）→ 仅相关段落 failed，Run 继续。
"""
from __future__ import annotations


class LLMError(Exception):
    pass


class RetryableError(LLMError):
    pass


class NetworkError(RetryableError):
    pass


class RateLimitError(RetryableError):
    pass


class ServerError(RetryableError):
    pass


class FatalRunError(LLMError):
    pass


class AuthError(FatalRunError):
    pass


class BalanceError(FatalRunError):
    pass


class SegmentFailError(LLMError):
    pass


class ContentPolicyError(SegmentFailError):
    pass


class ResponseFormatError(SegmentFailError):
    pass


_POLICY_MARKS = ("content_policy", "content policy", "content_filter",
                 "content filter", "safety system", "敏感内容")


def classify_http(status: int, body: str) -> None:
    """按状态码抛出对应异常；无异常则正常返回。"""
    body_l = (body or "").lower()
    if status in (401, 403):
        raise AuthError(f"鉴权失败({status})：请检查 API 密钥")
    if status == 402:
        raise BalanceError("余额不足(402)：请充值或更换 Provider")
    if status == 429:
        raise RateLimitError(f"触发限流(429)")
    if status >= 500:
        raise ServerError(f"服务端错误({status})")
    if status == 400 and any(k in body_l for k in _POLICY_MARKS):
        raise ContentPolicyError(f"内容策略拒绝(400)")
    if status >= 400:
        raise ServerError(f"请求被拒绝({status})：{body[:200]}")
