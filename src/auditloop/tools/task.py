"""子 agent：把一段独立的探索丢给一个「上下文干净」的下层 agent。

为什么需要：主循环的上下文是稀缺资源。让主 agent 去翻 20 个文件找调用关系，
它会带着 20 个文件的原文走到最后。子 agent 的价值就是**把噪音留在外面**，
只把结论带回来。

交接方式是刻意的窄接口：`spawn(prompt, tools=None) -> str`。
没有共享内存、没有消息队列——子 agent 挂了，主 agent 只是拿到一条错误。

状态：骨架阶段。接口已定，实现在 P2（见 docs/roadmap.md）。
"""

from __future__ import annotations

from typing import Any

from ..errors import ToolError
from .base import Tool, ToolContext


class SpawnTask(Tool):
    name = "task"
    description = (
        "把一个独立的子任务交给下层 agent 执行，只返回它的结论。"
        "适合「在大范围里找一个东西」这类会产生大量噪音的活。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "子任务的完整描述，子 agent 看不到当前对话"},
            "tools": {"type": "string", "description": "可选的工具白名单，逗号分隔，默认只读工具"},
        },
        "required": ["prompt"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        spawn = ctx.extra.get("spawn")
        if spawn is None:
            raise ToolError(
                "当前运行没有启用子 agent（P2 未实现）。"
                "如需启用，请用 auditloop run --allow-subagent，或先用 grep/fs_read 自己完成。"
            )
        return spawn(args["prompt"], args.get("tools"))
