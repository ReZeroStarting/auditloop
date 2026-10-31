"""把账本渲染成人能读的东西。这是「可观测」的最后一公里。

一条原则：默认只显示「决策点」，不显示全量。
`auditloop trace show` 默认视图是：模型调了几次、工具做了什么、有没有被拒、
上下文什么时候压缩的、最后验证过没有。要看完整 payload 加 --full。

理由很实际：一次 20 步的 run 全量打印，人根本读不下去，
「能看」和「会看」是两件事。
"""

from __future__ import annotations

from typing import Any

from ..util import human_bytes, shorten
from . import events as E

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _args_preview(payload: dict[str, Any], limit: int = 100) -> str:
    if "command" in payload:
        return shorten(payload["command"], limit)
    if "path" in payload:
        return shorten(payload["path"], limit)
    if "pattern" in payload:
        return shorten(payload["pattern"], limit)
    return shorten(", ".join(f"{k}={v}" for k, v in payload.items()), limit)


def render_event(event: E.Event, *, full: bool = False, color: bool = True) -> str:
    def c(code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if color else text

    p = event.payload
    t = event.type

    if t == E.RUN_CREATED:
        return c(DIM, f"▶ run {event.run_id} · 策略 {p.get('policy')} · 模型 {p.get('model')} · 工作区 {p.get('workspace')}")
    if t == E.MODEL_REQUEST:
        return c(DIM, f"  → 模型调用 #{p.get('index')}（{p.get('messages')} 条消息，约 {p.get('est_tokens')} tokens）")
    if t == E.MODEL_RESPONSE:
        text = c(GREEN, "  ← 返回") + f" {p.get('finish_reason')} · in {p.get('input_tokens')}/out {p.get('output_tokens')}"
        if p.get("tool_calls"):
            text += f" · 工具 {', '.join(p['tool_calls'])}"
        if p.get("text"):
            text += "\n      " + c(DIM, shorten(p["text"], 160))
        return text
    if t == E.POLICY_DECISION:
        tool = p.get("tool")
        if p.get("denied"):
            return c(RED, f"  ⊘ 拒绝 {tool}") + f"：{p.get('reason')}"
        if p.get("needs_approval"):
            mark = "✓" if p.get("approved") else "✗"
            label = "已批准" if p.get("approved") else "未批准"
            return c(YELLOW, f"  ⚑ 审批 {tool}") + f"：{label}（{mark}）"
        return c(DIM, f"  ✓ 放行 {tool}") if full else ""
    if t == E.TOOL_CALL:
        return f"  · {p.get('name')}({_args_preview(p.get('args') or {})})"
    if t == E.TOOL_RESULT:
        if p.get("ok"):
            return c(DIM, f"    ↳ 成功 {human_bytes(int(p.get('bytes') or 0))} · {p.get('duration_ms')}ms")
        return c(RED, f"    ↳ 失败：{shorten(str(p.get('error')), 200)}")
    if t == E.CONTEXT_COMPACTED:
        saved = int(p.get("tokens_before") or 0) - int(p.get("tokens_after") or 0)
        return c(YELLOW, "  ⇣ 压缩上下文") + f"：丢 {p.get('dropped_messages')} 条，约省 {saved} tokens"
    if t == E.VERIFICATION:
        code = GREEN if p.get("rc") == 0 else RED
        return c(code, f"  ⌘ 验证 `{shorten(str(p.get('command')), 80)}` → rc={p.get('rc')}")
    if t == E.RUN_FINISHED:
        status = str(p.get("status"))
        mark = c(GREEN, "完成") if status == "ok" else c(RED, status)
        return (
            f"■ 结束：{mark} · {p.get('steps')} 步 · "
            f"{p.get('input_tokens')}in/{p.get('output_tokens')}out tokens · ${p.get('cost_usd')}"
        )
    return c(DIM, f"  ? {t} {shorten(str(p), 120)}")


def render(events: list[E.Event], *, full: bool = False, color: bool = True) -> str:
    lines = [render_event(e, full=full, color=color) for e in events]
    return "\n".join(line for line in lines if line)


def printer(*, full: bool = False, color: bool = True):
    """给 CLI 的实时渲染回调：空行（比如策略放行）直接吞掉。"""

    def _print(event: E.Event) -> None:
        line = render_event(event, full=full, color=color)
        if line:
            print(line, flush=True)

    return _print
