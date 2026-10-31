# API 参考

> 这里只写**公开 API**。以 `_` 开头的一律视为内部实现，升级不保证兼容。
> 所有示例都可以直接跑；带 `✅ verified` 的输出是我实际执行过的。

---

## 0. 三条使用路径

| 你想做的事 | 用哪个 |
|---|---|
| 跑一个任务 | CLI：`auditloop run "..."` |
| 把 agent 嵌进自己的程序 | Python：`run_loop(...)` |
| 换模型 / 加模型 | 实现 `LLM` 协议，或只改环境变量 |

---

## 1. 核心：`run_loop`

```python
from auditloop.core.loop import run_loop

result = run_loop(
    task: str,
    *,
    llm,                              # 任何实现了 chat() 的对象
    registry: Registry,               # 工具集
    policy: Policy,                   # 策略
    workspace: Path,                  # agent 唯一能碰的目录
    trace: TraceWriter,               # 账本写入器
    approver=None,                    # 需要审批时问谁；None = 一律拒绝
    budget: Budget | None = None,     # 默认 24 步 / 400k 输入 / $1
    model_cfg: ModelConfig | None = None,
    max_steps: int | None = None,
    compact_at: int = 12_000,         # 上下文压缩水位（估算 token）
    keep_recent: int = 8,             # 压缩时保留最近多少条
    tool_chars: int = 8_000,          # 单条工具输出进上下文前的上限
    verify_cmd: str | None = None,    # 收尾验证命令；rc!=0 → unverified
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    on_event=None,                    # 实时渲染回调：fn(Event) -> None
    spawn=None,                       # 子 agent 入口（P4）
) -> RunResult
```

### `RunResult`

| 字段 | 类型 | 含义 |
|---|---|---|
| `run_id` | `str` | 与账本文件同名 |
| `status` | `str` | `ok` / `unverified` / `policy_blocked` / `budget_exceeded` / `max_steps` / `model_error` |
| `answer` | `str` | 模型的最终文本（`unverified` 时会附加说明） |
| `steps` | `int` | 模型调用轮数 |
| `usage` | `Usage` | `input_tokens` / `output_tokens` / `cache_read_tokens` |
| `cost_usd` | `float` | 按**实际返回的模型名**计价；mock/replay 为 0 |
| `trace_path` | `Path` | 账本文件 |
| `verification` | `dict \| None` | `{command, rc, output, duration_ms}` |
| `denials` | `int` | 被策略拒绝的次数 |
| `tool_calls` | `int` | 工具调用次数 |

`result.ok` 等价于 `status == "ok"`；`result.as_dict()` 给出可 JSON 化的版本。

### 已验证的最小嵌入

```python
from pathlib import Path
from auditloop.core.loop import run_loop
from auditloop.llm.mock import ScriptedLLM
from auditloop.policy.approvals import AutoApprove
from auditloop.policy.policy import Policy
from auditloop.tools.base import Tool, ToolContext
from auditloop.tools.registry import Registry
from auditloop.trace.writer import TraceWriter

class WordCount(Tool):
    name = "word_count"
    description = "数一个文本文件有多少行。"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    def run(self, args, ctx: ToolContext) -> str:
        return f"{args['path']}: {len(ctx.resolve(args['path']).read_text(encoding='utf-8').splitlines())} 行"

with TraceWriter(Path("/tmp/runs")) as trace:
    result = run_loop(
        "notes.txt 有多少行？",
        llm=ScriptedLLM([{"tool": "word_count", "args": {"path": "notes.txt"}},
                         {"content": "notes.txt 有 3 行。"}]),
        registry=Registry([WordCount()]),
        policy=Policy(mode="auto"),
        approver=AutoApprove(),
        workspace=Path("/tmp/ws"),
        trace=trace,
    )
```

完整可运行版本：`examples/embed_custom_tool.py`。

```text
status : ok
answer : notes.txt 有 3 行。
steps  : 2 · tool calls: 1
``` ✅ verified（`python examples/embed_custom_tool.py`，2026-10-31）

---

## 2. 模型层

```python
class LLM(Protocol):
    name: str
    def chat(self, messages: list[Message], tools: list[dict]) -> LLMResponse: ...
```

| 实现 | 用途 |
|---|---|
| `OpenAICompatLLM(base_url, api_key, model, *, temperature=0.0, max_output_tokens=4096, timeout_s=120, retries=2)` | 真实上游 |
| `ScriptedLLM(steps, model="mock", final=None)` | 确定性演示 / 评测：step 是 `{"tool": 名, "args": {...}}` 或 `{"content": "..."}` |
| `HeuristicMockLLM()` | 没配 key 时的兜底 |
| `ReplayLLM(responses)` | 账本回放，`responses` 来自 `recorded_llm_responses(events)` |
| `FallbackLLM(llms, on_fallback=None)` | 多上游降级链 |

`LLMResponse(message, usage, model, finish_reason, latency_ms)`；`Usage.add()` 累加。

