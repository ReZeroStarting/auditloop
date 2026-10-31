"""策略层：决定「这件事允不允许做」。

三层判断，顺序不能反：
1. 硬拒绝（deny）：命中破坏性命令清单 / 路径越界 —— 谁批都不行；
2. 只读放行：ls、cat、git status 这类不改状态的，任何模式都放；
3. 需要审批：会改状态的，交给 mode 决定 —— never 拒绝 / ask 问人 / auto 放行。

为什么把「硬拒绝」和「需要审批」分开：
审批是人的判断，会疲劳；rm -rf 这种东西不该出现在人的审批队列里，
它根本不该被问。把「不该问的」和「该问的」混在一个队列，是审批失效的开始。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any

# 只读命令：任何策略模式下都直接放行。
READ_ONLY_COMMANDS = {
    "ls", "dir", "cat", "type", "head", "tail", "pwd", "echo", "find",
    "rg", "grep", "wc", "sort", "uniq", "which", "where", "tree", "stat",
    "git", "python", "python3", "py", "pytest", "node", "uv",
}

# git 的只读子命令；python/pytest 的只读形式单独判断。
GIT_READ_ONLY = {"status", "diff", "log", "show", "branch", "rev-parse", "ls-files", "remote", "describe", "blame"}
VERSION_FLAGS = {"--version", "-v", "-V", "--help", "-h"}

# 硬拒绝：不需要问人，直接不执行。
DENY_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*\s+)*-?[a-zA-Z]*[rf]",
    r"\bdel\s+/([fqs]|\s)*",
    r"\brmdir\s+/s",
    r"\bformat\s+[a-zA-Z]:",
    r"\bmkfs\.",
    r"\bdd\s+if=",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bchmod\s+777\s+/",
    r"\b(curl|wget)\b[^|]*\|\s*(ba)?sh",
    r":\(\)\s*\{.*\}\s*;",  # fork bomb
]
_DENY_RE = [re.compile(p, re.IGNORECASE) for p in DENY_PATTERNS]

READ_ONLY_TOOLS = {"fs_read", "fs_list", "glob", "grep", "todo_write"}


@dataclass
class Decision:
    allowed: bool
    needs_approval: bool = False
    reason: str = ""

    @property
    def denied(self) -> bool:
        return not self.allowed and not self.needs_approval


@dataclass
class Policy:
    mode: str = "ask"  # ask | never | auto
    allow_write: bool = True
    allow_shell: bool = True
    extra_deny: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.mode not in {"ask", "never", "auto"}:
            raise ValueError(f"mode 只能是 ask/never/auto，收到 {self.mode!r}")

    # ---------- 对外唯一入口 ----------
    def check(self, tool_name: str, mutating: bool, args: dict[str, Any]) -> Decision:
        if tool_name == "shell":
            if not self.allow_shell:
                return Decision(False, reason="策略禁止执行命令（allow_shell=false）")
            return self._check_command(str(args.get("command") or ""))

        if tool_name in READ_ONLY_TOOLS or not mutating:
            return Decision(True)

        if tool_name.startswith("fs_") and not self.allow_write:
            return Decision(False, reason="策略禁止写文件（allow_write=false）")

        if self.mode == "never":
            return Decision(False, reason=f"policy=never：不允许改动状态的操作（{tool_name}）")
        if self.mode == "auto":
            return Decision(True)
        return Decision(True, needs_approval=True, reason=f"policy=ask：{tool_name} 会改动状态，需要确认")

    # ---------- 命令分类 ----------
    def _check_command(self, command: str) -> Decision:
        stripped = command.strip()
        if not stripped:
            return Decision(False, reason="命令为空")

        for pattern in _DENY_RE:
            if pattern.search(stripped):
                return Decision(False, reason=f"命中硬拒绝规则：{pattern.pattern}")
        for extra in self.extra_deny:
            if extra and extra.lower() in stripped.lower():
                return Decision(False, reason=f"命中自定义拒绝词：{extra}")

        kind = classify_command(stripped)
        if kind == "forbidden":
            return Decision(False, reason="命令不在允许清单内，且看起来会改动系统状态")
        if kind == "read_only":
            return Decision(True)

        if self.mode == "never":
            return Decision(False, reason="policy=never：只允许只读命令")
        if self.mode == "auto":
            return Decision(True)
        return Decision(True, needs_approval=True, reason=f"policy=ask：命令会改动状态，需要确认（{stripped[:60]}）")


def classify_command(command: str) -> str:
    """返回 read_only / mutating / forbidden。"""
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return "forbidden"
    head = tokens[0].strip('"').lower()
    rest = [t.strip('"') for t in tokens[1:]]

    if head not in READ_ONLY_COMMANDS:
        return "forbidden"

    if head in {"git"}:
        sub = next((t for t in rest if not t.startswith("-")), "")
        return "read_only" if sub in GIT_READ_ONLY else "mutating"

    if head in {"python", "python3", "py", "pytest", "node", "uv"}:
        # 只要不是纯 --version / --help，就当会执行代码（可能改状态）
        return "read_only" if rest and rest[0] in VERSION_FLAGS else "mutating"

    return "read_only"
