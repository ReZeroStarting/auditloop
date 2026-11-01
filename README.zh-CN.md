# auditloop

**有账本的 agent 运行时。** 一个 coding agent 的 harness：能拦、能查、能证。

[English](README.md) · [设计文档](docs/design.md) · [计划](docs/plan.md)

```bash
pip install auditloop
auditloop demo          # 不需要 API key，不需要联网，30 秒看完整个循环
```n
▶ **[在线账本查看器](https://rezerostarting.github.io/auditloop/)** —— 浏览器里点开三次真实运行（成功 / 未验证 / 被拦停）。

---

## 它解决什么问题

让模型去改真实代码，失败几乎从来不是“它不会写代码”，而是这三件事：

| 问题 | 具体长什么样 | auditloop 的答案 |
|---|---|---|
| **它乱来** | `rm -rf`、删错文件、改到工作目录之外 | 策略层：硬拒绝 + 只读放行 + 需审批三档；路径越界直接拒 |
| **它说的不算** | “我改好了，测试都过了”——实际没跑，或者跑了没过 | 验证门禁：收尾必须跑一条命令，`rc != 0` 记成 `unverified`，不算完成 |
| **它坏了查不出来** | 跑了二十分钟，结果不对，现场已经没了 | 账本：每次模型调用、每次工具执行、每次拒绝都落 JSONL，可渲染、可回放 |

一句话：**模型的“我做完了”不是结论，账本里的 `rc=0` 才是。**

![同一段脚本跑两遍：没有门禁时记 ok；有门禁时账本记下 rc=1，运行记为未验证](docs/assets/demo-contrast.gif)

*同一段脚本、同一个仓库、同一个“模型”，唯一差别是收尾跑不跑一条验证命令。
自己试：`auditloop demo --contrast`。*

*动画由真实命令输出逐帧渲染（`tools/make_demo_gif.py`），不是屏幕录制。*

---

## 30 秒上手

```bash
auditloop demo                  # 离线演示：修一个 bug，10 步，1 次被策略拒绝
auditloop trace list            # 看账本
auditloop trace show demo --full
auditloop trace replay demo     # 回放：状态与步数应当一致
```

换成真模型：

```powershell
$env:AUDITLOOP_BASE_URL="https://api.deepseek.com/v1"
$env:AUDITLOOP_API_KEY="sk-xxxx"
$env:AUDITLOOP_MODEL="deepseek-chat"
auditloop run "把 divide 的除零行为改成抛 ValueError，跑测试验证" --verify "python -m pytest -q"
```

### 退出码（可直接进 CI）

| 码 | 含义 |
|---|---|
| 0 | 完成，且验证命令 `rc=0` |
| 3 | **未验证**：模型说完了，但验证没过 |
| 4 | 被策略拦住（模型反复撞墙，已停手） |
| 5 | 预算耗尽（token / 金额到顶） |
| 6 | 步数到顶 |
| 7 | 上游模型失败（重试与降级都已用尽） |

---

## 账本长什么样

一个 run 一个 append-only JSONL 文件，`cat` 就能看：

```
▶ run demo · 策略 ask · 模型 mock · 工作区 /tmp/auditloop-demo-x
  → 模型调用 #1（2 条消息，约 448 tokens）
  ← 返回 stop · in 248/out 66 · 工具 todo_write
  · todo_write(items=[...])
    ↳ 成功 59B · 0ms
  ...
  ⊘ 拒绝 shell：命中硬拒绝规则：\bdel\s+/([fqs]|\s)*
  ...
  ⇣ 压缩上下文：丢 8 条，约省 103 tokens
  ...
  ⌘ 验证 `python -m pytest -q` → rc=0
■ 结束：完成 · 10 步 · 4829in/350out tokens · $0.0000
```

九种事件，覆盖一次运行里所有值得事后追问的瞬间：
`run.created` · `model.request` · `model.response` · `policy.decision` · `tool.call` ·
`tool.result` · `context.compacted` · `verification` · `run.finished`

---

## 六个命令

| 命令 | 干什么 |
|---|---|
| `auditloop run "<任务>" --stream` | 跑一个任务，逐字输出（不影响账本） |
| `auditloop demo` | 离线演示：修一个 bug，全程可见 |
| `auditloop demo --contrast` | 对照实验：同一段脚本，有/无验证门禁 |
| `auditloop trace list / show / replay / verify / resume / otel` | 看账本、校验自洽、中断续跑、导出 OpenTelemetry |
| `auditloop eval run --gate 0.9` | 跑评测集，低于门禁退出 1 |
| `auditloop doctor` | 九项确定性自检，退出码给 CI |
| `auditloop providers list` | 14 家平台预设，换平台只改 `AUDITLOOP_PROVIDER` |
| `auditloop doctor --probe` | 真实探测：这个端点支不支持工具调用 |

---

## 架构

```
                    ┌──────────────────────────────────────────┐
   你的任务 ───────▶│              CLI (argparse)              │
                    └───────────────┬──────────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │            agent loop (core)             │
                    │   budget → compact → llm → 策略 → 工具    │
                    └───┬───────────────┬──────────────┬───────┘
                        │               │              │
           ┌────────────▼───┐   ┌───────▼──────┐  ┌────▼──────────┐
           │  context 层     │   │   策略层      │  │   工具层       │
           │  估算/截断/压缩  │   │ ask/never/auto│  │ fs/shell/grep │
           │                 │   │ 硬拒绝 + 审批  │  │ glob/todo/task│
           └────────────────┘   └──────────────┘  └────┬──────────┘
                                                       │ MCP (stdio, JSON-RPC)
                                                ┌──────▼──────────┐
                                                │  外部工具进程    │
                                                └─────────────────┘

   所有动作 ──▶ 账本 JSONL ──▶ show / list / replay / otel
                            └─▶ eval（确定性检查 → 通过率 → 门禁）
```

---

## 零依赖

核心只用标准库：`urllib` 打上游、`subprocess` 起工具、`json` 记账本、`queue` + 线程做 MCP。
`pip install` 里不带一个第三方包。

理由不是洁癖，是可安装性：agent 工具要装到别人的机器和 CI 里，
每多一个依赖就多一个劝退理由。代价是重试、超时、SSE、JSON-RPC 都得自己写——
而这恰好是这个项目想证明的部分。

---

## 评测

```bash
auditloop eval run --mode mock --gate 0.9
```

八条任务。其中三条（`t04`、`t05`、`t07`）断言的是**失败路径行为**，**刻意只跑 mock**：
真实模型会直接拒绝删库、会真的把失败测试修好，用它去套这些用例只会得到假失败。

| 模式 | 结果（2026-10-31） | 证明什么 |
|---|---|---|
| `mock` | **8/8**，$0.0000 | 管道、策略、账本、失败路径 |
| `live` · deepseek-chat | **5/5**，$0.0119，32.6s | 5 条与模型无关的用例上的真实能力 |

真仓库端到端一次：**15 步 · 18 次工具调用 · 1 次策略拒绝 · 验证 `rc=0` · $0.0127**。
那次拒绝是真的——模型想 `rm -f`，被拦住，然后自己在回答里说明了这件事。

---

## 文档

[英文 README](README.md) · [设计](docs/design.md) · [模型支持](docs/providers.md) · [API](docs/api.md) ·
[架构](docs/architecture.md) · [ADR](docs/adr/) · [安全](SECURITY.md) · [计划](docs/plan.md) ·
[路线图](docs/roadmap.md) · [待定决策](docs/decisions.md) · [FAQ](docs/faq.md) ·
[keepwatch 集成](docs/integration-keepwatch.md)

## 姊妹项目

- **[keepwatch](https://github.com/ReZeroStarting/keepwatch)** 管“活不丢”；
- **auditloop** 管“活干得对不对”。

一个守夜，一个记账。

## License

MIT
