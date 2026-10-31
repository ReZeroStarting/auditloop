"""把导出的 run JSON 打包成原型能直接吃的 JS 数据。

为什么要这一步：`file://` 下浏览器会拦 fetch（跨源），双击 HTML 就打不开账本。
用 <script> 加载一个静态 JS 是唯一稳的做法。

    python prototype/make_data.py
    # 读 prototype/data/*.json → 写 prototype/data/runs.js
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

SOURCES = [
    ("01-ok.json", "ok", "完成", "demo：修一个 bug，验证通过"),
    ("02-unverified.json", "unverified", "未验证", "模型说做完了，但验证命令 rc=1"),
    ("03-blocked.json", "policy_blocked", "被拦停", "连续 3 次撞策略，主动停手"),
]


def main() -> int:
    runs = []
    for filename, key, label, note in SOURCES:
        path = DATA / filename
        if not path.is_file():
            raise SystemExit(f"缺少数据文件：{path}\n先跑 auditloop trace export 生成它")
        payload = json.loads(path.read_text(encoding="utf-8"))
        runs.append({"key": key, "label": label, "note": note, "data": payload})

    out = DATA / "runs.js"
    body = "// 由 prototype/make_data.py 生成，请勿手改。\n"
    body += "window.AUDITLOOP_RUNS = " + json.dumps(runs, ensure_ascii=False, separators=(",", ":")) + ";\n"
    out.write_text(body, encoding="utf-8", newline="\n")
    print(f"wrote {out} ({out.stat().st_size} bytes, {len(runs)} runs)")
    for item in runs:
        summary = item["data"]["summary"]
        print(f"  {item['key']:<14} {summary['status']:<14} events={summary['events']:<3} steps={summary['steps']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
