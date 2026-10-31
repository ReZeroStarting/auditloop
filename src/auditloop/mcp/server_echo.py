"""一个最小的 MCP 服务端（stdio），用来测客户端、也用来演示怎么接第三方工具。

跑：python -m auditloop.mcp.server_echo
它只实现三件事：initialize、tools/list、tools/call。
协议细节按 MCP 的 stdio 传输走：一行一个 JSON-RPC 消息。
"""

from __future__ import annotations

import json
import sys
from typing import Any

TOOLS = [
    {
        "name": "echo",
        "description": "把输入原样返回，用来验证链路通不通。",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "两个整数相加。",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    },
]


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        return _ok(
            request_id,
            {
                "protocolVersion": message.get("params", {}).get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "auditloop-echo", "version": "0.1.0"},
            },
        )
    if method and method.startswith("notifications/"):
        return None
    if method == "tools/list":
        return _ok(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            return _ok(request_id, {"content": [{"type": "text", "text": str(args.get("text", ""))}]})
        if name == "add":
            try:
                total = int(args.get("a", 0)) + int(args.get("b", 0))
            except (TypeError, ValueError):
                return _ok(request_id, {"content": [{"type": "text", "text": "参数必须是整数"}], "isError": True})
            return _ok(request_id, {"content": [{"type": "text", "text": str(total)}]})
        return _ok(request_id, {"content": [{"type": "text", "text": f"未知工具 {name}"}], "isError": True})
    if method == "ping":
        return _ok(request_id, {})
    return _err(request_id, -32601, f"未实现的方法：{method}")


def _ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _err(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def main() -> int:
    # Windows 上 stdio 默认是本地代码页（GBK），JSON 里的中文会在这里被撕碎。
    # 客户端已经按 UTF-8 收发，服务端必须显式对齐——这是跨平台 MCP 服务的必修课。
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
