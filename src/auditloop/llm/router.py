"""上游降级链：主上游挂了自动切备用。

真实世界里最常见的故障不是「模型答错」，是「主上游 429 / 超时」。
没有降级链，一次限流就等于一次任务失败。
"""

from __future__ import annotations

from typing import Any, Callable

from ..core.messages import Message
from ..errors import LLMError
from .base import LLMResponse


class FallbackLLM:
    name = "fallback"

    def __init__(self, llms: list[Any], on_fallback: Callable[[str, str], None] | None = None):
        if not llms:
            raise ValueError("降级链至少需要一个上游")
        self._llms = llms
        self._on_fallback = on_fallback

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        failures: list[str] = []
        for index, llm in enumerate(self._llms):
            try:
                return llm.chat(messages, tools)
            except LLMError as exc:
                failures.append(f"{getattr(llm, 'name', index)}: {exc}")
                if self._on_fallback:
                    self._on_fallback(getattr(llm, "name", str(index)), str(exc))
        raise LLMError("所有上游都失败了：" + " | ".join(failures), retryable=False)
