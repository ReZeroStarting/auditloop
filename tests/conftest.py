from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from auditloop.core.loop import run_loop
from auditloop.llm.mock import ScriptedLLM
from auditloop.policy.approvals import AutoApprove
from auditloop.policy.policy import Policy
from auditloop.tools.builtin import default_registry
from auditloop.trace.writer import TraceWriter, read_events


@pytest.fixture()
def runner(tmp_path):
    """跑一次完整的 run，返回 (结果, 事件, 工作区)。"""

    def _run(
        steps,
        *,
        files=None,
        policy=None,
        mode="auto",
        approver=None,
        max_steps=6,
        verify=None,
        budget=None,
        compact_at=10**9,
        llm=None,
        task="测试任务",
        allow_subagent=False,
    ):
        ws = tmp_path / "ws"
        ws.mkdir(exist_ok=True)
        for name, content in (files or {}).items():
            path = ws / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        trace = TraceWriter(tmp_path / "runs")
        result = run_loop(
            task,
            llm=llm or ScriptedLLM(steps),
            registry=default_registry(),
            policy=policy or Policy(mode=mode),
            approver=approver or AutoApprove(),
            workspace=ws,
            trace=trace,
            max_steps=max_steps,
            verify_cmd=verify,
            budget=budget,
            compact_at=compact_at,
            allow_subagent=allow_subagent,
        )
        events = read_events(trace.path)
        trace.close()
        return result, events, ws

    return _run


# ---------------------------------------------------------------- 假上游
class _FakeHandler(BaseHTTPRequestHandler):
    """一个只有两种回答的假上游：SSE 分块，或一次性 JSON。"""

    def do_POST(self) -> None:  # noqa: N802 - http.server 的约定
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}
        self.server.last_body = body  # type: ignore[attr-defined]
        self.server.last_headers = dict(self.headers)  # type: ignore[attr-defined]
        self.server.request_count = getattr(self.server, "request_count", 0) + 1  # type: ignore[attr-defined]

        status = self.server.status_code  # type: ignore[attr-defined]
        if status != 200:
            payload = json.dumps({"error": {"message": "boom"}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in self.server.chunks:  # type: ignore[attr-defined]
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        payload = self.server.json_payload or {}  # type: ignore[attr-defined]
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # 静音，别把测试输出淹了
        return


@pytest.fixture()
def fake_upstream():
    """起一个本地假上游，返回 server 对象（.url / .last_body 可直接断言）。"""
    servers = []

    def _make(chunks=None, json_payload=None, status_code=200):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHandler)
        server.chunks = chunks or []          # type: ignore[attr-defined]
        server.json_payload = json_payload     # type: ignore[attr-defined]
        server.status_code = status_code       # type: ignore[attr-defined]
        server.request_count = 0               # type: ignore[attr-defined]
        server.last_body = None                # type: ignore[attr-defined]
        server.url = f"http://127.0.0.1:{server.server_address[1]}/v1"  # type: ignore[attr-defined]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield _make
    for server in servers:
        server.shutdown()
        server.server_close()  # 只 shutdown 不 close，监听口还在，下一轮会连上却没人应答
