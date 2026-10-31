"""OpenAI 兼容上游（DeepSeek / OpenAI / 通义 / Kimi / GLM / Ollama / vLLM …）。

只用标准库 urllib：少一个依赖，就少一条「装不上」的理由。
代价是重试、超时、SSE 分帧、工具调用增量拼装都得自己写。

四个值得讲的细节：

1. **429 / 5xx 才重试**。重试一个「参数错了」只会浪费钱，而且会把一个明确错误变成一个慢错误。
2. **流式只影响渲染**。`chat()` 仍然返回一条完整消息，`on_delta` 只是把增量顺手交给 UI。
   决策层（循环、账本、回放）完全不知道有没有流式——这是刻意的边界。
3. **工具调用是增量拼装的**。流式下 `arguments` 是碎片的，`id` 和 `name` 可能只在第一块出现。
   拼错这里，模型会「看起来调了工具，但参数是半截 JSON」。
4. **缓存命中字段两家叫法不同**（`prompt_cache_hit_tokens` / `prompt_tokens_details.cached_tokens`），
   统一收进 `cache_read_tokens`，账本才可比。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator

from ..core.context import estimate_tokens, messages_tokens
from ..core.messages import Message
from ..errors import LLMError
from ..providers import Provider
from .base import LLMResponse, Usage

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class OpenAICompatLLM:
    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        temperature: float = 0.0,
        max_output_tokens: int = 4096,
        timeout_s: int = 120,
        retries: int = 2,
        provider: Provider | None = None,
        stream: bool = False,
        on_delta: Callable[[str], None] | None = None,
    ):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.timeout_s = timeout_s
        self.retries = retries
        self.provider = provider
        self.stream = stream
        self.on_delta = on_delta

    # ---------------- 请求 ----------------
    def _headers(self) -> dict[str, str]:
        if self.provider:
            return self.provider.headers(self.api_key)
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

    def _payload(self, messages: list[Message], tools: list[dict[str, Any]]) -> bytes:
        field = self.provider.max_tokens_field if self.provider else "max_tokens"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            field: self.max_output_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if self.stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        payload = self._payload(messages, tools)
        last: LLMError | None = None
        emitted = False

        for attempt in range(self.retries + 1):
            started = time.perf_counter()
            try:
                req = urllib.request.Request(self.url, data=payload, headers=self._headers(), method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    if self.stream:
                        message, usage, finish, emitted = self._consume_stream(resp, messages, emitted)
                    else:
                        message, usage, finish = self._consume_json(resp)
                return LLMResponse(
                    message=message,
                    usage=usage,
                    model=self.model,
                    finish_reason=finish,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            except urllib.error.HTTPError as exc:
                body = exc.read()[:500].decode("utf-8", "replace")
                last = LLMError(f"HTTP {exc.code}: {body}", retryable=exc.code in RETRYABLE_STATUS, status=exc.code)
            except urllib.error.URLError as exc:
                last = LLMError(f"网络错误：{exc.reason}", retryable=True)
            except (TimeoutError, OSError) as exc:
                last = LLMError(f"连接失败：{exc}", retryable=True)
            except json.JSONDecodeError as exc:
                last = LLMError(f"上游返回了非 JSON：{exc}", retryable=False)
            if last is None or not last.retryable or attempt == self.retries or emitted:
                raise last  # type: ignore[misc]
            time.sleep(1.5 * (attempt + 1))
        raise last or LLMError("未知失败")

    # ---------------- 非流式 ----------------
    def _consume_json(self, resp: Any) -> tuple[Message, Usage, str]:
        data = json.loads(resp.read().decode("utf-8"))
        try:
            choice = data["choices"][0]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"上游返回里没有 choices：{str(data)[:300]}") from exc
        raw = choice.get("message") or {}
        message: Message = {"role": "assistant", "content": raw.get("content")}
        if raw.get("tool_calls"):
            message["tool_calls"] = raw["tool_calls"]
        return message, _usage_from(data.get("usage") or {}), choice.get("finish_reason") or "stop"

    # ---------------- 流式 ----------------
    def _consume_stream(
        self,
        resp: Any,
        messages: list[Message],
        emitted: bool,
    ) -> tuple[Message, Usage, str, bool]:
        parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage_raw: dict[str, Any] = {}
        finish = "stop"

        for chunk in _iter_sse(resp):
            if chunk.get("usage"):
                usage_raw = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    parts.append(text)
                    emitted = True
                    if self.on_delta:
                        self.on_delta(text)
                for fragment in delta.get("tool_calls") or []:
                    index = int(fragment.get("index", 0))
                    slot = calls.setdefault(
                        index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                    )
                    if fragment.get("id"):
                        slot["id"] = fragment["id"]
                    fn = fragment.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]

        content = "".join(parts)
        message: Message = {"role": "assistant", "content": content or None}
        if calls:
            message["tool_calls"] = [calls[i] for i in sorted(calls)]
        usage = _usage_from(usage_raw)
        if not usage_raw:  # 有些本地服务端不发 usage，只能估算——但必须在账本里承认这是估算
            usage = Usage(
                input_tokens=messages_tokens(messages),
                output_tokens=estimate_tokens(content),
                raw={"estimated": True},
            )
        return message, usage, finish, emitted


def _iter_sse(resp: Any) -> Iterator[dict[str, Any]]:
    """逐行读 SSE。注释行、空行、非 data 行全部跳过。"""
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue  # 半截块不值得让整个运行失败


def _usage_from(raw: dict[str, Any]) -> Usage:
    cached = raw.get("prompt_cache_hit_tokens")
    if cached is None:
        cached = (raw.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return Usage(
        input_tokens=int(raw.get("prompt_tokens") or 0),
        output_tokens=int(raw.get("completion_tokens") or 0),
        cache_read_tokens=int(cached or 0),
        raw=raw,
    )
