# 架构

> `design.md` 讲「为什么这么设计」，这一页讲「代码长什么样、失败会落在哪里」。

---

## 1. 模块地图

```
src/auditloop/
├── cli.py            argparse 入口，五个子命令，负责退出码
├── config.py         配置合并（env > toml > 默认）、价格表
├── errors.py         异常分层：PolicyDenied / ToolError / LLMError / BudgetExceeded
├── doctor.py         九项确定性自检
├── demo.py           脚本化演示（离线可跑）
│
├── core/
│   ├── loop.py       ★ agent 循环：五种退出、三道闸门
│   ├── context.py    token 估算、截断、压缩（保证 tool 配对完整）
│   └── messages.py   消息构造与解析（OpenAI 形状）
│
├── llm/
│   ├── base.py       LLM 协议 + LLMResponse + Usage
│   ├── openai_compat.py  真实上游（urllib、重试、缓存字段归一）
│   ├── mock.py       ScriptedLLM / HeuristicMockLLM
│   ├── replay.py     从账本回放
│   └── router.py     降级链
│
├── tools/
│   ├── base.py       Tool 基类 + ToolContext（路径监狱）
│   ├── registry.py   Schema 校验 + 派发
│   ├── fs.py         read / write / edit / list / glob / grep
│   ├── shell.py      执行命令，返回真实 rc
│   ├── todo.py       任务清单
│   ├── task.py       子 agent 入口（P4 未实现）
│   └── builtin.py    默认工具集（窄，刻意的）
│
├── policy/
│   ├── policy.py     ask/never/auto + 命令分类 + 硬拒绝
│   └── approvals.py  AutoApprove / RejectAll / ConsoleApprover
│
├── trace/
│   ├── events.py     Event 模型 + 九种事件类型
│   ├── writer.py     追加写 + 坏行跳过
│   ├── render.py     人类可读渲染
│   ├── replay.py     从事件还原模型响应、统计
│   └── otel.py       映射到 gen_ai.* 语义约定
│
├── eval/
│   ├── schema.py     Task 定义与加载
│   ├── checks.py     七种确定性检查
│   ├── runner.py     临时仓库 + 独立账本 + 聚合
│   └── report.py     Markdown / JSON 报告
│
└── mcp/
    ├── client.py     stdio JSON-RPC，读线程 + 超时
    ├── bridge.py     MCP 工具 → Tool（默认 mutating）
    └── server_echo.py 示例服务端（也是客户端测试的对手）
```

---

## 2. 依赖方向

```
cli ──▶ config, doctor, demo, eval, trace, core
eval ──▶ core, tools, policy, trace
core ──▶ llm.base, tools, policy, trace, config
tools ──▶ policy（只读 ToolContext）, errors
llm ──▶ core.messages, core.context（仅 mock/replay 用来估算 token）
mcp ──▶ tools
```

**包级有一个双向引用**：`core/loop.py` 依赖 `llm/base.py`，而 `llm/mock.py` 依赖 `core/context.py`。
模块级**无环**（`llm/base.py` 不 import `core`），所以不会出现导入顺序问题，
但这也是个信号：`Usage` / `Message` 这类共享类型迟早应该下沉到 `auditloop/types.py`。

---

## 3. 一次 run 的调用序列

```
cli.cmd_run
 └─ Config.load                     # 环境变量 → 配置；无 key 则用 MockLLM
 └─ TraceWriter(runs_dir)           # 打开 JSONL
 └─ run_loop
     ├─ trace.emit(run.created)
     ├─ for step in 1..max_steps:
     │   ├─ 预算检查                → budget_exceeded
     │   ├─ context.fit(...)        → 截断 / 压缩；compacted 则 emit(context.compacted)
     │   ├─ trace.emit(model.request)
     │   ├─ llm.chat(messages, specs)  → LLMError 则 model_error
     │   ├─ trace.emit(model.response)
     │   ├─ 无 tool_calls → 收尾
     │   └─ for call in tool_calls:
     │       ├─ messages.parse_args  → 失败即回填 ERROR
     │       ├─ policy.check         → trace.emit(policy.decision)
     │       ├─ 需审批 → approver.ask
     │       ├─ 被拒 → 回填 DENIED，累计 3 次 → policy_blocked
     │       ├─ trace.emit(tool.call)
     │       ├─ registry.dispatch    → 异常转为观测文本，不中断
     │       └─ trace.emit(tool.result)
     ├─ verify_cmd 存在 → run_shell → trace.emit(verification)
     │                    rc!=0 → status = unverified
     └─ trace.emit(run.finished)
```

