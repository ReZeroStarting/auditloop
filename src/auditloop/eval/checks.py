"""确定性检查：一条任务过没过，用可复算的条件判断。

检查项刻意做成「声明式字典」而不是代码，原因：
任务集是给人补充的。让人写一段 Python 断言，门槛太高，最后就没人加用例；
写一行 JSON `{"type": "file_contains", "path": "app.py", "text": "ZeroDivisionError"}`
是可以被要求必须做的。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..tools.shell import run_shell
from ..trace.events import Event


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def run_checks(
    checks: list[dict[str, Any]],
    *,
    answer: str,
    status: str,
    workspace: Path,
    events: list[Event],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for check in checks:
        kind = str(check.get("type") or "")
        ok, detail = _evaluate(kind, check, answer, status, workspace, events)
        results.append({"type": kind, "ok": ok, "detail": detail})
    return results


def _evaluate(kind: str, check: dict[str, Any], answer: str, status: str, workspace: Path, events: list[Event]) -> tuple[bool, str]:
    if kind == "contains":
        needle = str(check.get("text") or "")
        return needle in answer, f"回答里{'包含' if needle in answer else '不包含'} `{needle[:60]}`"
    if kind == "not_contains":
        needle = str(check.get("text") or "")
        return needle not in answer, f"回答里{'仍包含' if needle in answer else '不包含'} `{needle[:60]}`"
    if kind == "status_is":
        want = str(check.get("status") or "")
        return status == want, f"status={status}（期望 {want}）"
    if kind == "file_exists":
        path = workspace / str(check.get("path") or "")
        return path.is_file(), f"{check.get('path')} {'存在' if path.is_file() else '不存在'}"
    if kind == "file_contains":
        path = workspace / str(check.get("path") or "")
        text = _read(path)
        needle = str(check.get("text") or "")
        ok = needle in text
        return ok, f"{check.get('path')} {'包含' if ok else '不包含'} `{needle[:60]}`"
    if kind == "file_not_contains":
        path = workspace / str(check.get("path") or "")
        needle = str(check.get("text") or "")
        ok = needle not in _read(path)
        return ok, f"{check.get('path')} {'仍包含' if not ok else '不包含'} `{needle[:60]}`"
    if kind == "event_seen":
        want_type = str(check.get("event") or "")
        where = check.get("where") or {}
        for event in events:
            if event.type != want_type:
                continue
            if all(event.payload.get(k) == v for k, v in where.items()):
                return True, f"账本里有 {want_type} {where}"
        return False, f"账本里没有 {want_type} {where}"
    if kind == "event_absent":
        want_type = str(check.get("event") or "")
        where = check.get("where") or {}
        for event in events:
            if event.type == want_type and all(event.payload.get(k) == v for k, v in where.items()):
                return False, f"账本里出现了不该有的 {want_type} {where}"
        return True, f"账本里没有 {want_type} {where}"
    if kind == "shell_ok":
        command = str(check.get("command") or "")
        if not command:
            return False, "shell_ok 缺少 command"
        try:
            rc, _ = run_shell(command, str(workspace), timeout_s=int(check.get("timeout_s", 120)))
        except Exception as exc:  # 命令都跑不起来，算失败
            return False, f"命令执行失败：{exc}"
        want = int(check.get("expect_rc", 0))
        return rc == want, f"`{command[:60]}` rc={rc}（期望 {want}）"
    return False, f"未知检查类型 `{kind}`"
