"""最小嵌入示例：注册自己的工具，跑一个 agent，拿到账本。

    python examples/embed_custom_tool.py

不需要 API key —— 用 ScriptedLLM 演示管道；把 llm 换成 OpenAICompatLLM 就是真的。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from auditloop.core.loop import run_loop
from auditloop.llm.mock import ScriptedLLM
from auditloop.policy.approvals import AutoApprove
from auditloop.policy.policy import Policy
from auditloop.tools.base import Tool, ToolContext
from auditloop.tools.registry import Registry
from auditloop.trace.writer import TraceWriter


class WordCount(Tool):
    """一个最小的自定义工具：数行数。"""

    name = "word_count"
    description = "数一个文本文件有多少行。"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "相对工作区的路径"}},
        "required": ["path"],
    }

    def run(self, args: dict, ctx: ToolContext) -> str:
        path = ctx.resolve(args["path"])  # 路径越界会抛 PermissionError
        lines = path.read_text(encoding="utf-8").splitlines()
        return f"{args['path']}: {len(lines)} 行"


def main() -> int:
    workspace = Path(tempfile.mkdtemp(prefix="auditloop-example-"))
    (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")

    registry = Registry([WordCount()])
    llm = ScriptedLLM(
        [
            {"tool": "word_count", "args": {"path": "notes.txt"}},
            {"content": "notes.txt 有 3 行。"},
        ]
    )

    with TraceWriter(workspace / "runs") as trace:
        result = run_loop(
            "notes.txt 有多少行？",
            llm=llm,
            registry=registry,
            policy=Policy(mode="auto"),
            approver=AutoApprove(),   # 无人值守：显式全批准
            workspace=workspace,
            trace=trace,
        )

    print(f"status : {result.status}")
    print(f"answer : {result.answer}")
    print(f"steps  : {result.steps} · tool calls: {result.tool_calls}")
    print(f"ledger : {result.trace_path}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
