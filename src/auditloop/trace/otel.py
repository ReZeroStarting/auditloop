"""把账本映射成 OpenTelemetry GenAI 语义约定的 span。

为什么要做这件事（而不是自己发明一套字段）：
可观测性这行的价值在于「能被现有工具接住」。自己定义字段，
最后只能自己写看板；按 OTel 的 gen_ai.* 约定输出，
Grafana / Langfuse / Phoenix 这类后端不用改一行代码就能吃进去。

这里不引入 opentelemetry-sdk（零依赖原则），只产出符合 OTLP 形状的 JSON。
真正要发到后端时，把它交给任意 collector 即可。
"""

from __future__ import annotations

from typing import Any

from . import events as E
from .events import Event


def to_otel_spans(events: list[Event], *, service_name: str = "auditloop") -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    model_name = ""
    for event in events:
        if event.type == E.RUN_CREATED:
            model_name = str(event.payload.get("model") or "")
            spans.append(
                {
                    "name": "agent.run",
                    "span_id": "root",
                    "parent_span_id": None,
                    "start_time": event.ts,
                    "attributes": {
                        "gen_ai.operation.name": "invoke_agent",
                        "gen_ai.agent.name": "auditloop",
                        "gen_ai.request.model": model_name,
                        "service.name": service_name,
                        "auditloop.run_id": event.run_id,
                        "auditloop.policy": event.payload.get("policy"),
                        "auditloop.workspace": event.payload.get("workspace"),
                    },
                }
            )
        elif event.type == E.MODEL_RESPONSE:
            spans.append(
                {
                    "name": f"chat {event.payload.get('model') or model_name}",
                    "span_id": event.span,
                    "parent_span_id": event.parent or "root",
                    "start_time": event.ts,
                    "attributes": {
                        "gen_ai.operation.name": "chat",
                        "gen_ai.system": "openai-compatible",
                        "gen_ai.request.model": event.payload.get("model") or model_name,
                        "gen_ai.response.finish_reasons": [event.payload.get("finish_reason") or "stop"],
                        "gen_ai.usage.input_tokens": event.payload.get("input_tokens", 0),
                        "gen_ai.usage.output_tokens": event.payload.get("output_tokens", 0),
                        "auditloop.latency_ms": event.payload.get("latency_ms", 0),
                    },
                }
            )
        elif event.type == E.TOOL_RESULT:
            spans.append(
                {
                    "name": f"execute_tool {event.payload.get('name')}",
                    "span_id": event.span,
                    "parent_span_id": event.parent or "root",
                    "start_time": event.ts,
                    "attributes": {
                        "gen_ai.operation.name": "execute_tool",
                        "gen_ai.tool.name": event.payload.get("name"),
                        "gen_ai.tool.call.id": event.span,
                        "auditloop.tool.ok": bool(event.payload.get("ok")),
                        "auditloop.tool.duration_ms": event.payload.get("duration_ms", 0),
                        "auditloop.tool.bytes": event.payload.get("bytes", 0),
                    },
                }
            )
    return spans
