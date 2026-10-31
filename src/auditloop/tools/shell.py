"""执行命令。这是整个 harness 里权限最重的一把刀。

四道约束：
1. 必须过策略闸门（哪些命令算只读、哪些要审批）；
2. 有超时（没超时的 agent 会挂在一个 `npm install` 上直到你发现）；
3. 输出截断（一次 `pytest` 能吐几万行，不截断等于自杀）；
4. 真实退出码必须原样返回，不许包装成 "success/failure" 字符串——
   上层要靠 rc 判断，这是 keepwatch 那条「账本里必须有 rc=0」的同一条原则。
"""

from __future__ import annotations

import subprocess
from typing import Any

from ..errors import ToolError
from .base import Tool, ToolContext

MAX_CAPTURE = 20_000


def run_shell(command: str, cwd: str, timeout_s: int = 60) -> tuple[int, str]:
    """统一入口，测试和 CLI 都走它（facade 便于替换成容器执行）。"""
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"命令超时（{timeout_s}s）：{command}") from exc
    except OSError as exc:
        raise ToolError(f"命令无法执行：{exc}") from exc
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > MAX_CAPTURE:
        out = out[: MAX_CAPTURE // 2] + f"\n…（截断 {len(out) - MAX_CAPTURE} 字符）…\n" + out[-MAX_CAPTURE // 2 :]
    return proc.returncode, out


class Shell(Tool):
    name = "shell"
    description = (
        "在工作目录内执行一条命令，返回真实退出码与输出。"
        "只读命令（ls/cat/git status 等）可直接执行；会改动状态的命令需要审批。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令，例如 python -m pytest -q"},
            "timeout_s": {"type": "integer", "description": "超时秒数，默认 60，上限 600"},
        },
        "required": ["command"],
    }
    mutating = True

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        command = str(args["command"]).strip()
        if not command:
            raise ToolError("命令为空")
        timeout = min(600, max(1, int(args.get("timeout_s", 60))))
        rc, output = run_shell(command, str(ctx.workspace), timeout)
        return f"rc={rc}\n{output}".rstrip()
