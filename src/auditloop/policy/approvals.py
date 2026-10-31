"""审批：需要人点头的操作，怎么问。

三种实现对应三种运行场景：
- AutoApprove   自动化 / 演示 / 评测（明确知道自己在全自动跑）
- RejectAll     无人值守的只读巡检（问了也没人答，那就当作拒绝）
- ConsoleApprover  人在终端时（默认，最安全）

关键设计：**默认没有审批者时一律拒绝**。
「问不到人就默认同意」是绝大多数 agent 事故的起点。
"""

from __future__ import annotations

from typing import Any, Protocol


class Approver(Protocol):
    def ask(self, tool: str, args: dict[str, Any], reason: str) -> bool: ...


class AutoApprove:
    def ask(self, tool: str, args: dict[str, Any], reason: str) -> bool:
        return True


class RejectAll:
    def __init__(self, reason: str = "无人可审批，按拒绝处理"):
        self.reason = reason

    def ask(self, tool: str, args: dict[str, Any], reason: str) -> bool:
        return False


class ConsoleApprover:
    """终端里问一句。非交互环境（EOF）按拒绝处理，绝不默认同意。"""

    def ask(self, tool: str, args: dict[str, Any], reason: str) -> bool:
        preview = str(args.get("command") or args.get("path") or args)[:120]
        try:
            answer = input(f"  ⚠ 需要确认：{reason}\n    工具 {tool} → {preview}\n    允许执行吗？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in {"y", "yes"}