**注意**：`message` 必须是 OpenAI 形状的 dict（`role` / `content` / `tool_calls`）。
自定义实现只要保证这一点，循环层完全不需要知道你是哪家。

---

## 3. 工具层

```python
class Tool:
    name: str
    description: str
    parameters: dict          # JSON Schema（只用到 type / required / properties / enum）
    mutating: bool = False    # True 表示会改磁盘状态，必须过策略闸门
    def run(self, args: dict, ctx: ToolContext) -> str: ...
```

- 抛 `ToolError` / `ToolArgError` / `PermissionError` → 被转成 `ERROR: ...` 回填给模型，**不会中断 run**。
- 抛其他异常 → 也被捕获，记成「工具内部异常」，同样不中断。
- 返回值直接进上下文（会被 `context.fit` 截断）。

`ToolContext(workspace, policy=None, todos=[], extra={})`，其中 `ctx.resolve(raw)` 会把路径解析到工作区内，越界抛 `PermissionError`。

`Registry(tools)`：`add()` / `get()` / `names()` / `specs()` / `dispatch(name, args, ctx)`。
`dispatch` 会先做 Schema 校验：缺必填、类型不符、**未知参数一律拒绝**（模型臆造参数比传错更危险）。

---

## 4. 策略层

```python
policy = Policy(mode="ask", allow_write=True, allow_shell=True, extra_deny=[])
decision = policy.check(tool_name, mutating, args)
# decision.allowed / decision.needs_approval / decision.denied / decision.reason
```

| mode | 只读工具 | 改状态工具 | 破坏性命令 |
|---|---|---|---|
| `never` | 放行 | 拒绝 | 拒绝 |
| `ask` | 放行 | 要审批 | 拒绝 |
| `auto` | 放行 | 放行 | 拒绝 |

审批者只要实现 `ask(tool, args, reason) -> bool`：
`AutoApprove` / `RejectAll` / `ConsoleApprover`。

**默认 `approver=None` 时一律拒绝**——问不到人就不做，这是刻意的默认值。

---

## 5. 账本层

```python
with TraceWriter(runs_dir, run_id=None) as trace:
    trace.emit("tool.call", {"name": "grep", "args": {...}}, span="m1.t1", parent="m1")

events = read_events(trace.path)
print(render(events))                       # 人类可读
summary = summarize_run(events)             # 统计：状态/步数/工具分布/拒绝次数
spans = to_otel_spans(events)               # OpenTelemetry gen_ai.* 形状
```

- `Event`：`seq` / `ts` / `run_id` / `type` / `span` / `parent` / `payload`。
- `read_events` **跳过坏行**：进程被杀时的半截 JSON 不会让读取失败。
- `render(events, full=False, color=True)`；`printer()` 给 CLI 用的实时回调（吞掉空行）。

---

## 6. 评测层

```python
from auditloop.eval.schema import load_tasks
from auditloop.eval.runner import run_tasks

report = run_tasks(load_tasks("tasks/eval.jsonl"), mode="mock", traces_dir=Path("runs"))
print(report.pass_rate, report.passed, report.total)
```

| 检查类型 | 参数 | 判什么 |
|---|---|---|
| `contains` / `not_contains` | `text` | 最终回答 |
| `status_is` | `status` | 运行状态 |
| `file_exists` / `file_contains` / `file_not_contains` | `path` / `text` | 工作区文件 |
| `shell_ok` | `command` / `expect_rc` | 跑一条命令 |
| `event_seen` / `event_absent` | `event` / `where` | **账本里有没有那条事件**（判行为，不判文本） |

`EvalReport`：`pass_rate` / `passed` / `total` / `cost_usd` / `results`。
`render_markdown(report)` / `render_json(report)` 负责落盘。

---

## 7. MCP

```python
from auditloop.mcp.client import StdioMCPClient
from auditloop.mcp.bridge import register_mcp_tools

with StdioMCPClient(["python", "-m", "auditloop.mcp.server_echo"]) as client:
    names = register_mcp_tools(registry, client)   # -> ["mcp_echo", ...]
```

MCP 工具一律标记 `mutating=True`：协议上无法知道对面会不会改状态，**不知道就按「会改」处理**。

---

## 8. 稳定性承诺

| 范围 | 承诺 |
|---|---|
| `run_loop` / `RunResult` / `Tool` / `Policy` / `TraceWriter` / `read_events` / `run_tasks` | v1.0 前允许加可选参数，不改已有签名 |
| 事件类型清单 | 只增不改；新增类型不影响旧读侧 |
| 账本文件格式 | JSONL，一行一事件，字段只增不删 |
| `auditloop.mcp.server_echo` | 示例，随时可改 |

## 9. 还没实现、不要依赖

- `spawn`（子 agent）：参数已存在，传进去会被 `task` 工具拒绝。
- 流式：`chat()` 是一次性返回；`on_event` 是事件级实时，不是 token 级。
- `trace verify`：账本自洽性校验，P4。
