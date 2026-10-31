"""子 agent：噪音留在外面，结论带回来。

测试策略：父与子共用一个 ScriptedLLM，脚本按调用顺序被两边依次取走——
这恰好也验证了「子 run 是同步嵌套执行的」。
"""

from __future__ import annotations

import pytest

from auditloop.core.subagent import subset_registry
from auditloop.tools.builtin import default_registry
from auditloop.trace import events as E
from auditloop.trace.writer import TraceWriter, read_events


def test_subagent_runs_and_returns_conclusion(runner, tmp_path) -> None:
    result, events, _ = runner(
        [
            {"tool": "task", "args": {"prompt": "找一下 divide 定义在哪"}},
            {"content": "子结论：divide 在 app.py 第 4 行。"},
            {"content": "根据子结论，问题定位在 app.py。"},
        ],
        files={"app.py": "def divide(a, b):\n    return a / b\n"},
        allow_subagent=True,
        max_steps=4,
    )
    assert result.status == "ok", result.answer
    assert "根据子结论" in result.answer
    tool_result = next(e for e in events if e.type == E.TOOL_RESULT and e.payload["name"] == "task")
    assert tool_result.payload["ok"] is True
    assert "子 agent" in str(tool_result.payload["content"])
    assert "子结论" in str(tool_result.payload["content"])


def test_subagent_has_its_own_ledger(runner) -> None:
    _, events, _ = runner(
        [
            {"tool": "task", "args": {"prompt": "查"}},
            {"content": "结论"},
            {"content": "完"},
        ],
        files={"a.txt": "x"},
        allow_subagent=True,
        max_steps=4,
    )
    content = next(e for e in events if e.type == E.TOOL_RESULT and e.payload["name"] == "task").payload["content"]
    assert "sub1.jsonl" in str(content), "父账本必须能指到子账本的路径"


def test_subagent_only_gets_read_only_tools_by_default() -> None:
    registry = subset_registry(default_registry(), None)
    assert "fs_read" in registry.names()
    assert "shell" not in registry.names(), "子 agent 默认不该有执行权限"
    assert "fs_write" not in registry.names()


def test_whitelist_can_be_narrowed() -> None:
    registry = subset_registry(default_registry(), ["grep"])
    assert registry.names() == ["grep"]


def test_task_tool_refuses_when_disabled(runner) -> None:
    result, events, _ = runner(
        [{"tool": "task", "args": {"prompt": "查"}}, {"content": "算了"}],
        allow_subagent=False,
    )
    failure = next(e for e in events if e.type == E.TOOL_RESULT and not e.payload["ok"])
    assert "没有启用子 agent" in str(failure.payload["error"])
    assert result.status == "ok"


def test_subagent_cannot_recurse(runner, tmp_path) -> None:
    """子 run 里再调 task 必须失败——递归子 agent 是烧钱最快的方式。"""
    result, events, _ = runner(
        [
            {"tool": "task", "args": {"prompt": "第一层"}},
            {"tool": "task", "args": {"prompt": "第二层"}},  # 这一条会发生在子 run 里
            {"content": "子 agent 说它做不到"},
            {"content": "父 agent 收尾"},
        ],
        files={"a.txt": "x"},
        allow_subagent=True,
        max_steps=4,
    )
    assert result.status == "ok"

    sub_ledgers = list((tmp_path / "runs").glob("*-sub1.jsonl"))
    assert sub_ledgers, "子 agent 应该留下自己的账本"
    sub_events = read_events(sub_ledgers[0])
    failures = [e for e in sub_events if e.type == E.TOOL_RESULT and not e.payload["ok"]]
    assert failures, "子 run 里的 task 调用必须是失败记录"
    # 递归被两道防线拦住：只读白名单里没有 task（更早），以及 spawn=None
    message = str(failures[0].payload["error"])
    assert "task" in message
    assert not list((tmp_path / "runs").glob("*-sub1-sub*.jsonl")), "不允许出现孙 agent"


def test_subagent_usage_is_counted_in_parent(runner) -> None:
    """子 run 烧的 token 必须进父 run 的账，否则成本会系统性偏低。"""
    _, events, _ = runner([{"content": "无子任务"}], files={"a.txt": "x"})
    baseline = next(e for e in events if e.type == E.RUN_FINISHED).payload["input_tokens"]

    _, events2, _ = runner(
        [
            {"tool": "task", "args": {"prompt": "查"}},
            {"content": "结论"},
            {"content": "完"},
        ],
        files={"a.txt": "x"},
        allow_subagent=True,
        max_steps=4,
    )
    with_sub = next(e for e in events2 if e.type == E.RUN_FINISHED).payload["input_tokens"]
    assert with_sub > baseline
