"""会话续跑：从账本重建上下文，接着跑。

测试里的「进程被杀」用截断事件列表模拟——这和真实场景一致：
账本最后一条之后发生的事，我们一无所知。
"""

from __future__ import annotations

import json

import pytest

from auditloop.core.loop import run_loop
from auditloop.core.resume import ResumeError, plan_resume
from auditloop.llm.mock import ScriptedLLM
from auditloop.policy.approvals import AutoApprove
from auditloop.policy.policy import Policy
from auditloop.tools.builtin import default_registry
from auditloop.trace import events as E
from auditloop.trace.writer import TraceWriter, read_events

STEPS = [
    {"tool": "fs_read", "args": {"path": "a.txt"}},
    {"tool": "fs_write", "args": {"path": "b.txt", "content": "x"}},
    {"content": "做完了"},
]


def test_plan_rebuilds_history_from_ledger(runner) -> None:
    _, events, _ = runner(STEPS, files={"a.txt": "hello\n"})
    partial = events[:6]  # 到第一次工具结果为止
    plan = plan_resume(partial)
    assert plan.steps == 1
    assert plan.messages[0]["role"] == "system"
    assert plan.messages[1]["role"] == "user"
    assert any(m.get("role") == "tool" for m in plan.messages), "工具输出全文必须能还原"
    assert plan.dangling == []
    assert plan.usage.input_tokens > 0


def test_resumed_run_continues_and_links_back(runner, tmp_path) -> None:
    _, events, ws = runner(STEPS, files={"a.txt": "hello\n"})
    plan = plan_resume(events[:6])
    with TraceWriter(tmp_path / "r2", run_id="resume-1") as trace:
        resumed = run_loop(
            plan.task,
            llm=ScriptedLLM([{"content": "接着做完了"}]),
            registry=default_registry(),
            policy=Policy(mode="auto"),
            approver=AutoApprove(),
            workspace=ws,
            trace=trace,
            seed_messages=plan.messages,
            seed_usage=plan.usage,
            seed_steps=plan.steps,
            resumed_from="partial.jsonl",
        )
    assert resumed.status == "ok"
    assert resumed.steps == plan.steps + 1
    created = read_events(trace.path)[0]
    assert created.payload["resumed_from"] == "partial.jsonl"
    assert created.payload["seed_steps"] == plan.steps
    finished = read_events(trace.path)[-1]
    assert finished.type == E.RUN_FINISHED


def test_dangling_tool_call_is_dropped_not_guessed(runner) -> None:
    _, events, _ = runner(STEPS, files={"a.txt": "hello\n"})
    plan = plan_resume(events[:5])  # 停在 tool.call 之后、结果之前
    assert plan.dangling, "必须记下这次没结果的调用"
    last = plan.messages[-1]
    assert not (last.get("role") == "assistant" and last.get("tool_calls")), \
        "不能留下一个没有结果的 assistant(tool_calls)，否则下一次请求会被上游拒绝"


def test_finished_run_cannot_be_resumed(runner) -> None:
    _, events, _ = runner([{"content": "完"}])
    with pytest.raises(ResumeError, match="已经正常结束"):
        plan_resume(events)


def test_legacy_ledger_is_rejected_honestly(runner) -> None:
    _, events, _ = runner(STEPS, files={"a.txt": "hello\n"})
    events = [e for e in events if e.type != E.RUN_FINISHED]  # 模拟中断
    for event in events:
        if event.type == E.TOOL_RESULT:
            event.payload.pop("content", None)
    with pytest.raises(ResumeError, match="没有记录工具输出全文"):
        plan_resume(events)


def test_cli_resume_end_to_end(runner, tmp_path, monkeypatch, capsys) -> None:
    from auditloop.cli import main as cli_main

    _, events, _ = runner(STEPS, files={"a.txt": "hello\n"})
    runs = tmp_path / ".auditloop" / "runs"
    runs.mkdir(parents=True)
    (runs / "partial.jsonl").write_text(
        "\n".join(json.dumps(e.to_dict(), ensure_ascii=False) for e in events[:6]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    code = cli_main(["trace", "resume", "partial", "--mock", "--yes", "--policy", "auto"])
    output = capsys.readouterr().out
    assert code == 0, output
    assert "续跑 partial" in output
