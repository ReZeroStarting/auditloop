# ADR 0001：零运行时依赖

- **状态**：已采纳
- **日期**：2026-10-31
- **相关**：[architecture.md](../architecture.md)、[design.md](../design.md)

## 背景

这个工具的目标用户是「想给 agent 加一层可靠性」的开发者，它需要装到别人的机器、别人的 CI、
以及可能是离线的环境里。候选依赖：`httpx`（HTTP）、`pydantic`（数据模型）、`rich`（渲染）、
`typer`（CLI）。

## 决定

`pip install auditloop` 不安装任何第三方包。核心只用标准库：

- HTTP：`urllib.request`
- 并发/超时：`threading` + `queue`（MCP 读线程）
- 数据模型：`dataclass` + 普通 dict
- CLI：`argparse`
- 配置：`tomllib`（3.11+ 标准库）

## 后果

**好处**

- 安装从不失败：没有编译、没有版本冲突、没有镜像站问题。
- CI 不需要装依赖，跑得快；`--no-deps` 安装在任何 Python 3.11+ 上都能过。
- 依赖越少，攻击面越小——对一个能执行 `shell` 的工具，这条尤其重要。

**代价**

- HTTP 重试、超时、SSE 解析、JSON-RPC 都要自己写，出错概率高于用成熟库。
- 没有 `pydantic` 的校验，参数校验要靠手写的子集（`tools/registry.py` 约 40 行）。
- 渲染层没有 `rich`，只能自己拼 ANSI 和 Unicode 符号。

## 替代方案

| 方案 | 为什么没选 |
|---|---|
| `httpx` + `pydantic` | 体验更好，但让 `pip install` 从「一秒钟」变成「要看网络脸色」 |
| 只用标准库但放弃 MCP | MCP 是当前 agent 工具生态的实际约定，放弃它等于放弃扩展能力 |
| 把依赖做成 extra（`auditloop[fast]`） | 两条代码路径要同时维护和测试，收益不值 |

## 什么时候应该重新考虑

如果出现下面任一情况，这条决定就过时了：

1. 要做流式且 SSE 分帧在真实上游上频繁出错（自写解析的维护成本超过依赖成本）；
2. 要支持 Anthropic/Gemini 原生协议，消息结构复杂到手工校验开始出 bug；
3. 要发布 Web 服务（`http.server` 的生产能力不足）。
