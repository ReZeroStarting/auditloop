"""循环的测试：五种退出、三道闸门。

这一组是整个项目里最值钱的测试——它们描述的不是「功能」，而是「失败时的行为」。
"""

from __future__ import annotations

from auditloop.config import Budget
from auditloop.llm.mock import ScriptedLLM
from auditloop.policy.approvals import RejectAll
from auditloop.policy.policy import Policy
from auditloop.trace import events as E


def test_happy_path_produces_full_trace(runner) -> None:
    result, events, _ = runner(
        [{"tool": "fs_read", "args": {"path": "a.txt"}}, {"content": "看完了"}],
        files={"a.txt": "hello\n"},
    )
    assert result.status == "ok"
    assert result.answer == "看完了"
    types = [e.type for e in events]
    assert types[0] == E.RUN_CREATED
    assert types[-1] == E.RUN_FINISHED
    assert E.TOOL_CALL in types and E.TOOL_RESULT in types
    assert result.tool_calls == 1


def test_tool_error_is_refilled_to_model(runner) -> None:
    result, events, _ = runner(
        [{"tool": "fs_read", "args": {"path": "missing.txt"}}, {"content": "文件不存在，我换个办法"}]
    )
    assert result.status == "ok"
    failed = [e for e in events if e.type == E.TOOL_RESULT and not e.payload["ok"]]
    assert failed, "工具失败必须落账，否则事后没法复盘"
    assert "ERROR" in failed[0].payload["error"] or failed[0].payload["error"]


def test_unknown_tool_becomes_observation_not_crash(runner) -> None:
    result, events, _ = runner([{"tool": "teleport", "args": {}}, {"content": "换条路"}])
    assert result.status == "ok"
    assert any(e.type == E.TOOL_RESULT and "未知工具" in str(e.payload.get("error")) for e in events)


def test_max_steps_stops_the_loop(runner) -> None:
    result, _, _ = runner(
        [{"tool": "glob", "args": {}}] * 5,
        files={"a.txt": "x"},
        max_steps=2,
    )
    assert result.status == "max_steps"
    assert result.steps == 2


def test_verification_gate_marks_unverified(runner) -> None:
    result, events, _ = runner(
        [{"content": "我做完了"}],
        verify='python -c "raise SystemExit(1)"',
    )
    assert result.status == "unverified"
    assert result.verification is not None and result.verification["rc"] == 1
    assert "未验证" in result.answer
    assert any(e.type == E.VERIFICATION for e in events)


def test_verification_gate_passes(runner) -> None:
    result, _, _ = runner(
        [{"content": "我做完了"}],
        verify='python -c "print(0)"',
    )
    assert result.status == "ok"
    assert result.verification["rc"] == 0


def test_three_denials_stop_and_escalate(runner) -> None:
    result, events, _ = runner(
        [{"tool": "shell", "args": {"command": "rm -rf ."}}] * 3,
        policy=Policy(mode="ask"),
    )
    assert result.status == "policy_blocked"
    assert result.denials == 3
    denied = [e for e in events if e.type == E.POLICY_DECISION and e.payload.get("denied")]
    assert len(denied) == 3
    assert all(e.payload["reason"] for e in denied)


def test_denied_tool_tells_model_to_change_path(runner) -> None:
    result, events, _ = runner(
        [{"tool": "shell", "args": {"command": "del /f /q a.txt"}}, {"content": "好的，我改别的做法"}],
        files={"a.txt": "x"},
        policy=Policy(mode="ask"),
    )
    assert result.status == "ok"
    assert result.denials == 1


def test_approval_rejected_counts_as_denial(runner) -> None:
    result, events, _ = runner(
        [{"tool": "fs_write", "args": {"path": "x.txt", "content": "y"}}, {"content": "算了"}],
        policy=Policy(mode="ask"),
        approver=RejectAll(),
    )
    assert result.denials == 1
    decisions = [e for e in events if e.type == E.POLICY_DECISION]
    assert decisions and decisions[0].payload["approved"] is False
    assert decisions[0].payload["denied"] is True


def test_budget_exceeded_stops_gracefully(runner) -> None:
    result, _, _ = runner(
        [{"tool": "glob", "args": {}}] * 4,
        files={"a.txt": "x"},
        llm=ScriptedLLM([{"tool": "glob", "args": {}}] * 4, model="deepseek-chat"),
        budget=Budget(max_steps=10, max_cost_usd=1e-9),
    )
    assert result.status == "budget_exceeded"
    assert "预算耗尽" in result.answer


def test_compaction_is_recorded(runner) -> None:
    steps = [{"tool": "fs_read", "args": {"path": "big.txt"}}] * 4 + [{"content": "完"}]
    result, events, _ = runner(
        steps,
        files={"big.txt": "行内容" * 2000},
        compact_at=300,
    )
    assert result.status == "ok"
    assert any(e.type == E.CONTEXT_COMPACTED for e in events)
