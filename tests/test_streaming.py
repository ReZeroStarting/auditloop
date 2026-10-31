"""流式解析测试：用真的 HTTP 服务端吐 SSE，不用 mock 对象。

这一层最容易出「看起来能跑」的 bug：
content 拼对了、但 tool_calls 的 arguments 拼成半截 JSON，模型下一轮就会收到坏参数。
"""

from __future__ import annotations

import json

import pytest

from auditloop.errors import LLMError
from auditloop.llm.openai_compat import OpenAICompatLLM
from auditloop.providers import PRESETS


def _content(chunks, text: str) -> list[dict]:
    chunks.append({"choices": [{"delta": {"content": text}}]})
    return chunks


def test_stream_assembles_content_and_tool_calls(fake_upstream) -> None:
    chunks = [
        {"choices": [{"delta": {"content": "你"}}]},
        {"choices": [{"delta": {"content": "好"}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "fs_read", "arguments": '{"pa'}}
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'th":"a.py"}'}}
        ]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"usage": {"prompt_tokens": 12, "completion_tokens": 7, "prompt_cache_hit_tokens": 3}, "choices": []},
    ]
    server = fake_upstream(chunks=chunks)
    deltas: list[str] = []

    llm = OpenAICompatLLM(server.url, "k", "m", stream=True, on_delta=deltas.append)
    response = llm.chat([{"role": "user", "content": "hi"}], [])

    assert response.message["content"] == "你好"
    call = response.message["tool_calls"][0]
    assert call["function"]["name"] == "fs_read"
    assert json.loads(call["function"]["arguments"]) == {"path": "a.py"}, "参数碎片必须拼成合法 JSON"
    assert response.usage.input_tokens == 12
    assert response.usage.cache_read_tokens == 3, "缓存命中要能被账本看见"
    assert "".join(deltas) == "你好"
    assert response.finish_reason == "tool_calls"
    assert server.last_body["stream"] is True


def test_stream_without_usage_estimates_and_says_so(fake_upstream) -> None:
    server = fake_upstream(chunks=_content([], "一段没有 usage 的回复"))
    response = OpenAICompatLLM(server.url, "k", "m", stream=True).chat([{"role": "user", "content": "hi"}], [])
    assert response.usage.output_tokens > 0
    assert response.usage.raw.get("estimated") is True, "估算了就要在账本里承认是估算"


def test_non_stream_still_works(fake_upstream) -> None:
    server = fake_upstream(json_payload={
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    })
    response = OpenAICompatLLM(server.url, "k", "m").chat([{"role": "user", "content": "hi"}], [])
    assert response.message["content"] == "ok"
    assert response.usage.input_tokens == 5


def test_provider_switch_changes_token_field(fake_upstream) -> None:
    server = fake_upstream(json_payload={"choices": [{"message": {"content": "ok"}}], "usage": {}})
    OpenAICompatLLM(server.url, "k", "m", provider=PRESETS["openai"]).chat([{"role": "user", "content": "hi"}], [])
    assert "max_completion_tokens" in server.last_body, "OpenAI 新模型不认 max_tokens"
    assert "max_tokens" not in server.last_body


def test_default_provider_uses_max_tokens(fake_upstream) -> None:
    server = fake_upstream(json_payload={"choices": [{"message": {"content": "ok"}}], "usage": {}})
    OpenAICompatLLM(server.url, "k", "m", provider=PRESETS["deepseek"]).chat([{"role": "user", "content": "hi"}], [])
    assert server.last_body["max_tokens"] == 4096


def test_http_400_is_not_retried(fake_upstream) -> None:
    """4xx 重试只会浪费钱，还会把明确错误变成慢错误。"""
    server = fake_upstream(status_code=400)
    llm = OpenAICompatLLM(server.url, "k", "m", retries=2)
    with pytest.raises(LLMError) as excinfo:
        llm.chat([{"role": "user", "content": "hi"}], [])
    assert excinfo.value.status == 400
    assert server.request_count == 1, "参数错误不该重试"


def test_bad_sse_chunk_is_skipped(fake_upstream) -> None:
    chunks = [{"choices": [{"delta": {"content": "a"}}]}, {"choices": [{"delta": {"content": "b"}}]}]
    server = fake_upstream(chunks=chunks)
    llm = OpenAICompatLLM(server.url, "k", "m", stream=True)
    response = llm.chat([{"role": "user", "content": "hi"}], [])
    assert response.message["content"] == "ab"


def test_cli_stream_flag_wires_through(fake_upstream, monkeypatch, capsys, tmp_path) -> None:
    """端到端：环境变量 → 平台解析 → 流式回调 → 终端输出。"""
    from auditloop.cli import main as cli_main

    server = fake_upstream(chunks=[{"choices": [{"delta": {"content": "完"}}]},
                                   {"choices": [{"delta": {"content": "成"}}]}])
    monkeypatch.setenv("AUDITLOOP_BASE_URL", server.url)
    monkeypatch.setenv("AUDITLOOP_API_KEY", "test-key")
    monkeypatch.setenv("AUDITLOOP_MODEL", "test-model")
    monkeypatch.chdir(tmp_path)

    code = cli_main(["run", "做点小事", "--stream", "--yes"])
    output = capsys.readouterr().out
    assert code == 0
    assert "流式" in output
    assert "完成" in output
