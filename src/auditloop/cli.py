"""命令行入口。

退出码是刻意的，可以直接进 CI：
  0  完成（有验证记录且 rc=0）
  1  参数/配置错误
  3  未验证（模型说完了，但验证命令失败）—— 最值得盯的一种
  4  被策略拦住（模型反复撞墙）
  5  预算耗尽
  6  步数到顶
  7  上游模型失败
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config
from .core.loop import run_loop
from .errors import AuditloopError
from .llm.mock import HeuristicMockLLM
from .llm.openai_compat import OpenAICompatLLM
from .llm.replay import ReplayLLM
from .policy.approvals import AutoApprove, ConsoleApprover, RejectAll
from .policy.policy import Policy
from .tools.builtin import default_registry
from .trace import events as E
from .trace.render import printer, render, render_event
from .trace.replay import recorded_llm_responses, summarize_run
from .trace.writer import TraceWriter, read_events
from .util import setup_console

EXIT_CODES = {
    "ok": 0,
    "unverified": 3,
    "policy_blocked": 4,
    "budget_exceeded": 5,
    "max_steps": 6,
    "model_error": 7,
}


def build_llm(cfg: Config, *, mock: bool = False, stream: bool = False, on_delta=None):
    if mock or not cfg.model.live:
        return HeuristicMockLLM()
    from .providers import resolve

    provider = None
    try:
        provider = resolve(provider=cfg.model.provider, base_url=cfg.model.base_url, model=cfg.model.model)
    except KeyError:
        provider = None  # 裸 URL 且模型名齐全：当作自定义平台
    base_url = cfg.model.base_url or (provider.base_url if provider else "")
    model = cfg.model.model or (provider.default_model if provider else "")
    return OpenAICompatLLM(
        base_url,
        cfg.model.api_key,
        model,
        temperature=cfg.model.temperature,
        max_output_tokens=cfg.model.max_output_tokens,
        timeout_s=cfg.model.timeout_s,
        retries=cfg.model.retries,
        provider=provider,
        stream=stream,
        on_delta=on_delta,
    )


def _approver(args: argparse.Namespace, mode: str):
    if getattr(args, "yes", False) or mode == "auto":
        return AutoApprove()
    if sys.stdin.isatty():
        return ConsoleApprover()
    return RejectAll("非交互环境，无人可以审批；如需全自动请加 --yes 或 --policy auto")


def _resolve_run(cfg: Config, name: str) -> Path:
    path = Path(name)
    if path.is_file():
        return path
    candidate = cfg.runs_dir / f"{name}.jsonl"
    if candidate.is_file():
        return candidate
    raise AuditloopError(f"找不到账本：{name}（在 {cfg.runs_dir} 下也没有）")


# ---------------------------------------------------------------- run
def cmd_run(args: argparse.Namespace) -> int:
    cfg = Config.load(workspace=args.workspace, state_dir=args.state_dir)
    if args.policy:
        cfg.policy_mode = args.policy
    cfg.ensure_dirs()
    if args.max_steps:
        cfg.budget.max_steps = args.max_steps
    if args.budget_usd:
        cfg.budget.max_cost_usd = args.budget_usd

    task = " ".join(args.task).strip()
    if not task:
        print("错误：任务描述不能为空", file=sys.stderr)
        return 1

    policy = Policy(mode=cfg.policy_mode)
    stream_on = args.stream and not (args.json or args.quiet)
    stream_state = {"started": False}

    def _on_delta(text: str) -> None:
        if not stream_state["started"]:
            stream_state["started"] = True
            print("\n  ┄ 流式：", end="", flush=True)
        sys.stdout.write(text)
        sys.stdout.flush()

    llm = build_llm(cfg, mock=args.mock, stream=stream_on, on_delta=_on_delta if stream_on else None)
    live = cfg.model.live and not args.mock
    if not args.json:
        print(f"auditloop {__version__} · 策略 {cfg.policy_mode} · "
              f"{'真实上游 ' + cfg.model.model if live else 'MockLLM（离线演示）'}")
        print(f"工作区 {cfg.workspace}")
        print("-" * 60)

    on_event = None if (args.json or args.quiet) else printer()
    try:
        with TraceWriter(cfg.runs_dir) as trace:
            result = run_loop(
                task,
                llm=llm,
                registry=default_registry(),
                policy=policy,
                approver=_approver(args, cfg.policy_mode),
                workspace=cfg.workspace,
                trace=trace,
                budget=cfg.budget,
                model_cfg=cfg.model,
                verify_cmd=args.verify,
                compact_at=args.compact_at,
                keep_recent=cfg.keep_recent_messages,
                tool_chars=cfg.tool_output_chars,
                on_event=on_event,
            )
    except AuditloopError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        if stream_state["started"]:
            print()
        print("-" * 60)
        print(result.answer)
        print("-" * 60)
        print(
            f"状态 {result.status} · {result.steps} 步 · {result.tool_calls} 次工具调用 · "
            f"被拒 {result.denials} 次 · {result.usage.input_tokens}in/{result.usage.output_tokens}out tokens · "
            f"${result.cost_usd:.4f}"
        )
        if result.verification:
            print(f"验证 `{result.verification['command']}` → rc={result.verification['rc']}")
        print(f"账本 {result.trace_path}")
    return EXIT_CODES.get(result.status, 1)


# ---------------------------------------------------------------- demo
def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import run_demo

    cfg = Config.load(workspace=args.workspace, state_dir=args.state_dir)
    print(f"auditloop {__version__} 演示：一个带 bug 的小仓库 + 脚本化模型")
    print("看点：策略拒绝了「删文件」、上下文被压缩过、收尾有验证门禁")
    print("-" * 60)
    # 账本落在当前目录下，演示完可以立刻 `auditloop trace list` / `trace replay`
    result, workdir = run_demo(runs_dir=cfg.runs_dir, on_event=printer())
    print("-" * 60)
    print(result.answer)
    print("-" * 60)
    print(f"状态 {result.status} · {result.steps} 步 · 被拒 {result.denials} 次 · ${result.cost_usd:.4f}")
    print(f"工作目录 {workdir}")
    print(f"账本 {result.trace_path}")
    print()
    print("继续看：")
    print(f"  auditloop trace show {result.trace_path}")
    print(f"  auditloop trace replay {result.trace_path}")
    return EXIT_CODES.get(result.status, 1)


# ---------------------------------------------------------------- trace
def cmd_trace(args: argparse.Namespace) -> int:
    cfg = Config.load(workspace=args.workspace, state_dir=args.state_dir)

    if args.trace_cmd == "list":
        runs = sorted(cfg.runs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not runs:
            print(f"{cfg.runs_dir} 下还没有账本")
            return 0
        print(f"{'run':<30} {'状态':<10} {'步数':<5} {'拒绝':<5} {'成本':<10} 工具")
        for path in runs[: args.limit]:
            try:
                s = summarize_run(read_events(path))
            except Exception as exc:  # 半截文件也要能列出来
                print(f"{path.stem:<30} 解析失败：{exc}")
                continue
            tools = ", ".join(f"{k}×{v}" for k, v in sorted(s["tools"].items())) or "-"
            print(f"{path.stem:<30} {s['status']:<10} {s['steps']:<5} {s['denials']:<5} "
                  f"${s['cost_usd']:<9.4f} {tools}")
        return 0

    if args.trace_cmd in {"show", "otel"}:
        path = _resolve_run(cfg, args.run)
        events = read_events(path)
        if args.trace_cmd == "otel":
            from .trace.otel import to_otel_spans

            print(json.dumps(to_otel_spans(events), ensure_ascii=False, indent=2))
            return 0
        print(f"# {path}")
        print(render(events, full=args.full))
        print()
        summary = summarize_run(events)
        print(f"事件 {summary['events']} 条 · 工具 {summary['tools']} · 工具错误 {summary['tool_errors']} · "
              f"压缩 {summary['compactions']} 次")
        return 0

    if args.trace_cmd == "export":
        from .trace.export import export_run

        path = _resolve_run(cfg, args.run)
        payload = export_run(read_events(path), source=path)
        text = json.dumps(payload, ensure_ascii=False, indent=args.indent)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8", newline="\n")
            print(f"已导出 {args.out}（{len(payload['timeline'])} 条事件）")
        else:
            print(text)
        return 0

    if args.trace_cmd == "replay":
        path = _resolve_run(cfg, args.run)
        events = read_events(path)
        created = next((e for e in events if e.type == E.RUN_CREATED), None)
        if created is None:
            print("账本里没有 run.created，无法回放", file=sys.stderr)
            return 1
        original = summarize_run(events)
        responses = recorded_llm_responses(events)
        if not responses:
            print("账本里没有模型响应，无法回放", file=sys.stderr)
            return 1
        workspace = Path(str(created.payload.get("workspace") or "."))
        if not workspace.is_dir():
            print(f"原工作区已不存在：{workspace}；请加 --workspace 指定一个替代目录", file=sys.stderr)
            return 1
        with TraceWriter(cfg.runs_dir, run_id=f"replay-{path.stem}") as trace:
            result = run_loop(
                str(created.payload.get("task") or ""),
                llm=ReplayLLM(responses),
                registry=default_registry(),
                policy=Policy(mode=str(created.payload.get("policy") or "ask")),
                approver=AutoApprove(),
                workspace=workspace,
                trace=trace,
                max_steps=args.max_steps,
            )
        print(f"回放 {path.name} → {trace.path.name}")
        print(f"  原始：status={original['status']} steps={original['steps']} 工具={original['tools']}")
        print(f"  回放：status={result.status} steps={result.steps}")
        same_status = result.status == original["status"]
        print(f"  结论：{'一致' if same_status else '不一致'}")
        return 0 if same_status else 1

    print(f"未知子命令 {args.trace_cmd}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------- eval
def cmd_eval(args: argparse.Namespace) -> int:
    from .eval.report import render_json, render_markdown
    from .eval.runner import run_tasks
    from .eval.schema import load_tasks

    cfg = Config.load(workspace=args.workspace, state_dir=args.state_dir)
    tasks_path = Path(args.tasks)
    if not tasks_path.is_file():
        print(f"任务集不存在：{tasks_path}", file=sys.stderr)
        return 1
    tasks = load_tasks(tasks_path)
    if not tasks:
        print("任务集是空的", file=sys.stderr)
        return 1

    llm = None
    if args.mode != "mock":
        llm = build_llm(cfg, mock=False)
        if not cfg.model.live:
            print("live 模式需要先配置 AUDITLOOP_BASE_URL / AUDITLOOP_API_KEY", file=sys.stderr)
            return 1

    print(f"auditloop eval · {len(tasks)} 条任务 · 模式 {args.mode}")
    print("-" * 60)

    def report_task(result) -> None:
        mark = "✓" if result.ok else "✗"
        print(f"  {mark} {result.id:<24} {result.status:<16} {result.steps:>2} 步  "
              f"{result.input_tokens:>6}in  ${result.cost_usd:.4f}")

    report = run_tasks(
        tasks,
        mode=args.mode,
        llm=llm,
        traces_dir=cfg.runs_dir.parent / "eval-runs",
        on_task=report_task,
        keep_workdirs=args.keep,
    )

    outdir = Path(args.report).parent if args.report else Path("eval-report")
    outdir.mkdir(parents=True, exist_ok=True)
    md_path = outdir / "report.md"
    json_path = outdir / "results.json"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(render_json(report), encoding="utf-8")

    print("-" * 60)
    print(f"通过率 {report.passed}/{report.total} = {report.pass_rate:.0%} · "
          f"{report.elapsed_ms / 1000:.1f}s · ${report.cost_usd:.4f}")
    print(f"报告 {md_path}")
    print(f"结果 {json_path}")

    if args.gate is not None and report.pass_rate < args.gate:
        print(f"门禁未通过：{report.pass_rate:.0%} < {args.gate:.0%}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------- doctor / tools / mcp
def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import run_doctor

    cfg = Config.load(workspace=args.workspace, state_dir=args.state_dir)
    if getattr(args, "probe", False):
        return cmd_probe(cfg)
    return run_doctor(cfg, verbose=args.verbose)


def cmd_probe(cfg: Config) -> int:
    from .probe import probe_provider, verdict

    if not cfg.model.live:
        print(
            "探测需要真实上游：设置 AUDITLOOP_PROVIDER（如 deepseek）+ AUDITLOOP_API_KEY，"
            "或直接给 AUDITLOOP_BASE_URL",
            file=sys.stderr,
        )
        return 1
    target = cfg.model.base_url or cfg.model.provider or "自定义"
    print(f"探测 {cfg.model.model or '（默认模型）'} @ {target} …")
    report = probe_provider(build_llm(cfg))
    ok, message = verdict(report)
    print(("  ✓ " if ok else "  ✗ ") + message)
    if report.get("ok"):
        print(f"    延迟 {report['latency_ms']}ms · finish={report['finish_reason']} · "
              f"usage={'有' if report['usage_reported'] else '无'}")
    return 0 if ok else 1


def cmd_providers(args: argparse.Namespace) -> int:
    from .providers import PRESETS, describe, get

    if args.providers_cmd == "show":
        info = describe(get(args.key))
        for key, value in info.items():
            print(f"{key:<18} {value}")
        return 0

    print(f"{'key':<14} {'base_url':<52} {'默认模型':<26} 备注")
    for key in sorted(PRESETS):
        preset = PRESETS[key]
        print(f"{key:<14} {preset.base_url:<52} {(preset.default_model or '—'):<26} {preset.notes[:40]}")
    print()
    print("用法：AUDITLOOP_PROVIDER=<key> AUDITLOOP_API_KEY=<key> auditloop run ...")
    print("探测：auditloop doctor --probe")
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    registry = default_registry()
    print(f"{'工具':<14} {'改状态':<8} 说明")
    for name in registry.names():
        tool = registry.get(name)
        assert tool is not None
        print(f"{name:<14} {'是' if tool.mutating else '否':<8} {tool.description[:70]}")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp.client import MCPError, StdioMCPClient

    argv = list(args.server_argv)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("用法：auditloop mcp list -- python -m auditloop.mcp.server_echo", file=sys.stderr)
        return 1
    try:
        with StdioMCPClient(argv) as client:
            if args.mcp_cmd == "list":
                info = client.server_info or {}
                server = (info.get("serverInfo") or {}).get("name", "?")
                print(f"已连接 MCP 服务：{server}")
                for tool in client.list_tools():
                    print(f"  {tool.get('name'):<12} {str(tool.get('description'))[:70]}")
                return 0
            payload = json.loads(args.json_args or "{}")
            print(client.call_tool(args.tool, payload))
            return 0
    except MCPError as exc:
        print(f"MCP 错误：{exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auditloop",
        description="有账本的 agent 运行时：可观测、可回放、可验证。",
    )
    parser.add_argument("--version", action="version", version=f"auditloop {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", default=None, help="agent 只能在这个目录内活动")
    common.add_argument("--state-dir", default=None, help="账本目录，默认 ./.auditloop")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", parents=[common], help="跑一个任务")
    p_run.add_argument("task", nargs="+", help="任务描述")
    p_run.add_argument("--policy", choices=["ask", "never", "auto"], default=None)
    p_run.add_argument("--yes", action="store_true", help="自动批准所有需要审批的操作（演示/CI 用）")
    p_run.add_argument("--mock", action="store_true", help="强制使用 MockLLM")
    p_run.add_argument("--stream", action="store_true", help="逐字打印模型输出（不影响账本）")
    p_run.add_argument("--max-steps", type=int, default=None)
    p_run.add_argument("--budget-usd", type=float, default=None)
    p_run.add_argument("--compact-at", type=int, default=12_000, help="上下文压缩水位（估算 token）")
    p_run.add_argument("--verify", default=None, help="收尾前必须通过的命令，rc!=0 记为 unverified")
    p_run.add_argument("--json", action="store_true")
    p_run.add_argument("--quiet", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_demo = sub.add_parser("demo", parents=[common], help="离线演示：修一个 bug，全过程可见")
    p_demo.set_defaults(func=cmd_demo)

    p_trace = sub.add_parser("trace", parents=[common], help="看账本")
    trace_sub = p_trace.add_subparsers(dest="trace_cmd", required=True)
    t_list = trace_sub.add_parser("list", parents=[common])
    t_list.add_argument("--limit", type=int, default=20)
    t_list.set_defaults(func=cmd_trace)
    t_show = trace_sub.add_parser("show", parents=[common])
    t_show.add_argument("run")
    t_show.add_argument("--full", action="store_true")
    t_show.set_defaults(func=cmd_trace)
    t_replay = trace_sub.add_parser("replay", parents=[common])
    t_replay.add_argument("run")
    t_replay.add_argument("--max-steps", type=int, default=24)
    t_replay.set_defaults(func=cmd_trace)
    t_otel = trace_sub.add_parser("otel", parents=[common])
    t_otel.add_argument("run")
    t_otel.set_defaults(func=cmd_trace)
    t_export = trace_sub.add_parser("export", parents=[common], help="导出为前端可消费的 JSON")
    t_export.add_argument("run")
    t_export.add_argument("--out", default=None, help="写入文件；缺省打印到 stdout")
    t_export.add_argument("--indent", type=int, default=2)
    t_export.set_defaults(func=cmd_trace)

    p_eval = sub.add_parser("eval", parents=[common], help="跑评测集")
    eval_sub = p_eval.add_subparsers(dest="eval_cmd", required=True)
    e_run = eval_sub.add_parser("run", parents=[common])
    e_run.add_argument("--tasks", default="tasks/eval.jsonl")
    e_run.add_argument("--mode", choices=["mock", "live"], default="mock")
    e_run.add_argument("--report", default=None)
    e_run.add_argument("--gate", type=float, default=None, help="通过率低于这个值就退出 1")
    e_run.add_argument("--keep", action="store_true", help="保留每条任务的临时工作目录")
    e_run.set_defaults(func=cmd_eval)

    p_doctor = sub.add_parser("doctor", parents=[common], help="环境自检")
    p_doctor.add_argument("--verbose", action="store_true")
    p_doctor.add_argument("--probe", action="store_true", help="对真实上游做一次能力探测")
    p_doctor.set_defaults(func=cmd_doctor)

    p_providers = sub.add_parser("providers", help="模型平台预设")
    provider_sub = p_providers.add_subparsers(dest="providers_cmd", required=True)
    pr_list = provider_sub.add_parser("list")
    pr_list.set_defaults(func=cmd_providers)
    pr_show = provider_sub.add_parser("show")
    pr_show.add_argument("key")
    pr_show.set_defaults(func=cmd_providers)

    p_tools = sub.add_parser("tools", help="列出工具")
    p_tools.set_defaults(func=cmd_tools)

    p_mcp = sub.add_parser("mcp", help="MCP 工具探测")
    mcp_sub = p_mcp.add_subparsers(dest="mcp_cmd", required=True)
    m_list = mcp_sub.add_parser("list")
    m_list.add_argument("server_argv", nargs=argparse.REMAINDER)
    m_list.set_defaults(func=cmd_mcp)
    m_call = mcp_sub.add_parser("call")
    m_call.add_argument("tool")
    m_call.add_argument("--json", dest="json_args", default="{}")
    m_call.add_argument("server_argv", nargs=argparse.REMAINDER)
    m_call.set_defaults(func=cmd_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AuditloopError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断（账本保留在 .auditloop/runs）", file=sys.stderr)
        return 130
