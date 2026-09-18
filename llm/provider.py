"""Provider 抽象（设计 §7.3）：统一 chat 接口 + usage 计量。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from llm.errors import LLMError


@dataclass
class Message:
    role: str            # system | user | assistant
    content: str


@dataclass
class LLMResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    finish: str = ""


class ILLMProvider(ABC):
    id: str = ""
    name: str = ""
    model: str = ""
    price_in: float = 0.0   # 每百万 token
    price_out: float = 0.0

    @abstractmethod
    async def chat(self, messages: list[Message], *,
                   json_mode: bool = False, temperature: float = 0.3) -> LLMResult:
        """单次调用；错误经 llm.errors 分类抛出，重试与并发由引擎负责。"""

    async def list_models(self) -> list[str]:
        """拉取平台在售模型列表（反馈 #5）；不支持时抛 LLMError，UI 回退手动输入。"""
        raise LLMError(f"{self.name} 不支持模型列表拉取，请手动输入")
