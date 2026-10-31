"""能力探测：这个上游到底能不能当 agent 用。

大量「agent 不好用」的投诉，最后都落在三件小事上：
端点通不通、支不支持 tools、usage 里有没有缓存字段。
把这三件事在启动阶段问清楚，比事后看一堆参数解析错误强。

探测本身也是一次真实的模型调用，所以它同时验证了：认证、模型名、网络、解析链路。
"""

from __future__ import annotations

import time
from typing import Any

from .core.messages import Message, system, user
from .errors import LLMError
from .llm.base import LLM

PROBE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "noop",
        "description": "什么都不做，只用来验证工具调用是否可用。",
        "parameters": {
            "type": "object",
            "properties": {"x": {"type": "string", "description": "随便一个字符串"}},
            "required": ["x"],
        },
    },
}

PROBE_MESSAGES: list[Message] = [
    system("你是一个连通性探针。只做被要求的事，不要解释。"),
    user('调用 noop 工具，参数 x="ping"。不要用文字回答。'),
]


def probe_provider(llm: LLM) -> dict[str, Any]:
    """返回一份探测报告。不抛异常——探测失败也是一种结论。"""
    started = time.perf_counter()
    try:
        response = llm.chat(PROBE_MESSAGES, [PROBE_TOOL])
    except LLMError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    elapsed = int((time.perf_counter() - started) * 1000)
    message = response.message or {}
    calls = message.get("tool_calls") or []
    names = [c.get("function", {}).get("name") for c in calls]
    usage = response.usage

    return {
        "ok": True,
        "model": response.model,
        "latency_ms": elapsed,
        "tool_calls": bool(calls),
        "tool_names": names,
        "arguments": [c.get("function", {}).get("arguments") for c in calls][:1],
        "finish_reason": response.finish_reason,
        "usage_reported": usage.input_tokens > 0,
        "cache_field": bool(usage.cache_read_tokens) or bool((usage.raw or {}).get("prompt_tokens_details")),
        "text": (message.get("content") or "")[:200],
    }


def verdict(report: dict[str, Any]) -> tuple[bool, str]:
    """把报告翻译成一句人话 + 一个退出码依据。"""
    if not report.get("ok"):
        return False, f"连不上：{report.get('error')}"
    if report.get("tool_calls"):
        cache = "，带回缓存字段" if report.get("cache_field") else "，未带缓存字段"
        useg = "，有 usage" if report.get("usage_reported") else "，无 usage（成本会记 0）"
        return True, f"可用：{report.get('latency_ms')}ms，工具调用正常（{', '.join(report.get('tool_names') or [])}）{useg}{cache}"
    return False, (
        "端点可用但**不支持工具调用**（返回了纯文本）。这个模型可以聊天，但不能当 agent。"
        f"它说：{report.get('text')!r}"
    )
