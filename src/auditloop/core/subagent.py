"""子 agent：把一段会产生大量噪音的探索，丢给一个上下文干净的下层 agent。

为什么需要：
主循环的上下文是稀缺资源。让主 agent 去翻 20 个文件找调用关系，它会带着
20 个文件的原文走到最后。子 agent 的价值就是**把噪音留在外面**，只把结论带回来。

三个刻意的边界：
1. **单独一本账**。子 run 写自己的 JSONL，父账本只在 tool.result 里记它的文件名。
   混在一起会让「这次运行到底发生了什么」变成一团嵌套。
2. **默认只读工具**。子 agent 大多数时候在找东西，不在改东西；给它写权限等于放大风险。
3. **不能递归**。子 agent 的 spawn 是 None，`task` 工具在子 run 里会明确报错——
   递归子 agent 是烧钱最快的方式。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..errors import AuditloopError
from ..llm.base import Usage
from ..policy.approvals import Approver
from ..policy.policy import Policy
from ..tools.base import ToolContext
from ..tools.registry import Registry
from ..trace.writer import TraceWriter
from .loop import run_loop

DEFAULT_TOOLS = ["fs_read", "fs_list", "glob", "grep"]
SUBAGENT_PROMPT = """你是一个负责「查清楚」的子 agent。

只做一件事：用工具找到答案，然后用不超过 200 字说清结论。
- 不要改文件，不要跑会改状态的命令；
- 结论里要带证据（文件路径、行号、关键代码片段）；
- 拿不到确定答案时，直接说「没找到」，不要编。"""

MAX_SUB_STEPS = 8
MAX_SUB_COST = 0.20


@dataclass
class SubResult:
    answer: str
    steps: int
    usage: Usage
    cost_usd: float
    trace_name: str
    tool_calls: int


def subset_registry(registry: Registry, names: list[str] | None) -> Registry:
    wanted = names or DEFAULT_TOOLS
    out = Registry()
    for name in wanted:
        tool = registry.get(name.strip())
        if tool is not None:
            out.add(tool)
    if not out.names():
        raise AuditloopError("子 agent 没有可用工具（白名单全部不存在）")
    return out


def run_subagent(
    prompt: str,
    tools: str | None,
    *,
    llm: Any,
    registry: Registry,
    policy: Policy,
    approver: Approver | None,
    workspace: Path,
    runs_dir: Path,
    parent_run_id: str,
    index: int,
    model_cfg: Any,
    budget: Any,
    compact_at: int,
    keep_recent: int,
    tool_chars: int,
    on_event: Callable[[Any], None] | None = None,
) -> SubResult:
    names = [n for n in (tools or "").split(",") if n.strip()] or None
    sub_registry = subset_registry(registry, names)

    run_id = f"{parent_run_id}-sub{index}"
    with TraceWriter(runs_dir, run_id=run_id) as trace:
        result = run_loop(
            prompt,
            llm=llm,
            registry=sub_registry,
            policy=policy,
            approver=approver,
            workspace=workspace,
            trace=trace,
            budget=budget,
            model_cfg=model_cfg,
            max_steps=min(MAX_SUB_STEPS, getattr(budget, "max_steps", MAX_SUB_STEPS)),
            compact_at=compact_at,
            keep_recent=keep_recent,
            tool_chars=tool_chars,
            system_prompt=SUBAGENT_PROMPT,
            on_event=on_event,
            allow_subagent=False,  # 子 agent 不能再开子 agent
        )
        trace_name = trace.path.name

    return SubResult(
        answer=result.answer,
        steps=result.steps,
        usage=result.usage,
        cost_usd=result.cost_usd,
        trace_name=trace_name,
        tool_calls=result.tool_calls,
    )


def format_result(result: SubResult, max_chars: int = 4000) -> str:
    answer = (result.answer or "").strip()
    if len(answer) > max_chars:
        answer = answer[:max_chars] + "\n…（子 agent 结论已截断）"
    return (
        f"{answer}\n\n"
        f"[子 agent：{result.steps} 步 · {result.tool_calls} 次工具 · "
        f"{result.usage.input_tokens}in/{result.usage.output_tokens}out tokens · 账本 {result.trace_name}]"
    )


def make_spawner(
    *,
    llm: Any,
    registry: Registry,
    policy: Policy,
    approver: Approver | None,
    workspace: Path,
    runs_dir: Path,
    parent_run_id: str,
    model_cfg: Any,
    budget: Any,
    compact_at: int,
    keep_recent: int,
    tool_chars: int,
    ctx: ToolContext,
) -> Callable[[str, str | None], str]:
    """给 `task` 工具用的闭包。计数在闭包里，所以一个父 run 内子 run 编号稳定。"""
    counter = {"n": 0}

    def _spawn(prompt: str, tools: str | None = None) -> str:
        counter["n"] += 1
        try:
            result = run_subagent(
                prompt,
                tools,
                llm=llm,
                registry=registry,
                policy=policy,
                approver=approver,
                workspace=workspace,
                runs_dir=runs_dir,
                parent_run_id=parent_run_id,
                index=counter["n"],
                model_cfg=model_cfg,
                budget=budget,
                compact_at=compact_at,
                keep_recent=keep_recent,
                tool_chars=tool_chars,
            )
        except AuditloopError as exc:
            return f"ERROR: 子 agent 失败：{exc}"
        # 把子 run 的用量交回主循环累加（否则成本账单会少一块）
        ctx.extra["sub_usage"] = result.usage
        ctx.extra["sub_cost_usd"] = result.cost_usd
        return format_result(result)

    return _spawn
