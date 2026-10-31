"""OpenAI 兼容上游（DeepSeek / OpenAI / 通义 / 任何自建兼容网关）。

只用标准库 urllib：少一个依赖，就少一条「装不上」的理由。
代价是要自己处理重试、超时、错误语义——这正好是本事所在。

三个细节值得讲：
1. 429 和 5xx 才重试，4xx 其余直接失败：重试一个「参数错了」只会浪费钱。
2. usage 里缓存命中字段两家叫法不同（prompt_cache_hit_tokens / prompt_tokens_details.cached_tokens），
   统一收进 cache_read_tokens，账本才可比。
3. 错误信息里带上响应体前 500 字符：线上排查时这一句能省半小时。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from ..core.messages import Message
from ..errors import LLMError
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
    ):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.timeout_s = timeout_s
        self.retries = retries

    def _payload(self, messages: list[Message], tools: list[dict[str, Any]]) -> bytes:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        payload = self._payload(messages, tools)
        last: LLMError | None = None
        for attempt in range(self.retries + 1):
            started = time.perf_counter()
            try:
                req = urllib.request.Request(
                    self.url,
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return self._parse(data, started)
            except urllib.error.HTTPError as exc:
                body = exc.read()[:500].decode("utf-8", "replace")
                last = LLMError(
                    f"HTTP {exc.code}: {body}",
                    retryable=exc.code in RETRYABLE_STATUS,
                    status=exc.code,
                )
            except urllib.error.URLError as exc:
                last = LLMError(f"网络错误：{exc.reason}", retryable=True)
            except (TimeoutError, OSError) as exc:
                last = LLMError(f"连接失败：{exc}", retryable=True)
            if last is None or not last.retryable or attempt == self.retries:
                raise last  # type: ignore[misc]
            time.sleep(1.5 * (attempt + 1))
        raise last or LLMError("未知失败")

    def _parse(self, data: dict[str, Any], started: float) -> LLMResponse:
        try:
            choice = data["choices"][0]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"上游返回里没有 choices：{str(data)[:300]}") from exc
        raw_msg = choice.get("message") or {}
        message: Message = {"role": "assistant", "content": raw_msg.get("content")}
        if raw_msg.get("tool_calls"):
            message["tool_calls"] = raw_msg["tool_calls"]
        usage_raw = data.get("usage") or {}
        cached = usage_raw.get("prompt_cache_hit_tokens")
        if cached is None:
            cached = (usage_raw.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        usage = Usage(
            input_tokens=int(usage_raw.get("prompt_tokens") or 0),
            output_tokens=int(usage_raw.get("completion_tokens") or 0),
            cache_read_tokens=int(cached or 0),
            raw=usage_raw,
        )
        return LLMResponse(
            message=message,
            usage=usage,
            model=data.get("model") or self.model,
            finish_reason=choice.get("finish_reason") or "stop",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
