"""回放：把账本里记下的模型响应当成上游，重跑一遍循环。

这是 auditloop 存在的核心理由之一：
**一个不能复现的 agent 运行，等于没有运行过。**
出问题的时候，你不可能「再跑一次看看」——模型是非确定性的，现场已经没了。
但账本里有当时的输入和输出，所以可以离线重放，把当时的判断过程重新演一遍。
"""

from __future__ import annotations

from typing import Any

from ..core.messages import Message
from ..errors import LLMError
from .base import LLMResponse, Usage


class ReplayLLM:
    name = "replay"

    def __init__(self, responses: list[dict[str, Any]]):
        # 每项形如 {"message": {...}, "usage": {...}, "model": str, "finish_reason": str}
        self._responses = list(responses)
        self._served = 0

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        if self._served >= len(self._responses):
            raise LLMError(
                f"账本里只有 {len(self._responses)} 条模型响应，"
                f"第 {self._served + 1} 次调用无据可回放——说明这次运行的输入变了"
            )
        item = self._responses[self._served]
        self._served += 1
        usage_raw = item.get("usage") or {}
        return LLMResponse(
            message=item.get("message") or {"role": "assistant", "content": ""},
            usage=Usage(
                input_tokens=int(usage_raw.get("input_tokens") or 0),
                output_tokens=int(usage_raw.get("output_tokens") or 0),
                cache_read_tokens=int(usage_raw.get("cache_read_tokens") or 0),
            ),
            model=item.get("model") or "replay",
            finish_reason=item.get("finish_reason") or "stop",
        )
