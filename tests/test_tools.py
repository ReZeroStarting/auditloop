"""工具层测试：路径越界、参数校验、编辑的唯一性。"""

from __future__ import annotations

import pytest

from auditloop.errors import ToolArgError, ToolError
from auditloop.tools.base import ToolContext
from auditloop.tools.builtin import default_registry
from auditloop.tools.fs import EditFile, Glob, Grep, ReadFile, WriteFile


@pytest.fixture()
def ctx(tmp_path):
    (tmp_path / "app.py").write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    return ToolContext(workspace=tmp_path)


def test_write_then_read_roundtrip(ctx) -> None:
    WriteFile().run({"path": "a/b.txt", "content": "你好\n世界\n"}, ctx)
    out = ReadFile().run({"path": "a/b.txt"}, ctx)
    assert "你好" in out
    assert "1| " in out, "读文件必须带行号，否则模型改代码只能靠猜"


def test_path_escape_is_rejected(ctx) -> None:
    with pytest.raises(PermissionError):
        ReadFile().run({"path": "../../etc/passwd"}, ctx)


def test_read_missing_file_raises_tool_error(ctx) -> None:
    with pytest.raises(ToolError):
        ReadFile().run({"path": "nope.py"}, ctx)


def test_edit_requires_unique_match(ctx) -> None:
    with pytest.raises(ToolError, match="没找到"):
        EditFile().run({"path": "app.py", "old": "不存在的内容", "new": "x"}, ctx)
    (ctx.workspace / "dup.py").write_text("a\na\n", encoding="utf-8")
    with pytest.raises(ToolError, match="不唯一"):
        EditFile().run({"path": "dup.py", "old": "a", "new": "b"}, ctx)


def test_edit_applies(ctx) -> None:
    EditFile().run({"path": "app.py", "old": "return a / b", "new": "return a / (b or 1)"}, ctx)
    assert "b or 1" in (ctx.workspace / "app.py").read_text(encoding="utf-8")


def test_glob_skips_vendor_dirs(ctx) -> None:
    (ctx.workspace / "node_modules").mkdir()
    (ctx.workspace / "node_modules" / "x.js").write_text("x", encoding="utf-8")
    out = Glob().run({"pattern": "**/*"}, ctx)
    assert "x.js" not in out


def test_grep_reports_file_and_line(ctx) -> None:
    out = Grep().run({"pattern": "def divide", "include": "*.py"}, ctx)
    assert "app.py:1" in out


def test_grep_invalid_regex_is_model_error(ctx) -> None:
    with pytest.raises(ToolArgError):
        Grep().run({"pattern": "([", "include": "*.py"}, ctx)


def test_registry_rejects_unknown_param(ctx) -> None:
    registry = default_registry()
    with pytest.raises(ToolArgError, match="未知参数"):
        registry.dispatch("fs_read", {"path": "app.py", "line": 3}, ctx)


def test_registry_requires_required_param(ctx) -> None:
    registry = default_registry()
    with pytest.raises(ToolArgError, match="缺少必填参数"):
        registry.dispatch("fs_read", {}, ctx)


def test_registry_rejects_wrong_type(ctx) -> None:
    registry = default_registry()
    with pytest.raises(ToolArgError, match="应该是 integer"):
        registry.dispatch("fs_read", {"path": "app.py", "limit": "很多"}, ctx)


def test_todo_write_renders_progress(ctx) -> None:
    out = default_registry().dispatch(
        "todo_write",
        {"items": [{"content": "看代码", "status": "completed"}, {"content": "改代码"}]},
        ctx,
    )
    assert "1/2 完成" in out
    assert ctx.todos[1]["status"] == "pending"


def test_shell_returns_real_exit_code(ctx) -> None:
    registry = default_registry()
    assert "rc=0" in registry.dispatch("shell", {"command": "python -c \"print(1)\""}, ctx)
    assert "rc=1" in registry.dispatch("shell", {"command": "python -c \"raise SystemExit(1)\""}, ctx)
