"""agent 循环 —— 整个 harness 的中心，也是最没有魔法的地方。

一个循环，四种退出，三道闸门：

退出：
  ok              模型不再调工具（它认为自己说完了）
  max_steps       步数到顶（防止无限循环烧钱）
  budget_exceeded token / 金额到顶（必须优雅收尾，不能抛栈）
  model_error     上游彻底失败（重试与降级都已用尽）
  unverified      声称完成，但验证命令没通过 —— 这是最值得强调的一种

闸门：
  1. 上下文闸门：进模型前先压缩，超预算不发请求
  2. 策略闸门：工具执行前先问策略，被拒也要回填给模型（让它换路）
  3. 验证闸门：收尾前跑一条验证命令，rc 写进账本

面试时的核心一句：
  **模型的「我做完了」不是结论，账本里的 rc=0 才是。**
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import Budget, ModelConfig, cost_usd_for
from ..errors import LLMError, ToolArgError, ToolError
from ..llm.base import LLM, Usage
from ..policy.approvals import Approver
from ..policy.policy import Decision, Policy
from ..tools.base import ToolContext
from ..tools.registry import Registry
from ..trace import events as E
from ..trace.writer import TraceWriter
from .context import fit, messages_tokens
from .messages import Message, parse_args, system, tool_result, user

DEFAULT_SYSTEM_PROMPT = """你是一个在真实仓库里干活的编码 agent。

规则：
1. 先看清楚再改：用 glob / grep / fs_read 定位，不要凭印象改代码。
2. 改动要小：一次只解决一个问题，不要顺手重构。
3. 改完必须验证：跑测试或跑一次能复现问题的命令，拿到真实退出码。
4. 工具被拒绝是正常的：换一条不违反策略的路，不要反复重试同一个被拒的调用。
5. 最后用中文给结论：改了什么、怎么验证的、还有什么风险。不要复述工具原始输出。

