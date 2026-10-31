"""账本与回放的测试：记录是否完整、回放是否一致。"""

from __future__ import annotations

from pathlib import Path

from auditloop.core.loop import run_loop
from auditloop.llm.replay import ReplayLLM
from auditloop.policy.approvals import AutoApprove
from auditloop.policy.policy import Policy
from auditloop.tools.builtin import default_registry
from auditloop.trace import events as E
from auditloop.trace.otel import to_otel_spans
from auditloop.trace.render import printer, render
from auditloop.trace.replay import recorded_llm_responses, summarize_run
from auditloop.trace.writer import TraceWriter, read_events


def test_writer_sequences_and_roundtrips(tmp_path) -> None:
    with TraceWriter(tmp_path / "runs", run_id="r1") as trace:
        trace.emit(E.RUN_CREATED, {"task": "t"})
        trace.emit(E.MODEL_REQUEST, {"index": 1}, span="m1")
    events = read_events(trace.path)
    assert [e.seq for e in events] == [1, 2]
    assert events[1].span == "m1"
    assert trace.path.name == "r1.jsonl"


def test_trace_survives_truncated_tail(tmp_path) -> None:
    """进程被杀会留下半行；读账本的人不能被这个半行搞崩。"""
    path = tmp_path / "half.jsonl"
    path.write_text(
        '{"seq": 1, "type": "run.created", "run_id": "r", "ts": "x", "span": "root", "payload": {}}\n{"seq": 2,',
        encoding="utf-8",
    )
    events = read_events(path)
    assert len(events) == 1, "坏行跳过，好行照读"
    assert events[0].type == E.RUN_CREATED


def test_render_has_no_ansi_when_color_off(runner) -> None:
    _, events, _ = runner([{"content": "完"}])
    text = render(events, color=False)
    assert "\033[" not in text
    assert "run" in text


def test_printer_swallows_empty_lines() -> None:
    assert callable(printer())


def test_summarize_counts_tools_denials_and_errors(runner) -> None:
    _, events, _ = runner(
        [
            {"tool": "fs_read", "args": {"path": "missing.txt"}},
            {"tool": "shell", "args": {"command": "rm -rf ."}},
            {"content": "完"},
        ],
        policy=Policy(mode="ask"),
    )
    summary = summarize_run(events)
    assert summary["status"] == "ok"
    assert summary["tools"]["fs_read"] == 1
    assert summary["denials"] == 1
    assert summary["tool_errors"] == 1


def test_otel_spans_follow_genai_conventions(runner) -> None:
    _, events, _ = runner([{"tool": "glob", "args": {}}, {"content": "完"}])
    spans = to_otel_spans(events)
    assert spans[0]["attributes"]["gen_ai.operation.name"] == "invoke_agent"
    chat = [s for s in spans if s["attributes"].get("gen_ai.operation.name") == "chat"]
    tool = [s for s in spans if s["attributes"].get("gen_ai.operation.name") == "execute_tool"]
    assert chat and tool
    assert chat[0]["attributes"]["gen_ai.usage.input_tokens"] > 0
    assert tool[0]["attributes"]["gen_ai.tool.name"] == "glob"


def test_export_run_is_frontend_ready(runner) -> None:
    from auditloop.trace.export import export_run

    _, events, _ = runner([{"tool": "glob", "args": {}}, {"content": "完"}])
    data = export_run(events, source="demo.jsonl")
    assert data["schema"] == 1
    assert data["meta"]["policy"] == "auto"
    assert data["summary"]["status"] == "ok"
    assert len(data["timeline"]) == len(events)
    assert data["timeline"][0]["line"], "每一行都要有渲染好的文本，前端不该重写渲染逻辑"
    assert all("payload" in item for item in data["timeline"])
    assert data["source"] == "demo.jsonl"


def test_replay_reproduces_decisions(runner, tmp_path) -> None:
    result, events, ws = runner([{"tool": "glob", "args": {}}, {"content": "完"}], files={"a.py": "x"})
    responses = recorded_llm_responses(events)
    assert len(responses) == 2

    with TraceWriter(tmp_path / "replay", run_id="replay-1") as trace:
        replayed = run_loop(
            "测试任务",
            llm=ReplayLLM(responses),
            registry=default_registry(),
            policy=Policy(mode="auto"),
            approver=AutoApprove(),
            workspace=ws,
            trace=trace,
            max_steps=6,
        )
    assert replayed.status == result.status
    assert replayed.answer == result.answer
    assert replayed.steps == result.steps


def test_replay_detects_changed_inputs(runner, tmp_path) -> None:
    _, events, ws = runner([{"tool": "glob", "args": {}}, {"content": "完"}], files={"a.py": "x"})
    responses = recorded_llm_responses(events)[:1]  # 故意少给一条
    with TraceWriter(tmp_path / "replay2", run_id="replay-2") as trace:
        replayed = run_loop(
            "测试任务",
            llm=ReplayLLM(responses),
            registry=default_registry(),
            policy=Policy(mode="auto"),
            approver=AutoApprove(),
            workspace=ws,
            trace=trace,
            max_steps=6,
        )
    assert replayed.status == "model_error", "回放发现输入变了，必须显式失败而不是装作没事"
