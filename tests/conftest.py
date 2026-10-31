from __future__ import annotations

import pytest

from auditloop.core.loop import run_loop
from auditloop.llm.mock import ScriptedLLM
from auditloop.policy.approvals import AutoApprove
from auditloop.policy.policy import Policy
from auditloop.tools.builtin import default_registry
from auditloop.trace.writer import TraceWriter, read_events


@pytest.fixture()
def runner(tmp_path):
    """跑一次完整的 run，返回 (结果, 事件, 工作区)。"""

    def _run(
        steps,
        *,
        files=None,
        policy=None,
        mode="auto",
        approver=None,
        max_steps=6,
        verify=None,
        budget=None,
        compact_at=10**9,
        llm=None,
        task="测试任务",
    ):
        ws = tmp_path / "ws"
        ws.mkdir(exist_ok=True)
        for name, content in (files or {}).items():
            path = ws / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        trace = TraceWriter(tmp_path / "runs")
        result = run_loop(
            task,
            llm=llm or ScriptedLLM(steps),
            registry=default_registry(),
            policy=policy or Policy(mode=mode),
            approver=approver or AutoApprove(),
            workspace=ws,
            trace=trace,
            max_steps=max_steps,
            verify_cmd=verify,
            budget=budget,
            compact_at=compact_at,
        )
        events = read_events(trace.path)
        trace.close()
        return result, events, ws

    return _run
