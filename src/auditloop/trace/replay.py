"""从账本里把「当时模型说了什么」抽出来，交给 ReplayLLM 重放。

这里有个必须讲清楚的边界：
**回放只保证「输入相同 → 决策相同」，不保证「结果相同」。**
因为工具会真的再执行一次（文件可能已经被改过、时间变了、测试仍然失败）。
所以回放的价值不是「拿到一样的答案」，而是「把当时的推理链重新走一遍，
看它到底在哪一步开始跑偏」——这才是排查 agent 事故时真正需要的东西。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import events as E
from .events import Event
from .writer import read_events


def load_run(path: str | Path) -> list[Event]:
    return read_events(path)


def recorded_llm_responses(events: list[Event]) -> list[dict[str, Any]]:
    """按账本顺序还原每一次模型响应，形状正好是 ReplayLLM 要的。"""
    out: list[dict[str, Any]] = []
    for event in events:
        if event.type != E.MODEL_RESPONSE:
            continue
        payload = event.payload
        out.append(
            {
                "message": payload.get("message") or {"role": "assistant", "content": ""},
                "usage": {
                    "input_tokens": payload.get("input_tokens", 0),
                    "output_tokens": payload.get("output_tokens", 0),
                    "cache_read_tokens": payload.get("cache_read_tokens", 0),
                },
                "model": payload.get("model"),
                "finish_reason": payload.get("finish_reason"),
            }
        )
    return out


def summarize_run(events: list[Event]) -> dict[str, Any]:
    """一条命令看清一次 run 的骨架：状态、步数、工具分布、被拒次数、成本。"""
    finished = next((e for e in events if e.type == E.RUN_FINISHED), None)
    tool_calls: dict[str, int] = {}
    denials = 0
    errors = 0
    compactions = 0
    for event in events:
        if event.type == E.TOOL_CALL:
            name = str(event.payload.get("name"))
            tool_calls[name] = tool_calls.get(name, 0) + 1
        elif event.type == E.POLICY_DECISION and event.payload.get("denied"):
            denials += 1
        elif event.type == E.TOOL_RESULT and not event.payload.get("ok"):
            errors += 1
        elif event.type == E.CONTEXT_COMPACTED:
            compactions += 1
    payload = finished.payload if finished else {}
    return {
        "status": payload.get("status", "unknown"),
        "steps": payload.get("steps", 0),
        "input_tokens": payload.get("input_tokens", 0),
        "output_tokens": payload.get("output_tokens", 0),
        "cost_usd": payload.get("cost_usd", 0.0),
        "tools": tool_calls,
        "denials": denials,
        "tool_errors": errors,
        "compactions": compactions,
        "events": len(events),
    }
