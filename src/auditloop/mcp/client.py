"""MCP（Model Context Protocol）客户端：stdio 传输，JSON-RPC 2.0，按行分帧。

为什么值得接 MCP：
工具是这个项目里最容易「越写越多、越写越乱」的部分。MCP 把它变成进程外的东西——
第三方工具不用改 auditloop 的代码就能挂进来，也不用把它们的依赖装进主进程。
这是 agent 生态目前的实际约定，接它比自己发明一套插件协议划算。

这个实现只用标准库：
- 一个读线程把 stdout 的每一行塞进队列（Windows 上没有 select，别指望非阻塞读）；
- stderr 单独一个线程排空，否则管道写满会让子进程卡死（真实踩过）；
- 超时按「等不到响应」算失败，而不是无限等。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from typing import Any

from .. import __version__

PROTOCOL_VERSION = "2025-06-18"


class MCPError(RuntimeError):
    pass


class StdioMCPClient:
    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float = 20.0,
    ):
        self.command = command
        self.cwd = cwd
        self.env = env
        self.timeout_s = timeout_s
        self._proc: subprocess.Popen[str] | None = None
        self._queue: "queue.Queue[str | None]" = queue.Queue()
        self._stderr: list[str] = []
        self._next_id = 0
        self.server_info: dict[str, Any] = {}

    # ---------- 生命周期 ----------
    def start(self) -> dict[str, Any]:
        try:
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=self.cwd,
                env={**os.environ, **(self.env or {})},
            )
        except OSError as exc:
            raise MCPError(f"无法启动 MCP 服务 {self.command!r}：{exc}") from exc

        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "auditloop", "version": __version__},
            },
        )
        self.server_info = result or {}
        self._notify("notifications/initialized", {})
        return self.server_info

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None

    def __enter__(self) -> "StdioMCPClient":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------- 协议 ----------
    def list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list", {})
        return list((result or {}).get("tools") or [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result is None:
            raise MCPError(f"工具 {name} 返回空结果")
        if result.get("isError"):
            raise MCPError(_flatten(result) or f"工具 {name} 执行失败")
        return _flatten(result)

    # ---------- 内部 ----------
    def _pump_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            self._queue.put(line)
        self._queue.put(None)

    def _pump_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            self._stderr.append(line.rstrip())
            if len(self._stderr) > 200:
                del self._stderr[:100]

    def _send(self, payload: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise MCPError("MCP 服务未启动")
        self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            message = self._recv()
            if message.get("id") != request_id:
                continue  # 服务端主动通知，跳过
            if "error" in message:
                raise MCPError(f"{method} 失败：{message['error']}")
            return message.get("result")

    def _recv(self) -> dict[str, Any]:
        try:
            line = self._queue.get(timeout=self.timeout_s)
        except queue.Empty as exc:
            tail = "\n".join(self._stderr[-5:])
            raise MCPError(f"等待 MCP 响应超时（{self.timeout_s}s）。服务端 stderr：\n{tail}") from exc
        if line is None:
            tail = "\n".join(self._stderr[-10:])
            raise MCPError(f"MCP 服务已退出。stderr：\n{tail}")
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise MCPError(f"MCP 返回了非 JSON 的一行：{line[:200]!r}") from exc


def _flatten(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in result.get("content") or []:
        if item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
        else:
            parts.append(json.dumps(item, ensure_ascii=False))
    if not parts and result.get("structuredContent") is not None:
        return json.dumps(result["structuredContent"], ensure_ascii=False)
    return "\n".join(parts)
