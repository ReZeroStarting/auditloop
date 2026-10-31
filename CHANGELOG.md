# Changelog

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## [Unreleased]

### 新增

- **模型平台预设表**（14 家）：`auditloop providers list / show`，换平台只改 `AUDITLOOP_PROVIDER`。
  各家差异（`max_tokens` vs `max_completion_tokens`、认证头、路径）收进预设，不散落在代码里。
- **流式输出**（`run --stream`）：SSE 解析 + 工具调用增量拼装（`arguments` 是碎片，拼错就是半截 JSON）。
  流式只影响渲染，账本与回放完全不变——这是刻意的边界。
- **能力探测**（`doctor --probe`）：一次真实调用判定「端点通不通 / 支不支持 tools / 有没有 usage」。
  没有 key 时会明确告诉你缺什么，而不是假装通过。
- **`trace export`**：把一次运行导出成前端可消费的 JSON（timeline 里带已渲染文本）。
- **`trace verify`**：七项账本自洽性校验（序号连续、工具调用与结果成对、
  策略判定先于执行、usage 能对上）。缺 `run.finished` 只算提示不算错误——
  进程被杀正是账本最有价值的时刻。
- **`trace resume`**：从账本重建上下文接着跑。工具输出全文与 `call_id` 现在会落账；
  「工具已调用、结果未落账」的悬空调用会被丢弃并记明，因为无法确定它到底执行了没有。
- **子 agent**（`run --subagent`）：`task` 工具起一个上下文干净的子 run，
  写自己的账本（`<run>-subN.jsonl`），默认只给只读工具，且不能递归；
  子 run 的 token 计回父 run，成本不漏账。评测集新增 `t08-subagent`。
- **HTML 原型**：`prototype/index.html` 账本查看器，三个真实状态（ok / unverified / policy_blocked）。
- 测试数量从 72 增加到 105：新增 SSE 拼装、Provider 预设、能力探测、导出结构。
## [0.1.0] - 2026-10-31

第一个可用版本。目标不是功能多，是**把「可观测 / 可拦截 / 可验证」三件事跑通**。

### 新增

- **agent 循环**：五种退出状态（`ok` / `max_steps` / `budget_exceeded` / `model_error` / `policy_blocked` / `unverified`），
  与之对应的退出码可直接进 CI。
- **策略层**：`ask` / `never` / `auto` 三档；破坏性命令硬拒绝；路径越出工作目录直接拒；
  需要审批的操作在没有审批者时一律拒绝。
- **验证门禁**：`--verify "<命令>"` 收尾时执行，`rc != 0` 时把运行记为 `unverified`，
  即使模型声称完成。这是本项目最核心的一条纪律。
- **账本**：append-only JSONL，九种事件；坏行跳过（进程被 kill 时的半截 JSON 不会让读取失败）。
- **回放**：`auditloop trace replay` 用账本里的模型响应重跑循环，比较状态是否一致。
- **上下文管理**：token 估算、工具输出头尾截断、超水位确定性压缩；压缩后保证
  `tool_call` / `tool_result` 配对不被切断。
- **工具集**：`fs_read` / `fs_write` / `fs_edit` / `fs_list` / `glob` / `grep` / `shell` / `todo_write` / `task`。
- **评测**：8 条任务（3 条专测失败路径），确定性检查、Markdown + JSON 报告、通过率门禁。
- **MCP 客户端**：stdio 传输、JSON-RPC 2.0、超时、stderr 排空；配一个可跑的示例服务端。
- **OpenTelemetry 导出**：`trace otel` 输出 `gen_ai.*` 语义约定的 span。
- **doctor**：九项确定性自检。
- **demo**：脚本化演示，离线可跑，每次输出一致。

### 工程

- 零运行时依赖，仅标准库；`pip install` 即装即跑。
- 112 项测试，覆盖策略、工具、上下文、循环、账本、回放、评测、MCP。
- CI 矩阵：Linux 3.11/3.12/3.13 + Windows 3.12/3.13。
- 发布走 PyPI Trusted Publishing（OIDC），不保存任何密钥。

### 已知限制

见 `docs/design.md` 第 7 节：不是沙箱、没有流式、token 为估算、回放不保证结果一致。
