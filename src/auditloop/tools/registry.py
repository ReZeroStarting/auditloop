"""工具注册表：Schema 校验 + 派发 + 错误归一。

不引入 jsonschema 依赖，只实现模型真正会用到的子集：
type 检查、required、未知参数拒绝。
「未知参数直接拒绝」是刻意的——模型臆造一个参数（比如给 fs_read 传 line），
静默忽略会让它以为自己在正常工作，然后一路错到底。
"""

from __future__ import annotations

from typing import Any

from ..errors import ToolArgError
from .base import Tool, ToolContext

_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate(parameters: dict[str, Any], args: dict[str, Any]) -> None:
    if not isinstance(args, dict):
        raise ToolArgError("参数必须是 JSON 对象")
    props: dict[str, Any] = parameters.get("properties") or {}
    for key in parameters.get("required") or []:
        if key not in args:
            raise ToolArgError(f"缺少必填参数 `{key}`")
    for key, value in args.items():
        if key not in props:
            allowed = ", ".join(props) or "（无）"
            raise ToolArgError(f"未知参数 `{key}`，可用参数：{allowed}")
        expected = props[key].get("type")
        py_type = _TYPE_MAP.get(expected or "")
        if py_type is None:
            continue
        if expected == "integer" and isinstance(value, bool):
            raise ToolArgError(f"参数 `{key}` 应该是整数，收到布尔值")
        if not isinstance(value, py_type):
            raise ToolArgError(f"参数 `{key}` 应该是 {expected}，收到 {type(value).__name__}")
    enum = params_enum(parameters)
    for key, allowed in enum.items():
        if key in args and args[key] not in allowed:
            raise ToolArgError(f"参数 `{key}` 只能是 {allowed}，收到 {args[key]!r}")


def params_enum(parameters: dict[str, Any]) -> dict[str, list[Any]]:
    out = {}
    for key, spec in (parameters.get("properties") or {}).items():
        if isinstance(spec, dict) and isinstance(spec.get("enum"), list):
            out[key] = spec["enum"]
    return out


class Registry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("工具必须有名字")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        return [self._tools[name].spec() for name in sorted(self._tools)]

    def dispatch(self, name: str, args: dict[str, Any], ctx: ToolContext) -> str:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolArgError(f"未知工具 `{name}`，可用：{', '.join(self.names())}")
        validate(tool.parameters, args)
        return tool.run(args, ctx)
