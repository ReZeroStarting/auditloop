"""评测报告：Markdown，能直接贴进 PR 或 README。

一句设计原则：**报告要能让没参加这次改动的人做决定。**
所以必须有：通过率、失败的是哪些检查（而不是「失败 3 条」）、
每条任务的账本路径（想深挖时点得进去）、总成本（决定要不要在 CI 里天天跑）。
"""

from __future__ import annotations

import json
from typing import Any

from .runner import EvalReport


def render_markdown(report: EvalReport) -> str:
    lines = [
        "# auditloop 评测报告",
        "",
        f"- 模式：`{report.mode}`" + ("（mock 只证明管道，不证明模型能力）" if report.mode == "mock" else ""),
        f"- 开始时间：{report.started_at}",
        f"- **通过率：{report.passed}/{report.total} = {report.pass_rate:.0%}**",
        f"- 总耗时：{report.elapsed_ms / 1000:.1f}s · 总 token：{report.input_tokens}in/{report.output_tokens}out · 总成本：${report.cost_usd:.4f}",
        "",
    ]
    if report.skipped:
        lines += [
            f"- 跳过 {len(report.skipped)} 条不适用本模式的用例：{', '.join(report.skipped)}"
            "（行为类用例依赖脚本化模型，见 docs/providers.md）",
            "",
        ]
    lines += [
        "| 任务 | 结果 | 状态 | 步数 | tokens | 成本 | 失败检查 |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in report.results:
        fails = "；".join(c["detail"] for c in result.failed_checks) or "—"
        lines.append(
            f"| `{result.id}` | {'✅' if result.ok else '❌'} | {result.status} | {result.steps} | "
            f"{result.input_tokens}/{result.output_tokens} | ${result.cost_usd:.4f} | {fails} |"
        )

    failures = [r for r in report.results if not r.ok]
    if failures:
        lines += ["", "## 失败详情", ""]
        for result in failures:
            lines.append(f"### `{result.id}` · {result.status}")
            lines.append("")
            for check in result.checks:
                mark = "✅" if check["ok"] else "❌"
                lines.append(f"- {mark} `{check['type']}`：{check['detail']}")
            if result.answer:
                lines.append("")
                lines.append("回答摘录：")
                lines.append("")
                lines.append("```")
                lines.append(result.answer[:1200])
                lines.append("```")
            lines.append("")
            if result.trace_path:
                lines.append(f"账本：`{result.trace_path}`")
            lines.append("")

    lines += ["", "## 账本", ""]
    for result in report.results:
        if result.trace_path:
            lines.append(f"- `{result.id}` → `{result.trace_path}`")
    return "\n".join(lines) + "\n"


def render_json(report: EvalReport) -> str:
    payload: dict[str, Any] = {
        "mode": report.mode,
        "started_at": report.started_at,
        "elapsed_ms": report.elapsed_ms,
        "pass_rate": report.pass_rate,
        "passed": report.passed,
        "total": report.total,
        "input_tokens": report.input_tokens,
        "output_tokens": report.output_tokens,
        "cost_usd": round(report.cost_usd, 6),
        "results": [r.as_dict() for r in report.results],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
