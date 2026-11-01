# HTML 原型 · 账本查看器

双击 `index.html` 就能打开。不需要构建、不需要服务器、不需要联网。

```
prototype/
├── index.html        # 全部界面（单文件，内嵌 CSS/JS）
├── make_data.py      # 把 data/*.json 打包成 data/runs.js
├── data/
│   ├── 01-ok.json          # 真实运行导出：完成
│   ├── 02-unverified.json  # 真实运行导出：未验证
│   ├── 03-blocked.json     # 真实运行导出：被策略拦停
│   └── runs.js             # 生成物，index.html 直接加载
└── shots/            # 三个状态的截图（验收用）
```

## 为什么是「数据 → 界面」，而不是画图

界面里的每一条事件都来自 `auditloop trace export` 的真实输出：

```bash
auditloop demo
auditloop trace export demo --out prototype/data/01-ok.json

auditloop eval run --mode mock --gate 0
auditloop trace export .auditloop/eval-runs/eval-t05-unverified-gate.jsonl --out prototype/data/02-unverified.json
auditloop trace export .auditloop/eval-runs/eval-t07-policy-blocked.jsonl --out prototype/data/03-blocked.json

python prototype/make_data.py
```

所以原型不会「画得比产品好」：产品少记一个字段，界面上就少一块。

## 三个状态

| 数据 | 状态 | 它要证明什么 |
|---|---|---|
| `01-ok` | `ok` | 完成是有证据的：验证命令 `rc=0` 写在账本里 |
| `02-unverified` | `unverified` | 模型说完成、验证没过 → **不算完成**，横幅直接显示 pytest 的失败尾部 |
| `03-blocked` | `policy_blocked` | 连续 3 次撞硬拒绝规则 → 主动停手，原因逐条可查 |

## 数据脱敏

账本里的工作区路径已替换为 `/tmp/...`（原始是本机临时目录），
公开页面不会暴露用户名与目录结构。事件本身、时间戳、token 数都是原样。

## 交互

| 操作 | 效果 |
|---|---|
| 点事件 | 右侧显示完整 payload（按类型做了专门排版） |
| `↑` / `↓` | 上下选择事件 |
| `空格` | 播放：自动逐条走完这次运行 |
| `1` / `2` / `3` | 切换三份数据 |
| 顶部分类 chip | 只保留某一类事件（模型 / 工具 / 策略 / 上下文 / 验证） |
| 地址栏 `#2` | 直接打开「未验证」现场，方便分享 |

## 底部的水位图

每条 `model.request` 的估算 token 连成一条线，琥珀色虚线是**压缩点**（上下文被压掉的时刻），
灰色虚线是 `compact_at` 水位。

这张图回答的是：「它什么时候开始忘事？」——压缩之后模型的判断往往会变差，
这条线就是那件事的可视化证据。

## 已知的粗糙处（原型的定位就是验证信息结构，不是做产品）

- 不支持加载任意 JSON（`file://` 下 fetch 被拦，所以数据走 `<script>` 预打包）；
- 没有移动端布局；
- 播放是前端定时器，不是真的按时间轴回放；
- 大账本（上千事件）没有做虚拟滚动。

这些都要等到真的要做 Web 版（P3 之后）再说。
