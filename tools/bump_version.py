"""版本号只该在一个地方手工改，其余由脚本同步。

三处必须一致：`pyproject.toml`、`src/auditloop/__init__.py`、`CHANGELOG.md` 的标题。
漏改任何一处，Build 出来的包和 `--version` 就会对不上；而 PyPI 的版本号是不可变的，
发错一次只能靠新版本号补救。

用法：
    python tools/bump_version.py            # 只检查一致性
    python tools/bump_version.py 0.2.1      # 改版本号 + 准备 CHANGELOG 小节
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "src" / "auditloop" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def current_versions() -> dict[str, str]:
    pyproject = re.search(r'^version = "([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.M)
    init = re.search(r'^__version__ = "([^"]+)"', INIT.read_text(encoding="utf-8"), re.M)
    changelog = re.search(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG.read_text(encoding="utf-8"), re.M)
    return {
        "pyproject.toml": pyproject.group(1) if pyproject else "?",
        "src/auditloop/__init__.py": init.group(1) if init else "?",
        "CHANGELOG.md": changelog.group(1) if changelog else "?",
    }


def check() -> int:
    versions = current_versions()
    values = set(versions.values())
    for name, value in versions.items():
        print(f"  {name:<28} {value}")
    if len(values) == 1 and "?" not in values:
        print(f"一致：{values.pop()}")
        return 0
    print("不一致！发版前必须统一")
    return 1


def bump(target: str) -> int:
    if not SEMVER.match(target):
        print(f"版本号格式不对：{target}（要 x.y.z）")
        return 2
    old = current_versions()

    text = PYPROJECT.read_text(encoding="utf-8")
    PYPROJECT.write_text(re.sub(r'^version = "[^"]+"', f'version = "{target}"', text, count=1, flags=re.M),
                         encoding="utf-8", newline="\n")

    text = INIT.read_text(encoding="utf-8")
    INIT.write_text(re.sub(r'^__version__ = "[^"]+"', f'__version__ = "{target}"', text, count=1, flags=re.M),
                    encoding="utf-8", newline="\n")

    ledger = CHANGELOG.read_text(encoding="utf-8")
    if f"## [{target}]" not in ledger:
        header = f"## [{target}] - {date.today().isoformat()}\n\n### 变更\n\n- \n\n"
        anchor = re.search(r"^## \[", ledger, re.M)
        if anchor:
            ledger = ledger[: anchor.start()] + header + ledger[anchor.start():]
        else:
            ledger = ledger.rstrip() + "\n\n" + header
        CHANGELOG.write_text(ledger, encoding="utf-8", newline="\n")

    print(f"{old['pyproject.toml']} -> {target}（三处已同步，CHANGELOG 增加了待填小节）")
    print("\n下一步：")
    print(f"  1. 编辑 CHANGELOG.md，填上 [{target}] 的「变更」条目")
    print("  2. python -m pytest && python tools/docs_check.py")
    print(f'  3. git commit -am "release: {target}" && git push')
    print(f"  4. GitHub → Releases → Draft a new release → tag v{target} → Publish")
    print("     （publish.yml 会用 Trusted Publishing 自动上传，不需要 token）")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        return check()
    return bump(argv[0])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
