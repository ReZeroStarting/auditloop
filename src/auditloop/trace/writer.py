"""账本写入：一个 run 一个 JSONL 文件，追加写，每写一条就 flush。

为什么是追加写 + flush：
agent 崩掉的时候，你最想知道的恰恰是「它崩之前最后做了什么」。
缓冲在内存里、进程一死就全丢，那账本就白记了。
代价是每条都落盘，慢一点——但相对一次模型调用（秒级）可以忽略。

文件命名：`<runs>/<run_id>.jsonl`。run_id 带时间前缀，所以 `ls` 就是按时间排序。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .events import Event, new_run_id, now_iso


class TraceWriter:
    def __init__(self, runs_dir: Path, run_id: str | None = None):
        self.run_id = run_id or new_run_id()
        self.path = Path(runs_dir) / f"{self.run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._fh = self.path.open("w", encoding="utf-8", newline="\n")

    def emit(
        self,
        type: str,
        payload: dict[str, Any] | None = None,
        *,
        span: str = "root",
        parent: str | None = None,
    ) -> Event:
        self._seq += 1
        event = Event(
            seq=self._seq,
            ts=now_iso(),
            run_id=self.run_id,
            type=type,
            span=span,
            parent=parent,
            payload=payload or {},
        )
        self._fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        self._fh.flush()
        return event

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self) -> "TraceWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_events(path: str | Path) -> list[Event]:
    """读账本。**坏行直接跳过，不抛异常。**

    理由很实际：进程被 kill 时，最后一行经常是半截 JSON。
    读账本的人要的是「它崩之前做了什么」，而不是一条 JSONDecodeError。
    账本的价值在残缺时最高，所以读侧必须比写侧更宽容。
    """
    events: list[Event] = []
    with Path(path).open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(Event.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    return events
