"""doctor：九项确定性自检，健康退出 0，有问题退出 1。

为什么值得单独做一个命令（而不是「跑一下就知道了」）：
agent 的故障经常是环境问题而不是代码问题——目录没写权限、上游 key 过期、
任务集文件被改坏、MCP 服务起不来。这些都不该等到跑了十分钟才发现。
doctor 的价值是**把排查前移到一条命令**，并且退出码可以直接给 CI。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Callable

from .config import Config
from .errors import ConfigError
from .eval.schema import load_tasks
from .policy.policy import Policy
from .tools.builtin import default_registry


def _check_python() -> tuple[bool, str]:
    ok = sys.version_info >= (3, 11)
    return ok, f"Python {sys.version.split()[0]}（需要 >= 3.11）"


def _check_workspace(cfg: Config) -> tuple[bool, str]:
    return cfg.workspace.is_dir(), f"工作目录 {cfg.workspace}"


def _check_writable(cfg: Config) -> tuple[bool, str]:
    probe = cfg.runs_dir / ".doctor-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, f"账本目录可写 {cfg.runs_dir}"
    except OSError as exc:
        return False, f"账本目录不可写 {cfg.runs_dir}：{exc}"


def _check_policy(cfg: Config) -> tuple[bool, str]:
    try:
        Policy(mode=cfg.policy_mode)
        return True, f"策略模式 {cfg.policy_mode}"
    except ValueError as exc:
        return False, str(exc)


def _check_model(cfg: Config) -> tuple[bool, str]:
    if cfg.model.live:
        return True, f"真实上游 {cfg.model.model} @ {cfg.model.base_url}"
    return True, "未配置上游，将使用 MockLLM（离线可跑，但不代表模型能力）"


def _check_tools() -> tuple[bool, str]:
    registry = default_registry()
    names = registry.names()
    return len(names) >= 5, f"工具 {len(names)} 个：{', '.join(names)}"


def _check_tasks() -> tuple[bool, str]:
    for candidate in (Path("tasks/eval.jsonl"), Path(__file__).resolve().parents[2] / "tasks" / "eval.jsonl"):
        if candidate.is_file():
            try:
                tasks = load_tasks(candidate)
                return bool(tasks), f"任务集 {candidate}（{len(tasks)} 条）"
            except Exception as exc:
                return False, f"任务集 {candidate} 解析失败：{exc}"
    return True, "未找到任务集（不影响 run，只影响 eval）"


def _check_git() -> tuple[bool, str]:
    path = shutil.which("git")
    return bool(path), f"git {path or '未安装（不影响核心功能）'}"


def _check_shell() -> tuple[bool, str]:
    path = shutil.which("python") or shutil.which("python3")
    return bool(path), f"python 可执行文件 {path or '未找到（shell 工具会失败）'}"


CHECKS: list[tuple[str, Callable[[Config], tuple[bool, str]]]] = [
    ("python", lambda cfg: _check_python()),
    ("workspace", _check_workspace),
    ("writable", _check_writable),
    ("policy", _check_policy),
    ("model", _check_model),
    ("tools", lambda cfg: _check_tools()),
    ("tasks", lambda cfg: _check_tasks()),
    ("shell", lambda cfg: _check_shell()),
    ("git", lambda cfg: _check_git()),
]


def run_doctor(cfg: Config, *, verbose: bool = False) -> int:
    print(f"auditloop doctor · 工作目录 {cfg.workspace}")
    print("-" * 60)
    failures = 0
    for name, check in CHECKS:
        try:
            ok, detail = check(cfg)
        except ConfigError as exc:
            ok, detail = False, str(exc)
        except Exception as exc:  # 自检本身不能崩
            ok, detail = False, f"自检异常 {type(exc).__name__}: {exc}"
        if not ok:
            failures += 1
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name:<10} {detail if (ok or verbose) else detail}")
    print("-" * 60)
    if failures:
        print(f"{failures} 项未通过")
        return 1
    print("九项自检全部通过")
    return 0
