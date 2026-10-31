# keepwatch 实测记录（2026-10-31）

> 不是评测报告，是**使用记录**：我真的拿它管了一次任务，下面每条都有对应的命令和输出。
> 对象：`D:\MyProjects\keepwatch`，可编辑安装，代码版本 0.2.4。
> 结论放最后。

---

## 0. 怎么测的

用一个真实的开发任务队列（auditloop 的文档任务），按 README 的路径走：
`init → send → check → claim → done → verify → run → reap → doctor → checkpoint → pipeline`，
外加一次 8 进程并发认领。

**先承认一个我自己的错误**：第一版并发测试用 8 个不同 agent 名去抢同一条任务，全部抛
`FileNotFoundError`。原因是 keepwatch 按 agent 分队列，不同 agent 之间根本不存在争用，
而且未 `init` 的 agent 没有 `processing/` 目录。**是测试写错了，不是它坏了。**

---

## 1. 成立的部分（有输出为证）

### 1.1 独占认领：8 个进程抢同一条，只有 1 个赢

```
who=builder winners: 1 / 8   codes=[3, 0, 3, 3, 3, 3, 3, 3]
  w0: keepwatch: task is busy (someone is working on it): .2026...677ee1.claim
  w1: claimed 2026...677ee1 (try 1) lease until 2026-10-31T15:10:23.423Z
  w2: keepwatch: task 2026...677ee1 is already claimed (owner=builder, until ...)
```

值得记一笔：**失败原因有两种**——一种是抢令牌失败（"task is busy"），一种是租约已被持有
（"already claimed, owner=... until ..."）。两层防线各自给出准确理由，而不是笼统的 "failed"。

### 1.2 没有验证记录就不许关闭（核心承诺）

```
$ keepwatch done --who builder --id 2026...f8f57f
keepwatch: refusing to close 2026...f8f57f: no verify record with rc=0.
Run `verify --id ... --name <check> --rc 0` first, or pass --force.
exit=13
```

这条最有价值的地方不是「拒绝」，是**拒绝时告诉了你下一步该敲什么命令**。
`verify --rc 0` 之后再 `done` 立刻成功。

### 1.3 租约过期 → status 直接点名该跑哪个命令

```
- builder: 2026...110cb5 held by builder, 2s past its lease - run `reap`

$ keepwatch reap
reaped 1 task(s)
  2026...110cb5  back to pending (tries=2)
```

`tries=2` 说明这是第二次尝试，交接痕迹没丢。这条设计（把「下一步做什么」写进状态输出）
比大多数工具强，值得保持。

### 1.4 其余

| 能力 | 实测 |
|---|---|
| `run` 包装器 | 成功 `closed (rc=0)`；失败 `released back to pending (rc=3)`，且把命令的 rc 透传出来 |
| `doctor` | 9 项全 ok，退出 0；输出是人能读的清单，不是一串 JSON |
| `checkpoint` | 写出 `CHECKPOINT.md`，含 root / 时间 / 闸门状态 / 每个 agent 的待办 id 与心跳年龄 |
| `pipeline --off` | `claim` 和 `run` 都退出 14 并说明「pipeline is stopped; no task may be claimed」；`check` 仍可读（合理） |
| 账本 | 按月分文件（`audit/2026-10.jsonl`），带 `schema` 字段，append-only，闸门变更也记 |
| 速度 | 单条命令都在 200ms 内，零依赖，无服务端 |

---

## 2. 问题（真实存在，按影响排序）

### 2.1 认领未初始化的 agent → 裸 traceback

```
FileNotFoundError: [Errno 2] No such file or directory:
  'D:\\...\\agents\\w0\\processing\\.2026...claim'
```

`--who` 打错一个字母，得到的是 Python 栈，而不是「未知 agent w0，请先 init」。
对无人值守的流水线，这是最不该出现的失败形态：**栈不等于决策**，
而且它暴露了绝对路径。建议：agent 不存在时给明确消息 + 独立退出码。

### 2.2 `check` 的排序与 `run` 的取用顺序相反

实测：连发三条任务（providers → api → SECURITY）后，`check` 输出是
**SECURITY、api、providers（新→旧）**；而 `run` 默认取的是 **providers（旧）**。

这对人是个陷阱：看着列表第一条，以为下一个就是它，结果 `run` 拿走了最下面那条。
建议二者统一（要么都旧→新，要么在 `check` 里显式标一行「run 会取这条」）。

### 2.3 `--json` 没有 schema，机器消费靠猜

`--help` 只说 "machine readable output"。真实顶层字段是：

```
root, ok, agents, expired_leases, next_steps, pipeline_enabled
```

我第一次按 `list[].id` 去解析，直接失败（`找不到属性 id`）。
一个明确以 agent 为用户的工具，JSON 结构应该写在 README 里（哪怕只有五行）。

### 2.4 退出码命名空间混用

`run` 会把被包装命令的 rc 直接透传（我测的 `SystemExit(3)` → keepwatch 退出 3），
而 13（拒绝关闭）、14（闸门关闭）是它自己的码。调用方不解析 stderr 就分不清
「任务失败」和「keepwatch 自身决定」。

这个可以接受，但要有文档：**建议在 README 列一张退出码表**。
（auditloop 自己就是被这个问题逼着定义了 0/3/4/5/6/7，所以这不是挑刺。）

### 2.5 `status` 里的 "alive" 容易读错

任何命令碰一下 root 都会写心跳，所以刚跑完一条命令，`status` 就显示 `hb 0s (alive)`。
它证明的是「最近有人动过这个 root」，不是「有一个 worker 进程还活着」。
设计意图（心跳是副产品）是对的，但 `alive` 这个词让人往后者理解。

### 2.6 环境侧的假象（不是它的问题）

`pip list` 报 0.1.1，而 `keepwatch.__version__` 是 0.2.4 —— 可编辑安装的元数据没刷新。
不影响使用，但说明「用版本号判断装的是哪版」不可靠；`doctor` 里如果输出代码版本会更好。

---

## 3. 结论

**能用的，而且核心承诺是真的。**

- 我原本最怀疑的两点——「独占认领是不是纸面承诺」「done 的验证门禁会不会被绕过」——
  都用真并发和真拒绝验证过了，成立，且失败信息质量高于同类工具。
- 它适合的场景是**单机、多 worker、无人值守、任务不能丢**。这一点它做到了。
- 它的短板集中在**接口契约**：JSON 没 schema、退出码没表、排序语义不一致、错误路径有栈。
  这些不是架构问题，是一天能补完的东西，但它们决定了「别的 agent 能不能可靠地用它」。

一句话：**机制是硬的，文档是软的。**

如果只改一处：把 `--json` 的 schema 和退出码表写进 README。
因为它的用户是 agent，而 agent 读的是契约，不是散文。

---

## 4. 顺带得到的两个教训（对 auditloop 本身）

1. **`check` 类命令要标注「下一条是谁」** —— 列表顺序本身就是一种承诺。
2. **错误路径的退出码要独立命名空间** —— 别让「工具自己失败」和「它执行的东西失败」
   共用一个码，否则无人值守时归因全靠人读日志。
