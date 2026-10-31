"""文件系统工具：读、写、改、列、找、搜。

刻意的取舍：
- 读文件带行号。模型改代码时最需要的就是「第几行」，没有行号它就得数，
  数错是高发的低级失败。
- 写文件不自动备份。备份会让「我到底改了什么」变得模糊；改坏了应该靠 git，
  而不是靠 harness 偷偷存副本——这是职责边界。
- 搜索结果按文件聚合，天然限量。无限返回会把上下文一次撑爆。
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from ..errors import ToolArgError, ToolError
from ..util import human_bytes
from .base import Tool, ToolContext

SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv", ".auditloop", "dist", "build"}
MAX_FILE_BYTES = 1_500_000


def _rel(ctx: ToolContext, path: Path) -> str:
    try:
        return path.relative_to(ctx.workspace).as_posix()
    except ValueError:
        return path.as_posix()


def _read_text(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ToolError(f"文件过大（{human_bytes(path.stat().st_size)}），请先分块或改用 grep")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ToolError(f"不是 UTF-8 文本文件：{path.name}") from None


class ReadFile(Tool):
    name = "fs_read"
    description = "读取工作目录内的文本文件，返回带行号的内容。大文件用 offset/limit 分段读。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作目录的路径"},
            "offset": {"type": "integer", "description": "从第几行开始（1 起），默认 1"},
            "limit": {"type": "integer", "description": "最多读多少行，默认 400，上限 800"},
        },
        "required": ["path"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = ctx.resolve(args["path"])
        if not path.is_file():
            raise ToolError(f"文件不存在：{args['path']}")
        text = _read_text(path)
        lines = text.splitlines()
        offset = max(1, int(args.get("offset", 1)))
        limit = min(800, max(1, int(args.get("limit", 400))))
        chunk = lines[offset - 1 : offset - 1 + limit]
        width = len(str(offset + len(chunk)))
        body = "\n".join(f"{i + offset:>{width}}| {line}" for i, line in enumerate(chunk))
        tail = "" if offset - 1 + len(chunk) >= len(lines) else f"\n…（共 {len(lines)} 行，还有 {len(lines) - offset + 1 - len(chunk)} 行）"
        return f"{_rel(ctx, path)}（{len(lines)} 行）\n{body}{tail}"


class WriteFile(Tool):
    name = "fs_write"
    description = "写入（覆盖）一个文本文件，父目录不存在会自动创建。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }
    mutating = True

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = ctx.resolve(args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        content = args["content"]
        path.write_text(content, encoding="utf-8", newline="\n")
        return f"已写入 {_rel(ctx, path)}（{len(content)} 字符，{len(content.splitlines())} 行）"


class EditFile(Tool):
    name = "fs_edit"
    description = (
        "把文件里的 old 精确替换成 new。old 必须在文件中唯一出现，否则报错——"
        "这是为了让模型先看清楚再改，而不是盲改。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old": {"type": "string", "description": "要被替换的原文，必须唯一且完全一致（含缩进）"},
            "new": {"type": "string", "description": "替换后的内容"},
        },
        "required": ["path", "old", "new"],
    }
    mutating = True

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = ctx.resolve(args["path"])
        if not path.is_file():
            raise ToolError(f"文件不存在：{args['path']}")
        text = _read_text(path)
        old, new = args["old"], args["new"]
        hits = text.count(old)
        if hits == 0:
            raise ToolError(f"没找到要替换的内容（{len(old)} 字符）。先用 fs_read 确认原文，注意缩进与空行。")
        if hits > 1:
            raise ToolError(f"要替换的内容出现了 {hits} 次，不唯一。请带上更多上下文让它唯一。")
        path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
        return f"已修改 {_rel(ctx, path)}（替换 1 处，{len(old)} → {len(new)} 字符）"


class ListDir(Tool):
    name = "fs_list"
    description = "列出目录下的文件与子目录。"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "默认 ."}},
        "required": [],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = ctx.resolve(args.get("path") or ".")
        if not path.is_dir():
            raise ToolError(f"不是目录：{args.get('path')}")
        rows = []
        for entry in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if entry.name in SKIP_DIRS:
                continue
            if entry.is_dir():
                rows.append(f"DIR  {entry.name}/")
            else:
                rows.append(f"FILE {entry.name}  ({human_bytes(entry.stat().st_size)})")
        return "\n".join(rows) or "（空目录）"


class Glob(Tool):
    name = "glob"
    description = "按 glob 模式查找文件，例如 **/*.py 或 src/**/test_*.py。返回相对路径与大小。"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 模式，默认 **/*"},
            "max_results": {"type": "integer", "description": "默认 200，上限 1000"},
        },
        "required": [],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        pattern = args.get("pattern") or "**/*"
        limit = min(1000, max(1, int(args.get("max_results", 200))))
        try:
            found = [p for p in ctx.workspace.glob(pattern) if p.is_file()]
        except (ValueError, OSError) as exc:
            raise ToolError(f"glob 模式不合法：{pattern}（{exc}）") from exc
        found = [p for p in found if not (set(p.relative_to(ctx.workspace).parts) & SKIP_DIRS)]
        found.sort()
        if not found:
            return f"没有匹配 {pattern} 的文件"
        rows = [f"{_rel(ctx, p)}  ({human_bytes(p.stat().st_size)})" for p in found[:limit]]
        if len(found) > limit:
            rows.append(f"…（共 {len(found)} 个，只显示前 {limit} 个）")
        return "\n".join(rows)


class Grep(Tool):
    name = "grep"
    description = "在工作目录内按正则搜索文本，返回 文件:行号:内容。适合定位函数、报错、配置。"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python 正则"},
            "include": {"type": "string", "description": "只搜匹配的文件名模式，例如 *.py"},
            "max_matches": {"type": "integer", "description": "默认 100，上限 400"},
        },
        "required": ["pattern"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        try:
            regex = re.compile(args["pattern"])
        except re.error as exc:
            raise ToolArgError(f"正则不合法：{exc}") from exc
        include = args.get("include") or "*"
        limit = min(400, max(1, int(args.get("max_matches", 100))))
        hits: list[str] = []
        scanned = 0
        for root, dirs, files in os.walk(ctx.workspace):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if not fnmatch.fnmatch(name, include):
                    continue
                path = Path(root) / name
                try:
                    if path.stat().st_size > MAX_FILE_BYTES:
                        continue
                    raw = path.read_bytes()
                except OSError:
                    continue
                if b"\x00" in raw[:4096]:
                    continue
                scanned += 1
                for lineno, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
                    if regex.search(line):
                        hits.append(f"{_rel(ctx, path)}:{lineno}: {line.strip()[:200]}")
                        if len(hits) >= limit:
                            return "\n".join(hits) + f"\n…（已达上限 {limit} 条，请缩小范围）"
        if not hits:
            return f"没有匹配 `{args['pattern']}` 的行（扫描了 {scanned} 个文件）"
        return "\n".join(hits)
