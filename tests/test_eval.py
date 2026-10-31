"""评测层测试：通过率怎么算、失败报告里有没有可操作的信息。"""

from __future__ import annotations

from auditloop.eval.report import render_json, render_markdown
from auditloop.eval.runner import run_tasks
from auditloop.eval.schema import Task, load_tasks


def _task(task_id: str, checks: list[dict], **kwargs) -> Task:
    defaults = {
        "prompt": "把事做完",
        "files": {"app.py": "x = 1\n"},
        "script": [{"tool": "glob", "args": {}}, {"content": "做完了"}],
    }
    defaults.update(kwargs)
    return Task(id=task_id, checks=checks, **defaults)


def test_all_checks_pass(tmp_path) -> None:
    report = run_tasks(
        [_task("a", [{"type": "status_is", "status": "ok"}, {"type": "file_exists", "path": "app.py"}])],
        mode="mock",
        traces_dir=tmp_path / "runs",
    )
    assert report.total == 1
    assert report.pass_rate == 1.0
    assert report.results[0].trace_path and "eval-a" in report.results[0].trace_path


def test_failed_check_is_reported(tmp_path) -> None:
    report = run_tasks(
        [
            _task("good", [{"type": "status_is", "status": "ok"}]),
            _task("bad", [{"type": "file_exists", "path": "nope.txt"}]),
        ],
        mode="mock",
        traces_dir=tmp_path / "runs",
    )
    assert report.passed == 1
    assert report.pass_rate == 0.5
    bad = next(r for r in report.results if r.id == "bad")
    assert bad.ok is False
    assert bad.failed_checks and "nope.txt" in bad.failed_checks[0]["detail"]
    md = render_markdown(report)
    assert "通过率：1/2 = 50%" in md
    assert "`bad`" in md


def test_event_level_checks_use_the_ledger(tmp_path) -> None:
    report = run_tasks(
        [
            _task(
                "deny",
                [
                    {"type": "event_seen", "event": "policy.decision", "where": {"denied": True}},
                    {"type": "event_absent", "event": "verification"},
                ],
                script=[{"tool": "shell", "args": {"command": "rm -rf ."}}, {"content": "算了"}],
            )
        ],
        mode="mock",
        traces_dir=tmp_path / "runs",
    )
    assert report.results[0].ok is True, report.results[0].checks
    assert report.results[0].checks[0]["ok"] is True


def test_json_report_is_machine_readable(tmp_path) -> None:
    import json

    report = run_tasks([_task("a", [{"type": "status_is", "status": "ok"}])], mode="mock", traces_dir=tmp_path)
    payload = json.loads(render_json(report))
    assert payload["pass_rate"] == 1.0
    assert payload["results"][0]["id"] == "a"


def test_load_tasks_rejects_broken_json(tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"id": "a", "prompt": "p"}\n{broken\n', encoding="utf-8")
    try:
        load_tasks(path)
    except ValueError as exc:
        assert "不是合法 JSON" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("坏任务集必须报错，不能静默跳过")


def test_modes_filter_skips_inapplicable_tasks(tmp_path) -> None:
    """行为类用例只跑 mock：真实模型不会按剧本犯错，硬套只会得到假失败。"""
    tasks = [
        _task("both", [{"type": "status_is", "status": "ok"}]),
        Task(id="mock-only", prompt="p", checks=[{"type": "status_is", "status": "ok"}], modes=["mock"]),
    ]
    report = run_tasks(tasks, mode="live", traces_dir=tmp_path / "runs", llm=None)
    assert "mock-only" in report.skipped
    assert report.skipped == ["mock-only"]


def test_skipped_is_reported_in_markdown(tmp_path) -> None:
    tasks = [Task(id="x", prompt="p", checks=[], modes=["live"])]
    report = run_tasks(tasks, mode="mock", traces_dir=tmp_path / "runs")
    assert report.total == 0
    assert "跳过 1 条" in render_markdown(report)


def test_repo_task_set_is_valid() -> None:
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "tasks" / "eval.jsonl"
    tasks = load_tasks(path)
    assert len(tasks) >= 5
    assert len({t.id for t in tasks}) == len(tasks), "任务 id 不能重复"
    for task in tasks:
        assert task.checks, f"{task.id} 没有检查项，等于没评"
