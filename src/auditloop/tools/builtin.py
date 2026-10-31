"""默认工具集。

面试点：工具集是**攻击面**。每加一个工具，就多一条让模型搞坏东西的路。
所以默认集只放「改代码需要的最小集合」，网络、数据库、浏览器这些都要显式打开，
而不是默认给。这也是 Claude Code 一类产品的默认姿势。
"""

from __future__ import annotations

from .fs import EditFile, Glob, Grep, ListDir, ReadFile, WriteFile
from .registry import Registry
from .shell import Shell
from .task import SpawnTask
from .todo import TodoWrite

READ_ONLY = [ReadFile(), ListDir(), Glob(), Grep(), TodoWrite()]


def default_registry(include_mutating: bool = True, include_subagent: bool = True) -> Registry:
    tools = list(READ_ONLY)
    if include_mutating:
        tools += [EditFile(), WriteFile(), Shell()]
    if include_subagent:
        tools.append(SpawnTask())
    return Registry(tools)
