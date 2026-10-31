"""把 MCP 工具接进工具注册表。

一个刻意的默认：MCP 工具一律标成 mutating=True。
因为从协议上你无法知道对面那个工具会不会改状态，而「不知道」必须按「会改」处理。
宁可多问一次，也不要让一个陌生工具在无人值守时改掉文件。
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Tool, ToolContext
from ..tools.registry import Registry
from .client import StdioMCPClient


class MCPTool(Tool):
    mutating = True

    def __init__(self, client: StdioMCPClient, info: dict[str, Any], *, prefix: str = "mcp_"):
        self.client = client
        self.raw_name = str(info.get("name") or "")
        if not self.raw_name:
            raise ValueError("MCP 工具缺少 name")
        self.name = f"{prefix}{self.raw_name}"
        self.description = str(info.get("description") or f"MCP 工具 {self.raw_name}")
        schema = info.get("inputSchema") or {"type": "object", "properties": {}, "required": []}
        self.parameters = schema

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return self.client.call_tool(self.raw_name, args)


def register_mcp_tools(registry: Registry, client: StdioMCPClient, *, prefix: str = "mcp_") -> list[str]:
    names: list[str] = []
    for info in client.list_tools():
        tool = MCPTool(client, info, prefix=prefix)
        registry.add(tool)
        names.append(tool.name)
    return names
