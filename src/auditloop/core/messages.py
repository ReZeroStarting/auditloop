"""消息层：只用 OpenAI 兼容的 dict 形状。

为什么不定义一套自己的 Message 类：
- 上游协议（OpenAI 兼容）本来就是 dict，多一层封装只会在「工具调用参数是 JSON 字符串」
  这种细节上反复出错；
- 账本要能直接存、直接读、直接给别人看，dict 是最稳的存档格式；
- 需要结构化的时候用 TypedDict，不需要 class。

面试点：这里唯一的「设计」是把 tool_call_id 当成一等公民——
一条工具结果必须能指回它响应的是哪一次调用，否则并发调用时会串味。
"""

from __future__ import annotations

import json
from typing import Any, TypedDict


class Message(TypedDict, total=False):
    role: str
    content: str | None
    tool_calls: list[dict[str, Any]]
    tool_call_id: str


def system(text: str) -> Message:
    return {"role": "system", "content": text}


def user(text: str) -> Message:
    return {"role": "user", "content": text}


def assistant(content: str | None = None, tool_calls: list[dict[str, Any]] | None = None) -> Message:
    msg: Message = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def tool_result(call_id: str, content: str) -> Message:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def parse_args(call: dict[str, Any]) -> dict[str, Any]:
    """模型给的参数是 JSON 字符串，且经常是空串或半截 JSON —— 这里必须容错。"""
    raw = call.get("function", {}).get("arguments") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"工具参数不是合法 JSON：{raw[:200]!r} ({exc})") from exc
    return value if isinstance(value, dict) else {"value": value}


def first_line(text: str | None, limit: int = 120) -> str:
    if not text:
        return ""
    line = str(text).strip().splitlines()[0] if str(text).strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def text_of(msg: Message) -> str:
    """把一条消息摊平成可估算 token 的文本。"""
    parts = [str(msg.get("content") or "")]
    for call in msg.get("tool_calls") or []:
        fn = call.get("function", {})
        parts.append(str(fn.get("name", "")))
        parts.append(str(fn.get("arguments", "")))
    return "\n".join(p for p in parts if p)
