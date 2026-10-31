"""提交前密钥扫描：`python tools/check_secrets.py --staged`

存在的理由很朴素：人的记性防不住「顺手 git add -f」和「打包发出去」。
这个脚本只做一件事——在提交前把看起来像真 key 的字符串找出来，并且**只打印掩码**。

支持的行内豁免（写在含密钥那一行的注释里）：
    KEY = "sk-..."  # auditloop:allow-secret   （测试夹具、文档示例用）
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai-style", re.compile(r"sk-[A-Za-z0-9_\-]{30,}")),
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-token", re.compile(r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}")),
    ("github-pat", re.compile(r"github_pat_[A-Za-z0-9_]{30,}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
]

ALLOW_MARKER = "auditloop:allow-secret"
SKIP_DIRS = {".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv", ".auditloop"}
MAX_BYTES = 2_000_000


def mask(value: str) -> str:
    if len(value) <= 10:
        return value[:2] + "…"
    return f"{value[:4]}…{value[-4:]}"


def scan_text(text: str) -> list[tuple[int, str, str]]:
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for rule, pattern in PATTERNS:
            for match in pattern.finditer(line):
                hits.append((lineno, rule, mask(match.group(0))))
    return hits


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    try:
        if path.stat().st_size > MAX_BYTES:
            return []
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in raw[:4096]:
        return []
    return scan_text(raw.decode("utf-8", "replace"))


def _git(*args: str) -> list[str]:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", check=False)
    except OSError:
        return []
    if out.returncode != 0:
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def targets(staged: bool, explicit: list[str]) -> list[Path]:
    if explicit:
        return [Path(p) for p in explicit]
    names = _git("diff", "--cached", "--name-only", "--diff-filter=ACM") if staged else _git("ls-files")
    return [Path(n) for n in names if Path(n).suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".whl"}]


def main(argv: list[str]) -> int:
    staged = "--staged" in argv
    explicit = [a for a in argv if not a.startswith("-")]
    files = targets(staged, explicit)

    findings: list[str] = []
    scanned = 0
    for path in files:
        if not path.is_file() or (set(path.parts) & SKIP_DIRS):
            continue
        scanned += 1
        for lineno, rule, masked in scan_file(path):
            findings.append(f"{path}:{lineno}  [{rule}]  {masked}")

    if findings:
        print(f"发现 {len(findings)} 处疑似密钥（只显示掩码）：")
        for item in findings:
            print(f"  ✗ {item}")
        print("\n如果这是测试夹具或文档示例，在该行加注释 `# auditloop:allow-secret`。")
        print("如果这是真 key：不要提交，先轮换，再清理工作区。")
        return 1

    scope = "暂存区" if staged else "工作区"
    print(f"密钥扫描通过：{scanned} 个文件（{scope}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
