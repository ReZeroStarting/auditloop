"""上下文管理：估算 token、截断工具输出、超水位压缩。

这是 harness 里最容易「看起来没问题、实际上慢慢坏掉」的地方。
三个决定：
1. token 用估算而不是精确分词 —— 为了少一个依赖，估算偏差控制在 10% 以内即可，
   因为预算本来就是留了余量的；真要精确，接上游的 tokenizer 是一个可替换函数。
2. 压缩不是「删掉旧消息」，是「把旧消息压成一段摘要，并且明确标注它不是用户输入」——
   否则模型会把摘要当成用户的新要求，行为立刻跑偏。
3. 压缩后必须保证 tool_call 和 tool_result 的配对不被切断 ——
   从中间硬切会让上游直接 400，这是真实踩过的坑。
"""

from __future__ import annotations

import re

from .messages import Message, first_line, text_of

# 中日韩字符大约 1 token/字；ASCII 大约 3.2 字符/token。两个系数都偏保守。
_CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")
ASCII_CHARS_PER_TOKEN = 3.2
PER_MESSAGE_OVERHEAD = 4

SUMMARY_HEADER = "【上下文压缩摘要｜由 auditloop 生成，不是用户输入】"


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    ascii_chars = len(text) - cjk
    return int(cjk + ascii_chars / ASCII_CHARS_PER_TOKEN) + 1


def messages_tokens(messages: list[Message]) -> int:
    return sum(estimate_tokens(text_of(m)) for m in messages) + len(messages) * PER_MESSAGE_OVERHEAD


def truncate_output(text: str, limit: int) -> str:
    """头 70% + 尾 30%：开头有「发生了什么」，结尾有「最后的结果」。"""
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    return f"{text[:head]}\n…（已截断 {len(text) - limit} 字符）…\n{text[-tail:]}"


def summarize(dropped: list[Message]) -> str:
    """把被丢弃的消息压成一段确定性的摘要。不调模型，所以可复现、零成本。

    注意：如果被丢弃的里面已经有上一轮的摘要，要**合并**而不是当成用户发言，
    否则多次压缩之后，摘要里会出现「用户：用户：用户：……」这种垃圾。
    """
    lines: list[str] = []
    for msg in dropped:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "user" and content.startswith(SUMMARY_HEADER):
            lines.append(content[len(SUMMARY_HEADER) :].strip())
        elif role == "user":
            lines.append("- 用户：" + first_line(content, 140))
        elif role == "assistant":
            calls = msg.get("tool_calls") or []
            if calls:
                names = ", ".join(c.get("function", {}).get("name", "?") for c in calls)
                lines.append("- 调用工具：" + names)
            elif content:
                lines.append("- 助手：" + first_line(content, 140))
        elif role == "tool":
            lines.append("- 观察：" + first_line(content, 90))
    return "\n".join(lines[-40:]) or "（无可摘要内容）"


def _align_to_assistant(tail: list[Message], start: int) -> int:
    """把切点挪到 assistant 边界：以 tool 消息开头的尾巴会失去对应的 tool_call。"""
    while start < len(tail) and tail[start].get("role") == "tool":
        start += 1
    return start


def fit(
    messages: list[Message],
    budget_tokens: int,
    keep_recent: int = 8,
    tool_chars: int = 8_000,
) -> tuple[list[Message], dict]:
    """返回 (可能被改写过的消息列表, 统计信息)。调用方负责把统计写进账本。"""
    truncated = 0
    work: list[Message] = []
    for msg in messages:
        if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
            content = msg["content"]
            if len(content) > tool_chars:
                work.append({**msg, "content": truncate_output(content, tool_chars)})
                truncated += 1
                continue
        work.append(msg)

    before = messages_tokens(work)
    info = {
        "compacted": False,
        "truncated_outputs": truncated,
        "tokens_before": before,
        "tokens_after": before,
        "dropped_messages": 0,
    }
    if before <= budget_tokens:
        return work, info

    first_user = next((i for i, m in enumerate(work) if m.get("role") == "user"), None)
    if first_user is None:
        return work, info

    head = work[: first_user + 1]
    tail = work[first_user + 1 :]
    # 严重超预算时，保留窗口自身也要收缩：先保住「最近发生了什么」，再谈细节。
    effective_keep = keep_recent
    if before > budget_tokens * 2 and keep_recent > 2:
        effective_keep = max(2, keep_recent // 2)
    start = _align_to_assistant(tail, max(0, len(tail) - effective_keep))
    if start <= 0 or start >= len(tail):
        return work, info

    dropped = tail[:start]
    kept = tail[start:]
    summary_msg: Message = {
        "role": "user",
        "content": f"{SUMMARY_HEADER}\n{summarize(dropped)}",
    }
    result = head + [summary_msg] + kept
    info.update(
        compacted=True,
        dropped_messages=len(dropped),
        tokens_after=messages_tokens(result),
    )
    return result, info
