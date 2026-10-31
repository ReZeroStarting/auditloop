"""docs_check.py — 文档里的数字和链接，必须能被机器校验。

为什么需要这个：
README 里写「72 项测试」「7 条评测」，每加一个测试，这句话就变成谎言。
面试官不会去数，但他会问「这个数字现在还准吗」。与其记得改，不如让 CI 报错。

检查三件事：
1. 测试数 / 评测任务数 / 自检项数 / 工具数 —— 文档里写的必须等于代码里的；
2. 文档之间的相对链接不能指向不存在的文件；
3. 事件类型清单不能漏。

用法：
    python tools/docs_check.py
    python tools/docs_check.py --verbose
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DOC_FILES = sorted(ROOT.glob("*.md")) + sorted(ROOT.glob("docs/**/*.md"))


def count_tests() -> int:
    """用 pytest 自己的收集器数，不靠解析输出文本（也不起子进程——本机沙箱不允许管道）。"""
    import pytest

    class Collector:
        def __init__(self) -> None:
            self.items: list = []

        def pytest_collection_modifyitems(self, items):
            self.items = list(items)

    collector = Collector()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        pytest.main(["--collect-only", "-q", "-p", "no:cacheprovider"], plugins=[collector])
    return len(collector.items)


def count_tasks() -> int:
    from auditloop.eval.schema import load_tasks

    path = ROOT / "tasks" / "eval.jsonl"
    return len(load_tasks(path)) if path.is_file() else 0


def count_doctor_checks() -> int:
    from auditloop.doctor import CHECKS

    return len(CHECKS)


def count_tools() -> int:
    from auditloop.tools.builtin import default_registry

    return len(default_registry().names())


def count_events() -> int:
    from auditloop.trace.events import ALL_TYPES

    return len(ALL_TYPES)


def check_numbers(verbose: bool = False) -> list[str]:
    actual = {
        "tests": count_tests(),
        "tasks": count_tasks(),
        "doctor": count_doctor_checks(),
        "tools": count_tools(),
        "events": count_events(),
    }
    patterns = {
        "tests": re.compile(r"(\d+)\s*项测试|(\d+)\s+tests\b"),
        "tasks": re.compile(r"(\d+)\s*条任务|(\d+)\s+(?:eval\s+)?tasks\b"),
        "doctor": re.compile(r"(\d+)\s*项(?:确定性)?自检|(\d+)\s+(?:deterministic\s+)?self-checks"),
        "tools": re.compile(r"(\d+)\s*个工具|(\d+)\s+tools\b"),
        "events": re.compile(r"(\d+)\s*种事件|(\d+)\s+event types"),
    }
    if verbose:
        print("代码里的真实数字：", actual)

    problems: list[str] = []
    for path in DOC_FILES:
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for key, pattern in patterns.items():
                for match in pattern.finditer(line):
                    claimed = int(match.group(1) or match.group(2))
                    if claimed != actual[key]:
                        problems.append(
                            f"{path.relative_to(ROOT)}:{lineno} 写的是 {claimed}，实际是 {actual[key]}（{key}）：{line.strip()[:80]}"
                        )
    return problems


def check_links(verbose: bool = False) -> list[str]:
    link_re = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    problems: list[str] = []
    for path in DOC_FILES:
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for target in link_re.findall(line):
                if target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                clean = target.split("#")[0].strip()
                if not clean:
                    continue
                resolved = (path.parent / clean).resolve()
                if not resolved.exists():
                    problems.append(f"{path.relative_to(ROOT)}:{lineno} 链接指向不存在的位置：{target}")
    if verbose:
        print(f"扫描了 {len(DOC_FILES)} 份文档")
    return problems


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv
    problems = check_numbers(verbose) + check_links(verbose)
    if problems:
        print(f"发现 {len(problems)} 处不一致：")
        for item in problems:
            print(f"  ✗ {item}")
        return 1
    print(f"文档自检通过：{len(DOC_FILES)} 份文档，数字与代码一致，链接无死链")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
