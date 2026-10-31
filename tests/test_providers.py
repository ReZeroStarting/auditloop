"""Provider 预设与能力探测。"""

from __future__ import annotations

import pytest

from auditloop.llm.openai_compat import OpenAICompatLLM
from auditloop.probe import probe_provider, verdict
from auditloop.providers import PRESETS, describe, get, resolve


def test_preset_table_covers_major_platforms() -> None:
    assert len(PRESETS) >= 12
    for key in ("deepseek", "openai", "qwen", "zhipu", "ollama", "groq", "gemini"):
        assert key in PRESETS
    assert all(p.base_url.startswith("http") for p in PRESETS.values())


def test_unknown_platform_is_a_clear_error() -> None:
    with pytest.raises(KeyError, match="未知平台"):
        get("no-such-platform")


def test_resolve_by_name(monkeypatch) -> None:
    monkeypatch.delenv("AUDITLOOP_PROVIDER", raising=False)
    provider = resolve(provider="deepseek")
    assert provider.base_url == "https://api.deepseek.com/v1"
    assert provider.default_model == "deepseek-chat"


def test_resolve_by_env(monkeypatch) -> None:
    monkeypatch.setenv("AUDITLOOP_PROVIDER", "ollama")
    provider = resolve()
    assert provider.key == "ollama"
    assert provider.base_url.startswith("http://localhost")


def test_bare_url_becomes_custom_provider(monkeypatch) -> None:
    monkeypatch.delenv("AUDITLOOP_PROVIDER", raising=False)
    provider = resolve(base_url="https://my-gateway.example/v1", model="my-model")
    assert provider.base_url == "https://my-gateway.example/v1"
    assert provider.default_model == "my-model"


def test_resolve_without_anything_says_what_to_set(monkeypatch) -> None:
    monkeypatch.delenv("AUDITLOOP_PROVIDER", raising=False)
    with pytest.raises(KeyError, match="AUDITLOOP_PROVIDER"):
        resolve()


def test_zhipu_uses_its_own_path() -> None:
    assert PRESETS["zhipu"].base_url.endswith("/api/paas/v4")


def test_headers_respect_auth_scheme() -> None:
    headers = PRESETS["deepseek"].headers("sk-1")
    assert headers["Authorization"] == "Bearer sk-1"
    assert describe(PRESETS["deepseek"])["label"] == "DeepSeek"


def test_probe_detects_tool_support(fake_upstream) -> None:
    server = fake_upstream(json_payload={
        "choices": [{
            "message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "noop", "arguments": '{"x":"ping"}'}}
            ]},
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    })
    report = probe_provider(OpenAICompatLLM(server.url, "k", "m"))
    ok, message = verdict(report)
    assert ok is True
    assert report["tool_calls"] is True
    assert "工具调用正常" in message


def test_probe_plain_text_means_not_agent_ready(fake_upstream) -> None:
    server = fake_upstream(json_payload={
        "choices": [{"message": {"role": "assistant", "content": "我不会调用工具"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    })
    report = probe_provider(OpenAICompatLLM(server.url, "k", "m"))
    ok, message = verdict(report)
    assert ok is False
    assert "不支持工具调用" in message


def test_probe_reports_connection_failure() -> None:
    llm = OpenAICompatLLM("http://127.0.0.1:1/v1", "k", "m", retries=0, timeout_s=2)
    report = probe_provider(llm)
    ok, message = verdict(report)
    assert ok is False
    assert "连不上" in message
