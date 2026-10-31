# 与 keepwatch 集成（最小可用）

> 决策见 [decisions.md](decisions.md) 的 D12：**只做最窄的一种集成，不双向耦合。**
> 理由：两个工具都在演进，强耦合会互相拖死；而它们本来就各管一段。

## 分工

| | 管什么 | 不管什么 |
|---|---|---|
| **keepwatch** | 活有没有人接、接的人死没死、租约过期了怎么回收 | 活干得对不对 |
| **auditloop** | 干的这段对不对、有没有验证、账本可不可查 | 任务队列、多 worker 调度 |

两者都用「退出码 + 文件系统」当接口，所以拼起来不需要任何适配层：

```bash
keepwatch run --who auditloop -- auditloop eval run --mode mock --gate 0.9
```

keepwatch 认领一条任务 → 执行 `auditloop` → 拿到真实 rc → `rc=0` 就 `done`，否则放回队列重试。
**两边的验证语义天然对齐**：auditloop 的 `--gate` 决定 rc，keepwatch 的 `done` 要求 rc=0。

## 实测（2026-10-31，无需 API key）

```bash
keepwatch --root .keepwatch init auditloop
keepwatch --root .keepwatch send --from me --to auditloop --subject "跑一次 mock 评测"
keepwatch --root .keepwatch run --who auditloop -- auditloop eval run --mode mock --gate 0.9
```

```
通过率 8/8 = 100% · 3.5s · $0.0000
closed 20261031T155258-04ff71 (rc=0)
```

keepwatch 侧：

```
auditloop   pending 0  processing 0  done 1  hb 3s (alive)
```

它自己的 `doctor` 会盯着租约：

```
[ok  ] tasks      8 task(s) parse
[warn] leases     1 expired lease(s) - run `reap`
healthy
```

> 观察：过期租约只给 `warn`、退出码仍是 0（因为「可回收」不等于「账本坏了」）。
> 这个分级是合理的，但 `healthy` 这个词在有人看到 warn 时会显得偏乐观。

## 边界（用之前必须知道）

1. **两本账不合并**。auditloop 记每次运行的细节，keepwatch 记每次交接。想还原全过程，得两边对着看。
2. **退出码会撞**。`keepwatch run` 会把子命令的 rc 原样透传，所以调用方分不清「auditloop 判了 unverified」和「keepwatch 自己失败」——这是 field notes 里记过的问题，解决办法是在外层用 `--json` 读结构，别只看 rc。
3. **`check` 与 `run` 的排序不一致**：`check` 新→旧，`run` 取最旧。人读列表会预测错下一条。
4. **认领未 init 的 agent 会抛裸 traceback**（已知），所以 `init` 要先做。

## 什么时候值得升级成强集成

只有一种情况：需要**跨机器**跑。那时 keepwatch 的文件目录要换成网络盘或对象存储，
auditloop 的 `--workspace` 要换成容器挂载——两件事都得改，现在不值得。

## 不做的事

- 不做「auditloop 自己调 keepwatch」：那会让一个 CLI 工具依赖另一个 CLI 工具的布局。
- 不做共享账本格式：两个项目的 schema 各自演进，强行统一只会双输。
