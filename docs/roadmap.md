# 路线图

> 原则：每个阶段结束时，都必须有一条**能当场跑给人看**的命令。
> 不是「做了三个模块」，是「多了一个证据」。

---

## P0 · 骨架（已完成，v0.1.0）

| 能力 | 验收命令 | 状态 |
|---|---|---|
| agent 循环 + 五种退出 | `auditloop demo` | ✅ |
| 策略：硬拒绝 / 只读 / 审批三档 | `auditloop eval run`（t04、t07） | ✅ |
| 验证门禁（rc≠0 记 unverified） | `auditloop eval run`（t05） | ✅ |
| 账本：写入 / 渲染 / 列表 / 回放 | `auditloop trace show / replay` | ✅ |
| 上下文：截断 + 压缩 + 配对完整性 | `pytest tests/test_context.py` | ✅ |
| 评测：7 条任务 + 报告 + 门禁 | `auditloop eval run --gate 0.9` | ✅ |
| MCP 客户端（stdio JSON-RPC） | `auditloop mcp list -- python -m auditloop.mcp.server_echo` | ✅ |
| OpenTelemetry 语义映射 | `auditloop trace otel <run>` | ✅ |
| 环境自检 9 项 | `auditloop doctor` | ✅ |
| 73 项测试 | `python -m pytest` | ✅ |

---

## P1 · 让它能用真模型干活（2–3 天）

目标：接上真实上游，跑一个真实仓库的真实 bug。

| 事项 | 验收标准 |
|---|---|
| 真实上游跑通 | `auditloop run "..." --verify "pytest"` 在一个真实仓库上返回 `ok` |
| 流式输出 | 模型返回时逐字打印；账本仍记完整消息（流式是渲染层的事，不改决策层） |
| 工具调用并发 | 同一条响应里的多个工具并行执行；账本里仍能看出先后（span 不变） |
| 预算熔断 | 超 `max_cost_usd` 时停手，退出码 5，且账本里有最后一条 `run.finished` |
| 首个真实评测 | 用 `--mode live` 跑 7 条，把通过率写进 README（**不美化**） |

**风险**：真模型会连续撞策略墙，可能一次 run 就被 `policy_blocked`。
这恰好是数据：统计「平均几次被拒才开始换路」。

---

## P2 · 让它可靠（1 周）

| 事项 | 验收标准 |
|---|---|
| 子 agent 落地 | `task` 工具真的起一个隔离上下文的子循环，主账本里有子 run 的引用 |
| 压缩升级为两级 | 先确定性摘要；只有当摘要仍超预算时才调模型压缩，且记 `context.compacted` 的 `strategy` 字段 |
| 沙箱（可选容器） | `--sandbox docker` 时 shell 在容器内执行；宿主目录只读挂载 |
| MCP 工具热插拔 | `auditloop.toml` 里声明多个 MCP 服务，启动时自动注册，策略默认按 mutating 处理 |
| 账本校验 | `auditloop trace verify <run>`：seq 连续、span 闭合、usage 可加总，有问题退出 1 |
| 失败注入测试 | 进程在「模型已返回、工具未执行」处被 kill，能否从账本看出这件事 |

---

## P3 · 让它像产品（2 周+）

| 事项 | 验收标准 |
|---|---|
| HTTP 层 | FastAPI（或纯标准库 http.server）+ SSE，把账本事件实时推到浏览器 |
| 预算双重闸门 | 单会话上限 + 全站日上限；到顶降级而不是 500 |
| 评测看板 | 每次改动跑 eval，结果存历史，能看「第 N 次改动后通过率从多少到多少」 |
| 长跑验证 | 连续 6 小时无人值守跑评测集，账本零丢失、零重复关闭 |
| 与 keepwatch 集成 | 长任务用 keepwatch 排队，auditloop 作为 worker 执行；交接痕迹两边都对得上 |

---

## 不着急做的（写下来是为了不被问倒）

- **多 agent 协作**：ClawBridge 已经覆盖了，这里不重复造。真要接，走共享目录 + 消息。
- **向量检索 / RAG**：代码搜索用 grep + glob 已经够；检索解决的是「不知道在哪」，代码场景通常知道。
- **微调**：这是运行时项目，模型能力是输入不是输出。
- **GUI 编辑器**：CLI 是这个项目最好的形态，图形化会把注意力从账本移开。
