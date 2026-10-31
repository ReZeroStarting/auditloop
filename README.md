# auditloop

**有账本的 agent 运行时。** 一个 coding agent 的 harness：能拦、能查、能证。

[![ci](https://github.com/ReZeroStarting/auditloop/actions/workflows/ci.yml/badge.svg)](https://github.com/ReZeroStarting/auditloop/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![deps](https://img.shields.io/badge/runtime%20deps-0-brightgreen)](#零依赖)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

```bash
pip install auditloop
auditloop demo          # 不需要 API key，不需要联网，30 秒看完整个循环
```

---

## 它解决什么问题

让模型去改真实代码，真正的难点从来不是「它不会写代码」，而是这三件事：

| 问题 | 具体长什么样 | auditloop 的答案 |
|---|---|---|
| **它乱来** | `rm -rf`、删错文件、改到不该改的目录 | 策略层：硬拒绝 + 只读放行 + 需审批三档；路径越界直接拒 |
| **它说的不算** | 「我改好了，测试都过了」——实际没跑，或者跑了没过 | 验证门禁：收尾必须跑一条命令，`rc != 0` 记成 `unverified`，不算完成 |
| **它坏了查不出来** | 跑了二十分钟，结果不对，现场已经没了 | 账本：每次模型调用、每次工具执行、每次拒绝都落 JSONL，可渲染、可回放 |

一句话：**模型的「我做完了」不是结论，账本里的 `rc=0` 才是。**

这不是一个「更聪明的 agent」，是一个**能把 agent 的行为变成可审计事实**的运行时。

---

## 30 秒上手

```bash
# 1. 看它怎么干活（离线，脚本化模型，每次输出都一样）
auditloop demo

# 2. 看账本：每一步、每次拒绝、每次压缩、最后的验证
auditloop trace list
auditloop trace show demo --full

# 3. 回放：把当时模型的决策重新走一遍
auditloop trace replay demo

# 4. 换成真模型
export AUDITLOOP_BASE_URL=https://api.deepseek.com/v1
export AUDITLOOP_API_KEY=sk-xxxx
export AUDITLOOP_MODEL=deepseek-chat
auditloop run "把 app.py 里 divide 的除零行为改成抛 ValueError，跑测试验证" --verify "python -m pytest -q"
```

Windows PowerShell 用 `$env:AUDITLOOP_BASE_URL="..."`。

`auditloop run` 的退出码可以直接进 CI：

| 码 | 含义 |
|---|---|
| 0 | 完成，且验证命令 `rc=0` |
| 3 | **未验证**：模型说完了，但验证没过 |
| 4 | 被策略拦住（模型反复撞墙，已停手） |
| 5 | 预算耗尽（token / 金额到顶） |
| 6 | 步数到顶 |
| 7 | 上游模型失败（重试与降级都已用尽） |

---

## 它到底把什么记下来了

账本是 append-only 的 JSONL，一个 run 一个文件，`cat` 就能看：

```
▶ run demo · 策略 ask · 模型 mock · 工作区 /tmp/auditloop-demo-x
  → 模型调用 #1（2 条消息，约 448 tokens）
  ← 返回 stop · in 248/out 66 · 工具 todo_write
  · todo_write(items=[{'content': '定位 divide 的除零问题', ...}])
    ↳ 成功 59B · 0ms
  ...
  ⊘ 拒绝 shell：命中硬拒绝规则：\bdel\s+/([fqs]|\s)*
  ...
  ⇣ 压缩上下文：丢 8 条，约省 103 tokens
  ...
  ⚑ 审批 fs_edit：已批准（✓）
  · fs_edit(app.py)
    ↳ 成功 29B · 1ms
  ...
  ⌘ 验证 `python -m pytest -q` → rc=0
■ 结束：完成 · 10 步 · 4829in/350out tokens · $0.0000
```

九种事件，覆盖一次运行里所有值得事后追问的瞬间：

`run.created` · `model.request` · `model.response` · `policy.decision` · `tool.call` · `tool.result` · `context.compacted` · `verification` · `run.finished`

---

## 五个命令

| 命令 | 干什么 |
|---|---|
| `auditloop run "<任务>"` | 跑一个任务，实时渲染过程 |
| `auditloop demo` | 离线演示：修一个 bug，全程可见 |
| `auditloop trace list / show / replay / otel` | 看账本、回放、导出成 OpenTelemetry |
| `auditloop eval run --gate 0.9` | 跑评测集，低于门禁退出 1（CI 用） |
| `auditloop doctor` | 九项确定性自检，退出码直接给 CI |

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

设计取舍写在 [`docs/design.md`](docs/design.md)，包括每一条「为什么不用 X」。

---

## 零依赖

核心只用标准库（`urllib` 打上游、`subprocess` 起工具、`json` 记账本、`queue`+线程做 MCP）。
连 `pip install` 里都不带一个第三方包。

这么做的理由不是洁癖，是**可安装性**：agent 工具要装到别人的机器上、别人的 CI 里，
每多一个依赖就多一个劝退理由。代价是重试、超时、SSE、JSON-RPC 都得自己写——
而这恰好是这个项目想证明的部分。

---

## 评测：怎么证明改动是变好还是变差

```bash
auditloop eval run --mode mock --gate 0.9
```

七条任务，其中三条**专门验证失败路径**（被拒三次要停手、验证不过要记 unverified、步数到顶要收尾）。
通过率不是「模型多聪明」，而是「管道有没有按设计坏掉」。

```
✓ t01-fix-divide           ok                4 步    1452in   $0.0000
✓ t05-unverified-gate      unverified        1 步     226in   $0.0000
✓ t07-policy-blocked       policy_blocked    3 步     888in   $0.0000
通过率 7/7 = 100% · 2.7s
```

`mock` 模式证明的是管道与策略；`live` 模式才证明模型能力。两者混着报「通过率 100%」就是耍流氓——
这条写在 [`docs/design.md`](docs/design.md) 里。

---

## 和 keepwatch 的关系

同一台机器上的两块拼图：

- **[keepwatch](https://github.com/ReZeroStarting/keepwatch)** 管「活不丢」：租约、心跳、过期回收、验证门禁。
- **auditloop** 管「活干得对不对」：它跑、它拦、它记账、它证明。

一个守夜，一个记账。

---

## 文档

- [`docs/design.md`](docs/design.md) — 架构、数据契约、关键取舍、面试讲法
- [`docs/roadmap.md`](docs/roadmap.md) — P0 已做 / P1–P3 计划，每项带验收标准
- [`docs/decisions.md`](docs/decisions.md) — 还没定的十二个选择，每个都有推荐与代价

## License

MIT