工作目录就是你唯一能碰的范围，路径越界会被拒绝。"""

MAX_CONSECUTIVE_DENIALS = 3


@dataclass
class RunResult:
    run_id: str
    status: str
    answer: str
    steps: int
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    trace_path: Path | None = None
    verification: dict[str, Any] | None = None
    denials: int = 0
    tool_calls: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "steps": self.steps,
            "answer": self.answer,
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "trace": str(self.trace_path) if self.trace_path else None,
            "verification": self.verification,
            "denials": self.denials,
            "tool_calls": self.tool_calls,
        }


def run_loop(
    task: str,
    *,
    llm: LLM,
    registry: Registry,
    policy: Policy,
    workspace: Path,
    trace: TraceWriter,
    approver: Approver | None = None,
    budget: Budget | None = None,
    model_cfg: ModelConfig | None = None,
    max_steps: int | None = None,
    compact_at: int = 12_000,
    keep_recent: int = 8,
    tool_chars: int = 8_000,
    verify_cmd: str | None = None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    on_event: Callable[[E.Event], None] | None = None,
    spawn: Callable[[str, str | None], str] | None = None,
) -> RunResult:
    budget = budget or Budget()
    model_cfg = model_cfg or ModelConfig()
    steps_limit = max_steps or budget.max_steps
    workspace = Path(workspace).resolve()

    def emit(kind: str, payload: dict[str, Any], *, span: str = "root", parent: str | None = None) -> None:
        event = trace.emit(kind, payload, span=span, parent=parent)
        if on_event:
            on_event(event)

    emit(
        E.RUN_CREATED,
        {
            "task": task,
            "workspace": str(workspace),
            "policy": policy.mode,
            "model": getattr(llm, "name", "unknown"),
            "compact_at": compact_at,
            "budget": {
                "max_steps": steps_limit,
                "max_input_tokens": budget.max_input_tokens,
                "max_cost_usd": budget.max_cost_usd,
            },
        },
    )

    ctx = ToolContext(workspace=workspace, policy=policy, extra={"spawn": spawn})
    messages: list[Message] = [system(system_prompt), user(task)]
    usage_total = Usage()
    cost = 0.0
    status = "ok"
    answer = ""
    denials = 0
    tool_calls_made = 0
    verification: dict[str, Any] | None = None
    steps = 0

    for step in range(1, steps_limit + 1):
        steps = step

        if usage_total.input_tokens > budget.max_input_tokens or cost > budget.max_cost_usd:
            status = "budget_exceeded"
            answer = (
                f"预算耗尽（输入 {usage_total.input_tokens} tokens / ${cost:.4f}），"
                "在收尾时停手。已完成的部分见账本，未验证的结论不算完成。"
            )
            break

        messages, info = fit(messages, compact_at, keep_recent, tool_chars)
        if info.get("compacted"):
            emit(E.CONTEXT_COMPACTED, info)

        est = messages_tokens(messages) + 200
        emit(
            E.MODEL_REQUEST,
            {"index": step, "messages": len(messages), "est_tokens": est, "model": getattr(llm, "name", "unknown")},
            span=f"m{step}",
        )

        started = time.perf_counter()
        try:
            response = llm.chat(messages, registry.specs())
        except LLMError as exc:
            status = "model_error"
            answer = f"模型调用失败：{exc}"
            emit(E.MODEL_RESPONSE, {"error": str(exc), "text": "", "finish_reason": "error"}, span=f"m{step}")
            break
        latency = int((time.perf_counter() - started) * 1000)

        usage_total.add(response.usage)
        cost += cost_usd_for(response.model or model_cfg.model, response.usage.input_tokens, response.usage.output_tokens)
        message = response.message
        messages.append(message)
        calls = message.get("tool_calls") or []

        emit(
            E.MODEL_RESPONSE,
            {
                "message": message,
                "model": response.model,
                "finish_reason": response.finish_reason,
                "text": message.get("content") or "",
                "tool_calls": [c.get("function", {}).get("name", "?") for c in calls],
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_tokens": response.usage.cache_read_tokens,
                "latency_ms": latency,
            },
            span=f"m{step}",
            parent="root",
        )

        if not calls:
            answer = message.get("content") or ""
            break

        for index, call in enumerate(calls, 1):
            span = f"m{step}.t{index}"
            name = call.get("function", {}).get("name", "")
            tool_calls_made += 1

            try:
                args = parse_args(call)
            except ValueError as exc:
                observation = f"ERROR: {exc}"
                emit(E.TOOL_CALL, {"name": name, "args": {}, "raw": call.get("function", {}).get("arguments")}, span=span, parent=f"m{step}")
                emit(E.TOOL_RESULT, {"name": name, "ok": False, "bytes": 0, "error": observation, "duration_ms": 0}, span=span, parent=f"m{step}")
                messages.append(tool_result(call.get("id", ""), observation))
                continue

            tool = registry.get(name)
            if tool is None:
                observation = f"ERROR: 未知工具 `{name}`，可用：{', '.join(registry.names())}"
                emit(E.TOOL_CALL, {"name": name, "args": args}, span=span, parent=f"m{step}")
                emit(E.TOOL_RESULT, {"name": name, "ok": False, "bytes": 0, "error": observation, "duration_ms": 0}, span=span, parent=f"m{step}")
                messages.append(tool_result(call.get("id", ""), observation))
                continue

            decision: Decision = policy.check(name, tool.mutating, args)
            payload = {
                "tool": name,
                "args": args,
                "allowed": decision.allowed,
                "needs_approval": decision.needs_approval,
                "denied": decision.denied,
                "reason": decision.reason,
                "mode": policy.mode,
            }
            if decision.needs_approval:
                approved = bool(approver and approver.ask(name, args, decision.reason))
                payload["approved"] = approved
                payload["denied"] = not approved
                if not approved:
                    payload["reason"] = "审批未通过（无人确认 / 用户拒绝）"
            emit(E.POLICY_DECISION, payload, span=span, parent=f"m{step}")

            if payload["denied"]:
                denials += 1
                observation = (
                    f"DENIED: {payload['reason']}。这条路径不可用，请换一种不违反策略的做法，"
                    "不要重复同样的调用。"
                )
                messages.append(tool_result(call.get("id", ""), observation))
                if denials >= MAX_CONSECUTIVE_DENIALS:
                    status = "policy_blocked"
                    answer = f"连续 {denials} 次被策略拒绝，停手等人介入。最后一次原因：{payload['reason']}"
                    break
                continue

            emit(E.TOOL_CALL, {"name": name, "args": args}, span=span, parent=f"m{step}")
            tool_started = time.perf_counter()
            try:
                result = registry.dispatch(name, args, ctx)
                ok = True
                error = None
            except (ToolArgError, ToolError, PermissionError) as exc:
                result = f"ERROR: {exc}"
                ok = False
                error = str(exc)
            except Exception as exc:  # 兜底：工具内部炸了也不能让整个 run 崩
                result = f"ERROR: 工具内部异常 {type(exc).__name__}: {exc}"
                ok = False
                error = str(exc)
            duration = int((time.perf_counter() - tool_started) * 1000)

            emit(
                E.TOOL_RESULT,
                {
                    "name": name,
                    "ok": ok,
                    "bytes": len(result),
                    "preview": result[:400],
                    "error": error,
                    "duration_ms": duration,
                },
                span=span,
                parent=f"m{step}",
            )
            messages.append(tool_result(call.get("id", ""), result))

        if status != "ok":
            break
    else:
        status = "max_steps"
        answer = f"达到最大步数 {steps_limit}，仍未收敛。已做过的动作见账本。"

    if status == "ok" and verify_cmd:
        from ..tools.shell import run_shell

        verify_started = time.perf_counter()
        try:
            rc, output = run_shell(verify_cmd, str(workspace), timeout_s=120)
        except ToolError as exc:
            rc, output = -1, str(exc)
        verification = {
            "command": verify_cmd,
            "rc": rc,
            "output": output[-2000:],
            "duration_ms": int((time.perf_counter() - verify_started) * 1000),
        }
        emit(E.VERIFICATION, verification)
        if rc != 0:
            status = "unverified"
            answer = (
                f"{answer}\n\n[auditloop] 模型声称完成，但验证命令 `{verify_cmd}` 返回 rc={rc}。"
                "这次运行记为「未验证」，不算完成。"
            )

    emit(
        E.RUN_FINISHED,
        {
            "status": status,
            "steps": steps,
            "answer": answer,
            "input_tokens": usage_total.input_tokens,
            "output_tokens": usage_total.output_tokens,
            "cache_read_tokens": usage_total.cache_read_tokens,
            "cost_usd": round(cost, 6),
            "denials": denials,
            "tool_calls": tool_calls_made,
        },
    )

    return RunResult(
        run_id=trace.run_id,
        status=status,
        answer=answer,
        steps=steps,
        usage=usage_total,
        cost_usd=cost,
        trace_path=trace.path,
        verification=verification,
        denials=denials,
        tool_calls=tool_calls_made,
    )
