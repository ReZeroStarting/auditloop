"""评测任务的数据契约。

一条任务 = 一个初始仓库 + 一句话需求 + 若干条**确定性检查**。

为什么坚持确定性检查而不是 LLM 判分：
LLM 判分适合「回答质量」这种主观维度，但它有个致命问题——**它自己会漂**。
你改了 prompt 想变好，判分也变了，于是你永远不知道是自己变好了还是尺子变松了。
所以默认用确定性检查（文件内容、退出码、账本里有没有那条事件），
主观维度才上 LLM 判分，并且必须固定住 judge 的模型与版本。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Task:
    id: str
    prompt: str
    files: dict[str, str] = field(default_factory=dict)
    script: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    max_steps: int = 8
    verify: str | None = None
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        if not data.get("id"):
            raise ValueError("任务缺少 id")
        if not data.get("prompt"):
            raise ValueError(f"任务 {data.get('id')} 缺少 prompt")
        return cls(
            id=str(data["id"]),
            prompt=str(data["prompt"]),
            files={str(k): str(v) for k, v in (data.get("files") or {}).items()},
            script=list(data.get("script") or []),
            checks=list(data.get("checks") or []),
            max_steps=int(data.get("max_steps", 8)),
            verify=data.get("verify"),
            tags=list(data.get("tags") or []),
        )


def load_tasks(path: str | Path) -> list[Task]:
    tasks: list[Task] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                tasks.append(Task.from_dict(json.loads(line)))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno} 不是合法 JSON：{exc}") from exc
    return tasks
