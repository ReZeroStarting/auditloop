"""从账本重建消息，让中断的运行可以接着跑（`auditloop trace resume`）。

前提是账本记了足够的证据：`call_id`（哪次调用）和 `content`（工具到底返回了什么）。
这两个字段是后加的，所以旧账本会被明确拒绝，而不是「续跑出来一条错乱的历史」。

一条刻意的规则：**只从完整的回合续跑。**
如果进程是在「工具已调用、结果未落账」的瞬间被杀的，那次调用会被丢掉并记账，
因为「它到底执行了没有」已经无法确定。宁可重做一次，也不要假设它没发生。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import cost_usd_for
from ..errors import AuditloopError
from ..llm.base import Usage
from ..trace import events as E
from ..trace.events import Event
from .loop import DEFAULT_SYSTEM_PROMPT
from .messages import Message, system, tool_result, user


class ResumeError(AuditloopError):
    pass


@dataclass
class ResumePlan:
    task: str
    workspace: Path
    policy_mode: str
    system_prompt: str
    model: str
    messages: list[Message]
    usage: Usage
    steps: int
    cost_usd: float
    dangling: list[str] = field(default_factory=list)


def plan_resume(events: list[Event]) -> ResumePlan:
    created = next((e for e in events if e.type == E.RUN_CREATED), None)
    if created is None:
        raise ResumeError("账本里没有 run.created，无法还原任务")
    if any(e.type == E.RUN_FINISHED for e in events):
        raise ResumeError("这次运行已经正常结束，不需要续跑")

    payload = created.payload
    task = str(payload.get("task") or "")
    if not task:
        raise ResumeError("账本里没有任务描述")

    system_prompt = str(payload.get("system_prompt") or DEFAULT_SYSTEM_PROMPT)
    workspace = Path(str(payload.get("workspace") or "."))
    model = str(payload.get("model") or "")

    messages: list[Message] = [system(system_prompt), user(task)]
    usage = Usage()
    steps = 0
    dangling: list[str] = []
    legacy = False

    for event in events:
        if event.type == E.MODEL_RESPONSE:
            message = event.payload.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                messages.append(message)
                steps += 1
                usage.add(
                    Usage(
                        input_tokens=int(event.payload.get("input_tokens") or 0),
                        output_tokens=int(event.payload.get("output_tokens") or 0),
                        cache_read_tokens=int(event.payload.get("cache_read_tokens") or 0),
                    )
                )
        elif event.type == E.TOOL_RESULT:
            content = event.payload.get("content")
            if content is None:
                legacy = True
                continue
            call_id = str(event.payload.get("call_id") or event.span)
            messages.append(tool_result(call_id, str(content)))

    if legacy:
        raise ResumeError(
            "这份账本没有记录工具输出全文（0.1.x 版本），无法精确续跑。"
            "它仍可用于 `trace show` / `trace replay`。"
        )

    # 只保留完整回合：最后一个 assistant(tool_calls) 的每个调用都必须有结果
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            ids = [c.get("id") for c in message["tool_calls"]]
            resolved = {
                messages[j].get("tool_call_id")
                for j in range(index + 1, len(messages))
                if messages[j].get("role") == "tool"
            }
            missing = [str(x) for x in ids if x not in resolved]
            if missing:
                dangling.extend(missing)
                messages = messages[:index]
            break

    return ResumePlan(
        task=task,
        workspace=workspace,
        policy_mode=str(payload.get("policy") or "ask"),
        system_prompt=system_prompt,
        model=model,
        messages=messages,
        usage=usage,
        steps=steps,
        cost_usd=cost_usd_for(model, usage.input_tokens, usage.output_tokens) if model else 0.0,
        dangling=dangling,
    )
