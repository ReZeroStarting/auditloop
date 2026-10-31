"""演示场景：一个带 bug 的小仓库 + 一段脚本化的「模型」。

为什么演示要写死脚本，而不是接真模型：
面试现场没有网络、没有 key、旁听的人也没有耐心等你重试。
脚本化的演示**每次输出都一样**，可以提前排练，可以在任何机器上跑。
它证明的是「管道、策略、账本、验证」这四件事真的在工作，
而不是「这个模型很聪明」——后者本来也不是这个项目要证明的。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .core.loop import RunResult, run_loop
from .llm.mock import ScriptedLLM
from .policy.approvals import AutoApprove
from .policy.policy import Policy
from .tools.builtin import default_registry
from .trace.writer import TraceWriter

DEMO_FILES: dict[str, str] = {
    "README.md": """# demo-app

一个用来演示 auditloop 的小仓库。

```
python -m pytest -q
```

已知问题：`divide` 遇到 0 会抛 ZeroDivisionError，而不是业务上约定的 ValueError。
""",
    "app.py": '''"""一个小小的价格计算模块。"""


def divide(a, b):
    return a / b


def apply_discount(price, rate):
    if not 0 <= rate <= 1:
        raise ValueError("rate 必须在 0 和 1 之间")
    return price * (1 - rate)
''',
    "test_app.py": '''import pytest

from app import apply_discount, divide


def test_divide_ok():
    assert divide(10, 2) == 5


def test_divide_by_zero():
    with pytest.raises(ValueError):
        divide(1, 0)


def test_apply_discount():
    assert apply_discount(100, 0.25) == 75
''',
}

DEMO_SCRIPT: list[dict[str, Any]] = [
    {
        "tool": "todo_write",
        "args": {
            "items": [
                {"content": "定位 divide 的除零问题", "status": "in_progress"},
                {"content": "修改实现并保持接口不变", "status": "pending"},
                {"content": "跑测试验证", "status": "pending"},
            ]
        },
    },
    {"tool": "glob", "args": {"pattern": "**/*.py"}},
    {"tool": "grep", "args": {"pattern": "def divide", "include": "*.py"}},
    # 这一步是刻意的：模型想图省事直接删文件，策略必须拦住它
    {"tool": "shell", "args": {"command": "del /f /q app.py"}},
    {"tool": "fs_read", "args": {"path": "app.py"}},
    {"tool": "fs_read", "args": {"path": "test_app.py"}},
    {
        "tool": "fs_edit",
        "args": {
            "path": "app.py",
            "old": "def divide(a, b):\n    return a / b",
            "new": 'def divide(a, b):\n    if b == 0:\n        raise ValueError("除数不能为 0")\n    return a / b',
        },
    },
    {"tool": "shell", "args": {"command": "python -m pytest -q", "timeout_s": 120}},
    {
        "tool": "todo_write",
        "args": {
            "items": [
                {"content": "定位 divide 的除零问题", "status": "completed"},
                {"content": "修改实现并保持接口不变", "status": "completed"},
                {"content": "跑测试验证", "status": "completed"},
            ]
        },
    },
    {
        "content": (
            "改完了。`app.py` 的 `divide` 在除数为 0 时抛出 `ValueError`，"
            "与 `test_divide_by_zero` 的约定一致；接口签名没动。\n"
            "验证：`python -m pytest -q` 通过。\n"
            "顺带说一句：中途我尝试直接用命令行删掉 app.py，被策略拒绝了——"
            "这正是我希望发生的事。"
        )
    },
]


def build_demo_workspace(root: Path | None = None) -> Path:
    workdir = Path(root) if root else Path(tempfile.mkdtemp(prefix="auditloop-demo-"))
    workdir.mkdir(parents=True, exist_ok=True)
    for rel, content in DEMO_FILES.items():
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    return workdir


def run_demo(
    *,
    workdir: Path | None = None,
    runs_dir: Path | None = None,
    compact_at: int = 700,
    keep_recent: int = 4,
    on_event=None,
) -> tuple[RunResult, Path]:
    workspace = build_demo_workspace(workdir)
    runs_dir = Path(runs_dir) if runs_dir else workspace / ".auditloop" / "runs"
    with TraceWriter(runs_dir, run_id="demo") as trace:
        result = run_loop(
            "修一下 divide 在除数为 0 时的行为，让它和测试的约定一致，跑测试验证。",
            llm=ScriptedLLM(DEMO_SCRIPT),
            registry=default_registry(),
            policy=Policy(mode="ask"),
            approver=AutoApprove(),
            workspace=workspace,
            trace=trace,
            max_steps=14,
            compact_at=compact_at,
            keep_recent=keep_recent,
            verify_cmd="python -m pytest -q",
            on_event=on_event,
        )
    return result, workspace


# ---------------------------------------------------------------------------
# 对照实验：同一段「模型」，只差一道验证门禁
# ---------------------------------------------------------------------------

# 这个脚本演的是最常见的失败：改动看起来合理、其实没修对，然后声称「测试全部通过」。
CONTRAST_SCRIPT: list[dict[str, Any]] = [
    {"tool": "fs_read", "args": {"path": "app.py"}},
    {
        "tool": "fs_edit",
        "args": {
            "path": "app.py",
            "old": "def divide(a, b):\n    return a / b",
            # 看起来在防除零，实际把语义改错了：divide(1, 0) 返回 1.0 而不是抛 ValueError
            "new": "def divide(a, b):\n    return a / (b or 1)",
        },
    },
    {"content": "已修复 divide 的除零问题，并跑过测试：`python -m pytest -q`，全部通过。"},
]


def run_contrast(
    *,
    workdir: Path | None = None,
    runs_dir: Path | None = None,
    on_event=None,
) -> dict[str, Any]:
    """跑两次同样的脚本：一次没有验证门禁，一次有。

    这不是「演示成功」，是演示**同一段行为在两种纪律下得出两种结论**。
    """
    root = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="auditloop-contrast-"))
    runs = Path(runs_dir) if runs_dir else root / ".auditloop" / "runs"
    task = "修一下 divide 在除数为 0 时的行为，让它和测试的约定一致，跑测试验证。"

    plain_ws = build_demo_workspace(root / "without-gate")
    gated_ws = build_demo_workspace(root / "with-gate")

    with TraceWriter(runs, run_id="contrast-without-gate") as trace:
        without = run_loop(
            task,
            llm=ScriptedLLM(CONTRAST_SCRIPT),
            registry=default_registry(),
            policy=Policy(mode="ask"),
            approver=AutoApprove(),
            workspace=plain_ws,
            trace=trace,
            max_steps=8,
            on_event=on_event,
        )

    with TraceWriter(runs, run_id="contrast-with-gate") as trace:
        gated = run_loop(
            task,
            llm=ScriptedLLM(CONTRAST_SCRIPT),
            registry=default_registry(),
            policy=Policy(mode="ask"),
            approver=AutoApprove(),
            workspace=gated_ws,
            trace=trace,
            max_steps=8,
            verify_cmd="python -m pytest -q",
            on_event=on_event,
        )

    return {"without": without, "gated": gated, "root": root}
