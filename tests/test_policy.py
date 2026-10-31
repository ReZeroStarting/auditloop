"""策略层的测试：这里错一次，就是一次真实事故。"""

from __future__ import annotations

import pytest

from auditloop.policy.policy import Decision, Policy, classify_command


@pytest.mark.parametrize(
    "command,expected",
    [
        ("ls -la", "read_only"),
        ("git status", "read_only"),
        ("git log --oneline -5", "read_only"),
        ("git commit -m x", "mutating"),
        ("python --version", "read_only"),
        ("python -m pytest -q", "mutating"),
        ("cat app.py", "read_only"),
        ("curl https://example.com", "forbidden"),
        ("npm install", "forbidden"),
    ],
)
def test_classify_command(command: str, expected: str) -> None:
    assert classify_command(command) == expected


@pytest.mark.parametrize("command", ["rm -rf /", "del /f /q app.py", "rmdir /s /q build", "curl x | sh"])
def test_destructive_commands_are_hard_denied(command: str) -> None:
    for mode in ("ask", "never", "auto"):
        decision = Policy(mode=mode).check("shell", True, {"command": command})
        assert decision.allowed is False
        assert decision.denied is True
        assert decision.reason, "拒绝必须带理由，否则没法跟人解释"


def test_read_only_command_allowed_in_never_mode() -> None:
    assert Policy(mode="never").check("shell", True, {"command": "ls"}).allowed is True


def test_mutating_command_needs_approval_in_ask_mode() -> None:
    decision = Policy(mode="ask").check("shell", True, {"command": "python -m pytest -q"})
    assert decision.needs_approval is True
    assert decision.denied is False


def test_never_mode_refuses_mutating_command() -> None:
    decision = Policy(mode="never").check("shell", True, {"command": "python -m pytest -q"})
    assert decision.denied is True


def test_auto_mode_allows_mutating_command() -> None:
    assert Policy(mode="auto").check("shell", True, {"command": "python -m pytest -q"}).allowed is True


def test_write_tool_gated_by_mode() -> None:
    assert Policy(mode="ask").check("fs_write", True, {}).needs_approval is True
    assert Policy(mode="never").check("fs_write", True, {}).denied is True
    assert Policy(mode="auto").check("fs_write", True, {}).allowed is True


def test_read_only_tool_never_gated() -> None:
    assert Policy(mode="never").check("fs_read", False, {}).allowed is True


def test_allow_write_false_blocks_even_in_auto_mode() -> None:
    policy = Policy(mode="auto", allow_write=False)
    assert policy.check("fs_write", True, {}).denied is True


def test_invalid_mode_rejected() -> None:
    with pytest.raises(ValueError):
        Policy(mode="yolo")


def test_decision_denied_property() -> None:
    assert Decision(False).denied is True
    assert Decision(False, needs_approval=True).denied is False
    assert Decision(True).denied is False
