"""模型层接口：只有 `chat(messages, tools) -> LLMResponse` 一个方法。

为什么接口这么窄：
- 换上游只需要换一个实现（OpenAI 兼容 / Anthropic / 本地 ollama / 回放 / Mock）；
- 循环层不关心流式与否，流式是「渲染」的事，不是「决策」的事；
- 账本记录的是 LLMResponse，所以回放时不需要网络。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..core.messages import Message


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    # 上游原始 usage 原样保留，便于排查「为什么这次贵了」
    raw: dict[str, Any] = field(default_factory=dict)

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
        }


@dataclass
class LLMResponse:
    message: Message
    usage: Usage = field(default_factory=Usage)
    model: str = "unknown"
    finish_reason: str = "stop"
    latency_ms: int = 0


class LLM(Protocol):
    name: str

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse: ...
