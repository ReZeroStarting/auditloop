"""账本事件模型。

一条事件 = 发生了什么 + 发生在哪个 span（哪次调用里）+ 什么时候。
字段刻意少：seq / ts / run_id / type / span / parent / payload。

为什么不做成强类型的一堆子类：
账本是**给人和给机器同时看的**。人要看 `auditloop trace show`，
机器要做统计和回放。一个扁平的 payload 字典，加一份事件类型清单，
比十个类更容易演进——加字段不需要迁移历史账本。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------- 事件类型清单（也是文档） ----------
RUN_CREATED = "run.created"
MODEL_REQUEST = "model.request"
MODEL_RESPONSE = "model.response"
POLICY_DECISION = "policy.decision"
TOOL_CALL = "tool.call"
TOOL_RESULT = "tool.result"
CONTEXT_COMPACTED = "context.compacted"
VERIFICATION = "verification"
RUN_FINISHED = "run.finished"

ALL_TYPES = [
    RUN_CREATED, MODEL_REQUEST, MODEL_RESPONSE, POLICY_DECISION,
    TOOL_CALL, TOOL_RESULT, CONTEXT_COMPACTED, VERIFICATION, RUN_FINISHED,
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


@dataclass
class Event:
    seq: int
    ts: str
    run_id: str
    type: str
    span: str = "root"
    parent: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "run_id": self.run_id,
            "type": self.type,
            "span": self.span,
            "parent": self.parent,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        return cls(
            seq=int(data.get("seq", 0)),
            ts=str(data.get("ts", "")),
            run_id=str(data.get("run_id", "")),
            type=str(data.get("type", "")),
            span=str(data.get("span") or "root"),
            parent=data.get("parent"),
            payload=data.get("payload") or {},
        )
