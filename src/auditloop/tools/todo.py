"""待办清单：让「打算做什么」变成可看见的状态。

为什么值得单独做一个工具：
模型的长任务失败，很多时候不是不会做，是**做着做着忘了本来要做什么**。
把计划外化成清单，每一步都读一眼，属于最便宜的重试预防。
清单同时会写进账本，事后能回答「它当时以为自己要干什么」。
"""

from __future__ import annotations

from typing import Any

from ..errors import ToolArgError, ToolError
from .base import Tool, ToolContext

STATUS_MARK = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
VALID_STATUS = set(STATUS_MARK)


class TodoWrite(Tool):
    name = "todo_write"
    description = (
        "写入/更新任务清单。items 是完整清单（会整体覆盖），每项 "
        "{content, status}，status 取 pending / in_progress / completed。"
        "开工前先列清单，每完成一项就更新一次。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": "完整清单",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "status": {"type": "string", "enum": sorted(VALID_STATUS)},
                    },
                    "required": ["content"],
                },
            }
        },
        "required": ["items"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        items = args.get("items")
        if not isinstance(items, list) or not items:
            raise ToolArgError("items 必须是非空数组")
        cleaned: list[dict[str, str]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not str(item.get("content") or "").strip():
                raise ToolArgError(f"第 {index + 1} 项缺少 content")
            status = str(item.get("status") or "pending")
            if status not in VALID_STATUS:
                raise ToolArgError(f"第 {index + 1} 项 status 不合法：{status}")
            cleaned.append({"content": str(item["content"]).strip(), "status": status})
        ctx.todos = cleaned
        done = sum(1 for i in cleaned if i["status"] == "completed")
        lines = [f"{STATUS_MARK[i['status']]} {i['content']}" for i in cleaned]
        return f"清单已更新（{done}/{len(cleaned)} 完成）\n" + "\n".join(lines)
