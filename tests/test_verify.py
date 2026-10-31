"""账本自洽性校验的测试：坏账本必须被指出来，而不是「看起来能用」。"""

from __future__ import annotations

from auditloop.trace import events as E
from auditloop.trace.verify import summarize, verify_events


def _codes(events) -> set[str]:
    return {f.code for f in verify_events(events)}


def test_healthy_ledger_passes(runner) -> None:
    _, events, _ = runner([{"tool": "glob", "args": {}}, {"content": "完"}])
    findings = verify_events(events)
    errors, _ = summarize(findings)
    assert errors == 0, [str(f) for f in findings]


def test_seq_gap_is_an_error(runner) -> None:
    _, events, _ = runner([{"content": "完"}])
    events[2].seq = 99
    assert "seq-gap" in _codes(events)


def test_missing_tool_result_is_an_error(runner) -> None:
    _, events, _ = runner([{"tool": "glob", "args": {}}, {"content": "完"}])
    trimmed = [e for e in events if e.type != E.TOOL_RESULT]
    assert "orphan-call" in _codes(trimmed)


def test_missing_finish_is_a_warning_not_an_error(runner) -> None:
    """进程被杀会没有 run.finished——这是账本最有价值的时刻，不该判为无效。"""
    _, events, _ = runner([{"content": "完"}])
    trimmed = [e for e in events if e.type != E.RUN_FINISHED]
    findings = verify_events(trimmed)
    errors, warnings = summarize(findings)
    assert errors == 0
    assert warnings >= 1
    assert "no-finish" in {f.code for f in findings}


def test_usage_mismatch_is_caught(runner) -> None:
    _, events, _ = runner([{"content": "完"}])
    finished = next(e for e in events if e.type == E.RUN_FINISHED)
    finished.payload["input_tokens"] = 12345
    assert "usage-sum" in _codes(events)


def test_policy_decision_must_come_before_execution(runner) -> None:
    _, events, _ = runner([{"tool": "glob", "args": {}}, {"content": "完"}])
    call_idx = next(i for i, e in enumerate(events) if e.type == E.TOOL_CALL)
    decision_idx = next(i for i, e in enumerate(events) if e.type == E.POLICY_DECISION)
    events[call_idx], events[decision_idx] = events[decision_idx], events[call_idx]
    assert "policy-order" in _codes(events)


def test_empty_ledger_is_rejected() -> None:
    assert "empty" in {f.code for f in verify_events([])}
