"""把一次运行导出成前端能直接吃的 JSON。

为什么单独做一层，而不是让原型自己去读 JSONL：
1. 账本里的 `payload` 是给机器用的，缺「这一行显示成什么」；
2. 渲染逻辑（`render_event`）已经在 Python 里了，前端再实现一遍必然漂移；
3. 导出是 `trace` 子命令的第一个真实消费者——它逼着我们把「账本能被外部消费」这件事做实。

约定：**导出物只增不改**。前端可以忽略不认识的字段，但不能因为多字段就崩。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import events as E
from .events import Event
from .render import render_event
from .replay import summarize_run


def _meta(events: list[Event]) -> dict[str, Any]:
    created = next((e for e in events if e.type == E.RUN_CREATED), None)
    payload = created.payload if created else {}
    return {
        "run_id": events[0].run_id if events else "",
        "task": payload.get("task", ""),
        "workspace": payload.get("workspace", ""),
        "policy": payload.get("policy", ""),
        "model": payload.get("model", ""),
        "compact_at": payload.get("compact_at", 0),
        "budget": payload.get("budget", {}),
        "started_at": events[0].ts if events else "",
    }


def export_run(events: list[Event], *, source: str | Path | None = None) -> dict[str, Any]:
    """返回 {meta, summary, timeline[], events[]}。timeline 的 line 已本地化。

    放行类策略判定在 CLI 里默认不打印（避免刷屏），但账本查看器需要每一行都看得见，
    所以这里给空行补一句人话——展示层的克制不能变成数据缺失。
    """
    timeline = []
    for event in events:
        line = render_event(event, color=False)
        if not line and event.type == E.POLICY_DECISION:
            payload = event.payload
            tool = payload.get("tool")
            if payload.get("denied"):
                line = f"⊘ 拒绝 {tool}"
            elif payload.get("needs_approval"):
                line = f"⚑ 审批 {tool}：{'通过' if payload.get('approved') else '未通过'}"
            else:
                line = f"✓ 放行 {tool}"
        timeline.append(
            {
                "seq": event.seq,
                "type": event.type,
                "span": event.span,
                "parent": event.parent,
                "ts": event.ts,
                "line": line,
                "payload": event.payload,
            }
        )
    return {
        "meta": _meta(events),
        "summary": summarize_run(events),
        "timeline": timeline,
        "events": [event.to_dict() for event in events],
        "source": str(source) if source else None,
        "schema": 1,
    }
