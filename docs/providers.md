# 模型支持

> 最后核对：2026-10-31。**DeepSeek 已实测**（probe 通过 + live 评测 5/5 + 真仓库端到端）；
> **其余端点仍未实测**（手头只有 DeepSeek 的 key），
> 来源是各家官方文档。P3 阶段会用 `auditloop doctor --probe` 逐家实测并回填「实测」列。
> 看到「未实测」三个字就说明：这段是资料，不是证据。

---

## 一句话结论

市面上**大多数**模型能直接用，因为大多数都实现了 OpenAI 兼容的 `/chat/completions`。
剩下的需要写适配器，不是因为难，是因为协议真的不一样。

| 协议族 | 代表 | 现状 |
|---|---|---|
| OpenAI 兼容 | DeepSeek、OpenAI、通义、Kimi、GLM、豆包、SiliconFlow、Ollama、vLLM、Groq、Mistral | ✅ 已支持，改 `AUDITLOOP_BASE_URL` 即可 |
| Anthropic Messages | Claude 全系 | ⏳ 计划 P3（`/v1/messages`，system 独立、content blocks、tool_use/tool_result） |
| Gemini `generateContent` | Gemini 全系 | ⏳ 计划 P3（另有 OpenAI 兼容端点见下） |
| Azure OpenAI | 企业部署 | ⏳ 需要 `api-version` 参数与 `api-key` 头，和标准兼容协议不同 |
| AWS Bedrock | 企业部署 | ⏳ SigV4 签名，属于另一个量级的适配 |

---

## 兼容协议预设表

用法：只设环境变量，不改代码。

```powershell
$env:AUDITLOOP_BASE_URL="https://api.deepseek.com/v1"
$env:AUDITLOOP_API_KEY="sk-..."
$env:AUDITLOOP_MODEL="deepseek-chat"
auditloop run "..." --verify "..."
```

