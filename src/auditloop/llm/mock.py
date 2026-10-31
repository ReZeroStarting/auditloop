"""MockLLM：两个用途。

1. `ScriptedLLM` —— 按预设脚本吐出工具调用，用来做**确定性的评测与演示**。
   它测的是 harness 的管道，不是模型的智力。这一点必须在文档里写清楚，
   否则「通过率 100%」就是自欺欺人。
2. `HeuristicMockLLM` —— 没配 key 时的兜底，让 `auditloop run` 也能走完一整圈。

面试点：能说清「mock 证明了什么、没证明什么」，比吹通过率值钱。
"""

from __future__ import annotations

from typing import Any

from ..core.context import estimate_tokens, messages_tokens
from ..core.messages import Message, assistant, tool_call
from .base import LLMResponse, Usage


class ScriptedLLM:
    """脚本里每一步是 {"tool": 名字, "args": {...}} 或 {"content": "最终回答"}。"""

    name = "mock"

    def __init__(self, steps: list[dict[str, Any]], model: str = "mock", final: str | None = None):
        self._steps = list(steps)
        self._final = final or "（脚本执行完毕）"
        self._model = model
        self._n = 0

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self._n += 1
        step = self._steps.pop(0) if self._steps else {"content": self._final}
        if "tool" in step:
            call = tool_call(step["tool"], step.get("args") or {}, f"call_{self._n}")
            msg = assistant(None, [call])
            out_text = call["function"]["arguments"]
        else:
            msg = assistant(step.get("content") or self._final)
            out_text = msg.get("content") or ""
        usage = Usage(
            input_tokens=messages_tokens(messages),
            output_tokens=estimate_tokens(out_text),
        )
        return LLMResponse(message=msg, usage=usage, model=self._model)


class HeuristicMockLLM:
    """没有 key 时的演示替身：只做「先看目录 → 再读 README → 收尾」的固定套路。"""

    name = "mock"

    def __init__(self, model: str = "mock"):
        self._model = model
        self._n = 0

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self._n += 1
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs:
            step = {"tool": "glob", "args": {"pattern": "**/*", "max_results": 40}}
        elif len(tool_msgs) == 1:
            step = {"tool": "fs_read", "args": {"path": "README.md"}}
        else:
            step = {
                "content": (
                    "（这是 MockLLM 的收尾，说明整条链路已经跑通：模型 → 工具 → 观察 → 收尾。）\n"
                    "要看到真实的分析与修改，请配置 AUDITLOOP_BASE_URL / AUDITLOOP_API_KEY。"
                )
            }
        return ScriptedLLM([step], model=self._model).chat(messages, tools)
