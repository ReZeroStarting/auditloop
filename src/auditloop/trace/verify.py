"""账本自洽性校验：`auditloop trace verify`。

为什么要单独做这件事：
账本是这个项目唯一的证据。证据本身如果是坏的（丢事件、断配对、steering 不了），
那所有「可回放、可审计」的承诺都是空的。

七项检查，分两级：
- error：证据不可用（断号、配对缺失、两处 usage 不一致）
- warn ：证据不完整但可解释（没有 run.finished —— 进程被杀，这正是账本最有价值的时刻）
"""

from __future__ import annotations

from dataclasses import dataclass

from . import events as E
from .events import Event


@dataclass
class Finding:
    level: str      # error | warn
    code: str
    detail: str

    def __str__(self) -> str:
        mark = "✗" if self.level == "error" else "!"
        return f"{mark} [{self.code}] {self.detail}"


def verify_events(events: list[Event]) -> list[Finding]:
    findings: list[Finding] = []
    if not events:
        return [Finding("error", "empty", "账本是空的")]

    # 1. seq 必须从 1 开始、严格递增、不跳号
    expected = 1
    for event in events:
        if event.seq != expected:
            findings.append(
                Finding("error", "seq-gap", f"事件序号跳号：期望 {expected}，实际 {event.seq}（说明有事件丢了）")
            )
            expected = event.seq
        expected += 1

    # 2. run_id 一致
    run_ids = {e.run_id for e in events}
    if len(run_ids) > 1:
        findings.append(Finding("error", "run-id", f"同一个文件里出现了多个 run_id：{sorted(run_ids)}"))

    # 3. 生命周期
    created = [e for e in events if e.type == E.RUN_CREATED]
    finished = [e for e in events if e.type == E.RUN_FINISHED]
    if not created:
        findings.append(Finding("error", "no-start", "缺少 run.created：这份账本没有起点"))
    if not finished:
        findings.append(
            Finding("warn", "no-finish", "缺少 run.finished：运行没有正常收尾（进程被杀？这正是要看它崩之前做了什么的原因）")
        )
    if len(finished) > 1:
        findings.append(Finding("error", "double-finish", "出现多个 run.finished：一次运行只应结束一次"))

    # 4. 工具调用与结果必须成对
    calls: dict[str, Event] = {}
    results: dict[str, Event] = {}
    for event in events:
        if event.type == E.TOOL_CALL:
            key = str(event.payload.get("call_id") or event.span)
            calls[key] = event
        elif event.type == E.TOOL_RESULT:
            key = str(event.payload.get("call_id") or event.span)
            results[key] = event
    for key, event in calls.items():
        if key not in results:
            findings.append(
                Finding("error", "orphan-call", f"工具调用 {event.payload.get('name')}（{key}）没有结果事件")
            )
    for key, event in results.items():
        if key not in calls:
            findings.append(
                Finding("error", "orphan-result", f"工具结果 {event.payload.get('name')}（{key}）找不到对应的调用")
            )

    # 5. 模型请求与响应成对（最后一次请求可能因中断而没有响应）
    requests = [e for e in events if e.type == E.MODEL_REQUEST]
    responses = [e for e in events if e.type == E.MODEL_RESPONSE]
    if len(requests) - len(responses) > 1:
        findings.append(
            Finding("error", "model-pair", f"{len(requests)} 次模型请求只有 {len(responses)} 次响应")
        )
    elif len(requests) == len(responses) + 1 and finished:
        findings.append(
            Finding("warn", "model-pair", "最后一次模型请求没有响应，但运行已正常收尾，可疑")
        )

    # 6. usage 必须能对上
    if finished:
        payload = finished[-1].payload
        total_in = sum(int(e.payload.get("input_tokens") or 0) for e in responses)
        total_out = sum(int(e.payload.get("output_tokens") or 0) for e in responses)
        if int(payload.get("input_tokens") or 0) != total_in or int(payload.get("output_tokens") or 0) != total_out:
            findings.append(
                Finding(
                    "error",
                    "usage-sum",
                    f"run.finished 记的用量（{payload.get('input_tokens')}/{payload.get('output_tokens')}）"
                    f"与逐条响应之和（{total_in}/{total_out}）不一致",
                )
            )

    # 7. 策略判定必须发生在执行之前（按 seq 顺序）
    for event in events:
        if event.type != E.TOOL_CALL:
            continue
        same_span = [
            e for e in events
            if e.span == event.span and e.type in {E.POLICY_DECISION, E.TOOL_CALL, E.TOOL_RESULT}
        ]
        if same_span and same_span[0].type != E.POLICY_DECISION:
            findings.append(
                Finding("error", "policy-order", f"工具调用 {event.payload.get('name')}（{event.span}）先执行了，之后才有策略判定")
            )

    return findings


def summarize(findings: list[Finding]) -> tuple[int, int]:
    errors = sum(1 for f in findings if f.level == "error")
    return errors, len(findings) - errors
