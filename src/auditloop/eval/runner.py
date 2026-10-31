"""评测执行器：每条任务一个干净的工作目录、一份独立账本、一组确定性检查。

三个刻意的设计：
1. **每个任务一个临时仓库**。复用同一个目录，前一条任务的残留会让后一条任务假通过，
   这类假阳性是最难发现的。
2. **账本也逐条留档**。评测失败时，第一件事是打开那条任务的账本看它当时干了什么，
   而不是加 print 重跑。
3. **mock 模式与 live 模式共用同一条管道**。mock 证明的是管道通、策略生效、账本完整；
   live 模式才证明模型能力。混在一起宣称「通过率 100%」就是耍流氓。
"""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.loop import run_loop
from ..llm.mock import HeuristicMockLLM, ScriptedLLM
from ..policy.policy import Policy
from ..tools.builtin import default_registry
from ..trace.writer import TraceWriter, read_events
from .checks import run_checks
from .schema import Task


@dataclass
class TaskResult:
    id: str
    ok: bool
    checks: list[dict[str, Any]]
    status: str
    steps: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int
    answer: str
    trace_path: str | None = None
    workdir: str | None = None

    @property
    def failed_checks(self) -> list[dict[str, Any]]:
        return [c for c in self.checks if not c["ok"]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ok": self.ok,
            "status": self.status,
            "steps": self.steps,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "duration_ms": self.duration_ms,
            "checks": self.checks,
            "answer": self.answer,
            "trace": self.trace_path,
        }


@dataclass
class EvalReport:
    mode: str
    results: list[TaskResult] = field(default_factory=list)
    elapsed_ms: int = 0
    started_at: str = ""

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.results)

    @property
    def output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.results)

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.results)


def run_tasks(
    tasks: list[Task],
    *,
    mode: str = "mock",
    llm: Any = None,
    traces_dir: str | Path | None = None,
    policy_mode: str = "auto",
    keep_workdirs: bool = False,
    on_task: Callable[[TaskResult], None] | None = None,
) -> EvalReport:
    from ..trace.events import now_iso

    traces_dir = Path(traces_dir or Path.cwd() / ".auditloop" / "eval-runs")
    traces_dir.mkdir(parents=True, exist_ok=True)
    report = EvalReport(mode=mode, started_at=now_iso())
    started = time.perf_counter()

    for task in tasks:
        workdir = Path(tempfile.mkdtemp(prefix=f"auditloop-{task.id}-"))
        for rel, content in task.files.items():
            target = workdir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")

        task_llm = ScriptedLLM(task.script) if (mode == "mock" and task.script) else (llm or HeuristicMockLLM())
        with TraceWriter(traces_dir, run_id=f"eval-{task.id}") as trace:
            task_started = time.perf_counter()
            result = run_loop(
                task.prompt,
                llm=task_llm,
                registry=default_registry(),
                policy=Policy(mode=policy_mode),
                workspace=workdir,
                trace=trace,
                max_steps=task.max_steps,
                verify_cmd=task.verify,
                allow_subagent=True,  # 评测要能覆盖 task 工具；只有脚本真的调用才会触发子 run
            )
            duration = int((time.perf_counter() - task_started) * 1000)
            events = read_events(trace.path)

        checks = run_checks(
            task.checks,
            answer=result.answer,
            status=result.status,
            workspace=workdir,
            events=events,
        )
        task_result = TaskResult(
            id=task.id,
            ok=all(c["ok"] for c in checks) if checks else result.ok,
            checks=checks,
            status=result.status,
            steps=result.steps,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cost_usd=result.cost_usd,
            duration_ms=duration,
            answer=result.answer,
            trace_path=str(trace.path),
            workdir=str(workdir) if keep_workdirs else None,
        )
        report.results.append(task_result)
        if on_task:
            on_task(task_result)
        if not keep_workdirs:
            shutil.rmtree(workdir, ignore_errors=True)

    report.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return report