---

## 4. 不变量

这五条是「账本能当证据」的前提：

1. **`tool` 消息前面必须紧跟带对应 `tool_call_id` 的 `assistant` 消息**。
   压缩、截断都不能破坏它，否则上游直接 400。`tests/test_context.py` 有一条专门守。
2. **`seq` 在同一文件内严格递增**。断号说明有事件丢了——这是 `trace verify`（P4）要检查的。
3. **`policy.decision` 在工具执行之前写入**。事后补记等于没有证据。
4. **`cost_usd` 单调不减**，且按响应里的真实模型名计价。mock/replay 价格为 0。
5. **`tool.result` 一定跟着 `tool.call`**：参数解析失败、未知工具、策略拒绝都会产出一条结果事件，
   区别只在 `ok=false`。

**已知缺口**：`run.finished` 是在正常路径末尾 emit 的，**没有包 try/finally**。
如果 `registry.specs()` 或磁盘写入抛异常，账本会停在半截。
P4 的 `trace verify` 会把「有 run.created 但没有 run.finished」明确报出来。

---

## 5. 失败域

| 出问题的层 | 表现 | 影响范围 |
|---|---|---|
| 上游（llm） | `model_error` 退出 | 本次 run 结束，账本完整 |
| 工具（tool） | 回填 `ERROR: ...` | 模型可自愈，run 继续 |
| 策略（policy） | 回填 `DENIED` | 模型可换路；连续 3 次升级为 `policy_blocked` |
| 上下文（context） | 压缩或截断 | 不改变状态，只影响可见信息 |
| 账本（trace） | 写失败 | 未处理——磁盘满时 run 会带着异常退出（P4 待办） |
| 评测（eval） | 单条任务失败 | 记入报告，不影响其他任务 |

**设计原则**：模型和工具的失败要变成「观测」，用户选择的失败要变成「终止」，
环境的失败要变成「明确的错误信息」。三类混成一种，agent 就永远学不会纠错。

---

## 6. 扩展点

| 想做的事 | 改哪里 | 不需要动哪里 |
|---|---|---|
| 加一个工具 | 继承 `Tool`，注册进 `Registry` | 循环、策略、账本 |
| 加一家模型 | 实现 `LLM.chat`，或只加 base_url 预设 | 其余全部 |
| 加一种检查 | `eval/checks.py` 里加一个分支 | runner、报告 |
| 加一种事件 | `trace/events.py` 常量 + `render.py` 一个分支 | 读侧（未知类型被忽略） |
| 改审批方式 | 实现 `ask()` | 策略与循环 |
| 换账本后端 | 替换 `TraceWriter`（接口是 `emit`） | 循环层 |

---

## 7. 代码规模（2026-10-31）

| 目录 | 行数 | 说明 |
|---|---|---|
| `core/` | ~520 | 循环 300 + 上下文 150 + 消息 70 |
| `tools/` | ~640 | fs 最重（读改搜） |
| `trace/` | ~400 | 渲染与回放占一半 |
| `eval/` | ~380 | 检查项最琐碎 |
| `llm/` | ~330 | OpenAI 兼容 + mock + replay |
| `policy/` | ~230 | 命令分类比想象中啰嗦 |
| `mcp/` | ~260 | 线程与超时 |
| `cli/doctor/demo/config` | ~600 | |
| 合计 | ~3100 | 测试 ~550 行 |

数字会漂移，所以 `tools/docs_check.py` 只校验文档里写死的那几个（测试数、任务数、自检数、工具数、事件数）。