| 平台 | base_url | 常用模型 | 注意 | 实测 |
|---|---|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat`、`deepseek-reasoner` | 价格表已内置；reasoner 的工具调用能力**待实测** | **已实测**：probe 1172ms、带 tools/usage/cache；live 5/5 |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini`、`gpt-4o` | 新模型用 `max_completion_tokens` 而非 `max_tokens` | 未实测 |
| 通义千问（DashScope 兼容模式） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-max`、`qwen-plus` | 兼容模式与原生模式端点不同，别混 | 未实测 |
| Kimi（Moonshot） | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` 等 | 长上下文模型按 token 计费差异大 | 未实测 |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-plus` | 路径是 `/v4`，不是 `/v1` | 未实测 |
| 豆包（火山 Ark） | `https://ark.cn-beijing.volces.com/api/v3` | 推理接入点 ID | 模型名填的是「接入点」，不是模型族名 | 未实测 |
| SiliconFlow | `https://api.siliconflow.cn/v1` | 多开源模型 | 同一平台不同模型的工具调用支持不一致 | 未实测 |
| OpenRouter | `https://openrouter.ai/api/v1` | 聚合 | 可在 `model` 里带 `provider/` 前缀 | 未实测 |
| Groq | `https://api.groq.com/openai/v1` | Llama 系 | 延迟极低，适合做流式演示 | 未实测 |
| Mistral | `https://api.mistral.ai/v1` | `mistral-large-latest` | 兼容 OpenAI 形状但字段有出入 | 未实测 |
| Ollama | `http://localhost:11434/v1` | `qwen2.5-coder`、`llama3.1` | 小模型的 tool_calls 经常格式错 | 未实测 |
| vLLM / LM Studio | `http://localhost:8000/v1`、`:1234/v1` | 自部署 | 是否支持 tools 取决于启动参数 | 未实测 |
| Gemini（兼容端点） | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-2.x` | Google 提供的 OpenAI 兼容层，能力可能少于原生 | 未实测 |

> 端点与模型名会变，以官方文档为准。本表的用途是省掉「先查一遍」的十分钟，不是替代官方文档。

---

## 参数差异（适配器要处理的部分）

| 差异点 | 表现 | auditloop 现在怎么处理 |
|---|---|---|
| 输出上限字段 | `max_tokens` vs `max_completion_tokens` | 统一发 `max_tokens`；不吃的平台会报 400，需要 per-provider 开关（P3） |
| 认证 | `Authorization: Bearer` vs `api-key` 头 vs query 参数 | 只支持 Bearer（P3 加 header 策略） |
| 系统消息 | 有的要求放 `messages[0]`，有的要求独立 `system` 字段 | 统一放 `messages[0]`；Anthropic 适配器负责转换 |
| 工具调用结果 | OpenAI 是 `role: "tool"` + `tool_call_id`；Anthropic 是 `tool_result` block | 内部用 OpenAI 形状，适配器做双向映射 |
| 并行工具调用 | `parallel_tool_calls` 有的不支持 | 不依赖并行；一次响应里多个调用按顺序执行 |
| 推理字段 | `reasoning_content` / `thinking` blocks | 现在直接忽略（不进入下一轮上下文）——这是有意的，见下 |
| 缓存计费 | `prompt_cache_hit_tokens` / `prompt_tokens_details.cached_tokens` | 两个字段都认，统一进 `cache_read_tokens` |
| temperature 范围 | 有的 0–1，有的 0–2 | 默认 0，配置项原样透传 |

**关于推理字段为什么忽略**：把 reasoning 内容再喂回上下文，既贵又容易让模型自我强化。
需要保留时，应当作为独立字段记在账本里，而不是混进消息流——这是 P4 的事。

---

## 加一家新平台要做什么

1. 若是 OpenAI 兼容：只加一条预设（base_url + 默认模型 + 参数开关），**不用写代码**。
2. 若不是：实现 `LLM` 协议的 `chat(messages, tools) -> LLMResponse`，只此一个方法。
   循环层、策略层、账本层一行都不用改——这是当初把接口收窄的回报。
3. 加一条 **provider 测试**：用 `MockTransport` 喂一段真实响应样本，断言 tool_calls 与 usage 解析正确。
   样本放进 `tests/fixtures/<provider>/`。
4. 跑 `auditloop doctor --probe`，把结果回填到本页「实测」列。

## 能力探测（已实现）

```bash
auditloop doctor --probe
```

对当前配置发一个最小请求，回答三个问题：

1. 这个端点通不通（认证 / 网络 / 模型名）；
2. 支不支持 `tools`（发一个只有一个参数的工具，看返回里有没有 `tool_calls`）；
3. usage 字段里有没有缓存命中（决定账本的成本是否可信）。

**为什么值得做**：大量「agent 不好用」的投诉，最后发现是模型根本不支持可靠的 tool calling。
把这件事在启动阶段用一个命令问清楚，比事后看一堆格式错误强。

## 已知的坑（会持续回填）

- **小模型的 `tool_calls`**：`arguments` 可能是空串、半截 JSON、或者不是 JSON。auditloop 会把它当模型错误回填，让它自己改——但连续错三次就会 `policy_blocked`，这不是 bug，是模型能力问题。
- **本地模型没有 usage**：有些 Ollama 版本不返回 `usage`，成本会记成 0。账本里 `input_tokens` 会明显偏小，别拿它做容量规划。
- **推理模型的延迟**：`deepseek-reasoner` 一类模型首 token 很慢，P3 上流式之前，观感会很差。

## 命令速查

```bash
auditloop providers list            # 14 家预设一览
auditloop providers show deepseek   # 单家细节：端点、默认模型、token 字段、认证头
auditloop doctor --probe            # 真实探测（需要 key）
auditloop run "..." --stream        # 逐字输出，账本与回放不受影响
```

探测的三种结论：

| 结论 | 含义 | 退出码 |
|---|---|---|
| 工具调用正常 | 可以当 agent 用，顺带报出 usage / 缓存字段 | 0 |
| 端点通但只回文本 | **不能当 agent**：模型不支持 function calling，或网关把 `tools` 吞了 | 1 |
| 连不上 | 认证、网络、模型名三者之一有问题 | 1 |

没有配 key 时，`--probe` 会直接说明缺什么，而不是假装探测成功。
