# FAQ

## 跑不起来

**`auditloop: command not found`**
`python -m auditloop ...` 也能跑。若两者都不行，说明装的是别的 Python：
`python -c "import sys; print(sys.executable)"` 和 `where.exe auditloop` 对比一下。

**Windows 下中文乱码**
CLI 启动时会强制 UTF-8（`util/console.py`）。若仍有乱码，多半是终端本身：
`chcp 65001`，或改用 Windows Terminal。

**`doctor` 报 `tasks` 未找到**
只有在仓库根目录才有 `tasks/eval.jsonl`。装成包之后没有这个文件是正常的，只影响 `eval`。

## 模型相关

**模型一直说「参数错误」，来回打转**
看账本里的 `tool.result`，`error` 字段会写清楚是哪个参数。常见原因：

- 模型把数字写成字符串（`"limit": "10"`）——auditloop 会拒绝，这是刻意的；
- 模型臆造参数名（`line` 而不是 `offset`）——也会拒绝。

连续 3 次被拒会直接 `policy_blocked`，这是设计，不是 bug。

**模型压根不调用工具，只回一句话**
两种可能：① 这个模型不支持 function calling；② 端点是 OpenAI 兼容但网关把 `tools` 字段吞了。
用 `auditloop doctor --probe`（P3 实现）能直接判定。临时办法：换成支持工具调用的模型。

**配置了 key 还是走 MockLLM**
两个变量都要有：`AUDITLOOP_BASE_URL` 和 `AUDITLOOP_API_KEY`。少一个就退回 Mock，`doctor` 会告诉你当前用的是哪个。

## 行为相关

**状态是 `ok`，但仓库里没变化**
`ok` 只表示「模型没有再调工具，且验证命令通过」。如果任务本身不需要改文件，`ok` 是正常结果。
要确认它真的改了东西，看账本里的 `tool.result`（有 `fs_write` / `fs_edit` 吗），或加一条 `file_contains` 检查。

**状态是 `unverified` 是什么意思**
模型说完成了，但你给的 `--verify` 命令返回了非 0。这是本项目最重要的一种状态：
**它把「说完成」和「真完成」分开了。** 退出码 3，可以直接卡 CI。

**为什么 `mock` 的成本是 $0**
因为价格表按**响应里的真实模型名**查；`mock` / `replay` 的价格是 0。这不是算错，是不让演示数据带上假成本。

**`--policy never` 下模型什么都干不了**
`never` 的含义是「只读」：不写文件、不跑会改状态的命令。适合「先看看为什么坏了」。
要让它动手，用默认的 `ask`（逐次确认）或 `auto`（全自动，危险）。

**被策略拒绝了，模型会重试吗**
会换路，但同一个调用连续 3 次被拒就停手（`policy_blocked`，退出码 4）。
被拒原因会以 `DENIED: ...` 回填进上下文，所以好的模型会换办法——这也是在测模型。

## 账本相关

**账本最后一行是半截 JSON**
进程被 kill 了。读侧会跳过坏行（`read_events`），`trace list` 仍能列出这个 run。
这也正是「为什么账本要 append + flush」的理由：能留下「崩之前做了什么」。

**`trace replay` 结论不一致**
回放保证的是「输入相同 → 决策相同」，不保证「结果相同」——工具会真的再执行一次，
文件可能已经被改过。若状态不同，先看账本里原始 `run.finished` 的 `status`。

**账本能删吗**
能，但删了就没有证据了。默认落在 `./.auditloop/runs/`，已经在 `.gitignore` 里。

## 和其他工具的关系

**和 Claude Code / Codex CLI 有什么区别**
同类（都是 harness），取向不同：它们优化交互体验，auditloop 优化**事后审计**——
账本能回放、能导出 OpenTelemetry、能当 CI 门禁。

**和 keepwatch 什么关系**
keepwatch 管「活不丢」（任务队列、租约、回收），auditloop 管「活干得对不对」。
两者都零依赖，可以用 `keepwatch run --who auditloop -- auditloop run ...` 串起来，但**不做强耦合**。

**能不能离线用**
能。`auditloop demo`、`trace`、`eval --mode mock` 全程不联网。只有 `run` 接真实模型时需要网络。

## 贡献

见 [CONTRIBUTING.md](../CONTRIBUTING.md)。最小路径是加一条评测任务，不用改循环。
