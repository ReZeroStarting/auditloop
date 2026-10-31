"""工具层基类。

一个工具 = 名字 + 给模型看的说明 + JSON Schema + 实现。
三层校验缺一不可：
1. Schema 校验（模型给的参数形状对不对）——错在这里是**模型的错**，回填给它自己改；
2. 策略校验（这件事允不允许做）——错在这里是**用户的选择**，回填也没用；
3. 执行校验（文件在不在、命令有没有超时）——错在这里是**环境的事**，要如实上报。
把这三类混在一起，是 agent 永远学不会纠错的根因。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolContext:
    """一次 run 内所有工具共享的上下文。"""

    workspace: Path
    policy: Any = None
    todos: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def resolve(self, raw: str) -> Path:
        """把用户/模型给的路径解析成绝对路径，并拒绝越界。"""
        candidate = Path(raw or ".").expanduser()
        full = candidate.resolve() if candidate.is_absolute() else (self.workspace / candidate).resolve()
        try:
            full.relative_to(self.workspace)
        except ValueError as exc:
            raise PermissionError(f"路径越界（不在工作目录内）：{raw}") from exc
        return full


class Tool:
    """所有工具的基类。子类只需要填 4 个字段 + 一个 run()。"""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    mutating: bool = False  # 会改变磁盘状态的工具，必须过策略闸门

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        raise NotImplementedError

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Tool {self.name}{' *mutating' if self.mutating else ''}>"
