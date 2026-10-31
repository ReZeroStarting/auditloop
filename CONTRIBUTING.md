# Contributing

最小的贡献路径：**加一个评测任务**或**加一个工具**。两者都不需要改循环。

---

## 环境

```bash
git clone https://github.com/ReZeroStarting/auditloop
cd auditloop
python -m pip install --no-deps -e .
python -m pip install pytest
python -m pytest -q            # 72 passed
python tools/docs_check.py     # 文档数字与链接
python -m auditloop doctor     # 九项环境自检
python tools/check_secrets.py  # 密钥扫描（只打印掩码）
git config core.hooksPath .githooks   # 启用提交前拦截（每个 clone 做一次）
```

没有运行时依赖，所以不需要虚拟环境的依赖安装；Python 3.11+ 即可。

---

## 代码结构

见 [docs/architecture.md](docs/architecture.md)。三个原则：

1. **循环层不做业务**：`core/loop.py` 只管「调模型 → 处理工具调用 → 记事件」。任何新能力优先放进工具或策略。
2. **失败要分类**：模型错（回填）、用户选择（终止）、环境错（明确报错）。别混成一句 `error`。
3. **账本先于动作**：`policy.decision` 必须在执行前写。事后补记等于没有证据。

---

## 加一个工具

```python
# src/auditloop/tools/mytool.py
from .base import Tool, ToolContext

class MyTool(Tool):
    name = "my_tool"
    description = "一句话说清它干什么（模型靠这句话决定用不用）。"
    parameters = {
        "type": "object",
        "properties": {"target": {"type": "string", "description": "..."}},
        "required": ["target"],
    }
    mutating = False          # 会改磁盘状态就设 True，否则策略不会管它

    def run(self, args: dict, ctx: ToolContext) -> str:
        return "结果文本（会被截断后进入上下文）"
```

然后在 `tools/builtin.py` 的 `default_registry()` 里注册，并补三条测试：

- 正常路径返回什么；
- 参数不合法（缺字段 / 类型错 / 未知字段）抛 `ToolArgError`；
- 路径越界时抛 `PermissionError`（如果它接受路径）。

**不要**在工具里调用 `print`——输出会污染 CLI 渲染；需要日志就写进返回值或账本。

---

## 加一个评测任务

在 `tasks/eval.jsonl` 里加一行：

```json
{"id": "t08-my-case", "prompt": "...", "files": {"a.py": "..."},
 "script": [{"tool": "fs_read", "args": {"path": "a.py"}}, {"content": "..."}],
 "checks": [{"type": "status_is", "status": "ok"}],
 "verify": "python -m pytest -q", "tags": ["read-only"]}
```

规则：

- `id` 唯一，格式 `tNN-短描述`；
- 至少一条 `checks`，否则等于没评；
- 优先用**确定性检查**（文件内容、退出码、账本事件），不要一上来就上 LLM 判分；
- 失败路径也要有用例：本项目里 `t05` / `t06` / `t07` 就是专门测「该失败的时候有没有正确失败」。

加完跑 `auditloop eval run --mode mock --gate 0.9`，通过率必须回到 100%（mock 模式下不该有偶发失败）。

---

## 加一家模型平台

先看 [docs/providers.md](docs/providers.md)。

- **OpenAI 兼容**：只加预设，不写代码；
- **原生协议**：实现 `LLM.chat()`，并在 `tests/test_llm_<provider>.py` 里用固定响应样本断言解析，
  **不要在测试里打真实网络**。

---

## 文档

- 中文为主，`README.md` 是英文（对外），`README.zh-CN.md` 是中文。
- 文档里的数字（测试数、任务数、自检数、工具数、事件数）必须能被 `python tools/docs_check.py` 校验。
  写死数字前先跑一次，拿到真实值。
- 大一点的取舍写 ADR（`docs/adr/NNNN-标题.md`），格式见现有五份。

---

## 提交

```
feat: 一句话说清加了什么能力
fix: 一句话说清修了什么错误
docs: ...
refactor: ...
test: ...
```

提交信息里请写**为什么**，不只是**做了什么**。评审时最有用的信息是「这条改动避免了什么」。

## 密钥

不要把 key 写进仓库（包括 `auditloop.toml`）。用环境变量：`AUDITLOOP_API_KEY` / `AUDITLOOP_PROVIDER`。

提交前钩子会在暂存区扫 `sk-…`、AWS、GitHub token 等模式，命中就拦下并只打印掩码。
测试夹具里的假 key 请在行尾加 `# auditloop:allow-secret`。

## PR 自查

- [ ] `python -m pytest -q` 全绿
- [ ] `python tools/docs_check.py` 通过
- [ ] `python -m auditloop demo` 仍能跑完，且 `trace replay` 结论一致
- [ ] 新行为有测试；失败路径也有测试
- [ ] 没有把密钥、绝对路径、个人目录写进代码或文档
