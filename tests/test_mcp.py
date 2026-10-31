"""MCP 客户端测试：跑一个真的子进程，走真的 JSON-RPC。"""

from __future__ import annotations

import sys
from pathlib import Path

from auditloop.mcp.bridge import register_mcp_tools
from auditloop.mcp.client import StdioMCPClient
from auditloop.tools.base import ToolContext
from auditloop.tools.registry import Registry

SRC = Path(__file__).resolve().parents[1] / "src"


def _client() -> StdioMCPClient:
    # 用真实子进程 + 真实 stdio，不用 mock：这一层的 bug 只有跑起来才会暴露
    return StdioMCPClient([sys.executable, "-m", "auditloop.mcp.server_echo"], env={"PYTHONPATH": str(SRC)})


def test_handshake_lists_tools() -> None:
    with _client() as client:
        names = {tool["name"] for tool in client.list_tools()}
        assert {"echo", "add"} <= names
        assert client.server_info["serverInfo"]["name"] == "auditloop-echo"


def test_call_tool_roundtrip() -> None:
    with _client() as client:
        assert client.call_tool("echo", {"text": "你好"}) == "你好"
        assert client.call_tool("add", {"a": 2, "b": 3}) == "5"


def test_mcp_tools_are_registered_as_mutating() -> None:
    with _client() as client:
        registry = Registry()
        names = register_mcp_tools(registry, client)
        assert "mcp_echo" in names
        tool = registry.get("mcp_echo")
        assert tool is not None and tool.mutating is True, "来源不明的工具必须按「会改状态」处理"

        ctx = ToolContext(workspace=Path.cwd())
        out = registry.dispatch("mcp_echo", {"text": "hi"}, ctx)
        assert out == "hi"


def test_missing_server_reports_clean_error() -> None:
    from auditloop.mcp.client import MCPError

    client = StdioMCPClient([sys.executable, "-c", "import sys; sys.exit(2)"], timeout_s=5)
    try:
        client.start()
    except MCPError as exc:
        assert "MCP" in str(exc) or "退出" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("服务起不来必须报错")
    finally:
        client.close()
