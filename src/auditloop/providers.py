"""模型平台预设表。

设计动机：市面上大多数平台都实现了 OpenAI 兼容协议，差异只在
「base_url、默认模型、参数名、认证头」这几处。把它们收进一张表，
换平台就变成改一个环境变量，而不是改代码。

    export AUDITLOOP_PROVIDER=deepseek
    export AUDITLOOP_API_KEY=sk-xxx
    auditloop run "..." --verify "pytest"

**这些端点是资料，不是证据**：手头没有各家的 key，无法逐家实测。
P3 的 `auditloop doctor --probe` 就是用来把「未实测」变成「实测」的。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    base_url: str
    default_model: str
    protocol: str = "openai"          # openai | anthropic | gemini
    max_tokens_field: str = "max_tokens"
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"      # 空字符串表示原样填 api key
    notes: str = ""

    def headers(self, api_key: str) -> dict[str, str]:
        value = f"{self.auth_scheme} {api_key}".strip() if self.auth_scheme else api_key
        return {"Content-Type": "application/json", self.auth_header: value}


def _openai_compatible() -> list[Provider]:
    return [
        Provider("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat",
                 notes="价格表已内置；reasoner 的工具调用能力待实测"),
        Provider("openai", "OpenAI", "https://api.openai.com/v1", "gpt-4o-mini",
                 max_tokens_field="max_completion_tokens",
                 notes="新模型用 max_completion_tokens；老端点用 max_tokens"),
        Provider("qwen", "通义千问（兼容模式）", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-max",
                 notes="兼容模式与原生模式端点不同，别混"),
        Provider("moonshot", "Kimi（Moonshot）", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
        Provider("zhipu", "智谱 GLM", "https://open.bigmodel.cn/api/paas/v4", "glm-4-plus",
                 notes="路径是 /v4，不是 /v1"),
        Provider("doubao", "豆包（火山 Ark）", "https://ark.cn-beijing.volces.com/api/v3", "",
                 notes="模型名要填「推理接入点 ID」，不是模型族名"),
        Provider("siliconflow", "SiliconFlow", "https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-72B-Instruct",
                 notes="同平台不同模型的工具调用支持不一致"),
        Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini",
                 notes="model 可带 provider/ 前缀"),
        Provider("groq", "Groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile",
                 notes="延迟极低，适合做流式演示"),
        Provider("mistral", "Mistral", "https://api.mistral.ai/v1", "mistral-large-latest"),
        Provider("gemini", "Gemini（OpenAI 兼容层）", "https://generativelanguage.googleapis.com/v1beta/openai/",
                 "gemini-2.0-flash", notes="Google 提供的兼容层，能力可能少于原生协议"),
        Provider("ollama", "Ollama（本地）", "http://localhost:11434/v1", "qwen2.5-coder",
                 notes="小模型的 tool_calls 经常格式错"),
        Provider("vllm", "vLLM（自部署）", "http://localhost:8000/v1", "",
                 notes="是否支持 tools 取决于启动参数"),
        Provider("lmstudio", "LM Studio（本地）", "http://localhost:1234/v1", "",
                 notes="需要在界面里开启工具调用"),
    ]


PRESETS: dict[str, Provider] = {p.key: p for p in _openai_compatible()}

# 原生协议（不是 OpenAI 兼容），P3 只登记，尚未实现适配器
NATIVE = {
    "anthropic": "https://api.anthropic.com/v1/messages",
    "gemini-native": "https://generativelanguage.googleapis.com/v1beta",
}


def get(key: str) -> Provider:
    key = (key or "").strip().lower()
    if key not in PRESETS:
        raise KeyError(f"未知平台 {key!r}，可用：{', '.join(sorted(PRESETS))}")
    return PRESETS[key]


def resolve(
    *,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> Provider:
    """把「平台名 / 裸 URL / 什么都不给」统一成一个 Provider。

    优先级：显式 base_url > 平台名 > 环境变量 AUDITLOOP_PROVIDER > 报错。
    裸 URL（不以预设名出现）会被包装成一个临时 Provider，模型名必须有。
    """
    name = (provider or os.environ.get("AUDITLOOP_PROVIDER") or "").strip().lower()
    url = (base_url or "").strip()

    if url:
        template = PRESETS.get(name) or Provider(key="custom", label="custom", base_url=url, default_model="")
        return replace(template, key=name or "custom", label=name or "custom", base_url=url,
                       default_model=model or template.default_model)

    if not name:
        raise KeyError(
            "没有指定平台：设置 AUDITLOOP_PROVIDER（如 deepseek），"
            "或直接给 AUDITLOOP_BASE_URL"
        )
    preset = get(name)
    return replace(preset, default_model=model or preset.default_model)


def describe(provider: Provider) -> dict[str, str]:
    return {
        "key": provider.key,
        "label": provider.label,
        "base_url": provider.base_url,
        "default_model": provider.default_model or "（需指定）",
        "protocol": provider.protocol,
        "max_tokens_field": provider.max_tokens_field,
        "auth": f"{provider.auth_header}: {provider.auth_scheme} <key>" if provider.auth_scheme
                else f"{provider.auth_header}: <key>",
        "notes": provider.notes or "—",
    }
