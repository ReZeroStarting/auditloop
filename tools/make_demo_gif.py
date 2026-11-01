"""把 `auditloop demo --contrast` 的真实输出渲染成动画 GIF。

这不是屏幕录制：没有桌面捕获，也没有模拟键盘。做法是把**真实命令输出逐行渲染**成帧，
再拼成 GIF。所以文字是真的、顺序是真的，但"打字"效果是合成的。
README 里会注明这一点，不冒充录屏。

用法：
    auditloop demo --contrast > demo.txt
    python tools/make_demo_gif.py demo.txt docs/assets/demo-contrast.gif
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 940, 404
MARGIN = 26
TITLE_H = 34
LINE_H = 22
FPS = 12
REVEAL_FRAMES = 3          # 每行停留 3 帧 ≈ 0.25s
HOLD_FRAMES = 22           # 结尾停 1.8s
BG = (11, 14, 19)
PANEL = (17, 22, 30)
BORDER = (31, 39, 51)
TEXT = (214, 222, 232)
DIM = (125, 138, 153)
WARN = (210, 153, 34)
OK = (63, 185, 80)
ACCENT = (91, 157, 255)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\consola.ttf",
]


def pick_font(size: int = 15) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def color_for(line: str) -> tuple[int, int, int]:
    if line.startswith(("A ·", "B ·")):
        return ACCENT
    if "unverified" in line:
        return WARN
    if line.strip().startswith("状态") and "ok" in line:
        return OK
    if line.startswith(("结论", "      B", "      A")):
        return DIM
    if line.startswith("---"):
        return BORDER
    return TEXT


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    if not text:
        return [""]
    out, current = [], ""
    for ch in text:
        if draw.textlength(current + ch, font=font) <= max_width:
            current += ch
        else:
            out.append(current)
            current = ch
    out.append(current)
    return out


def build_frames(lines: list[str]) -> list[Image.Image]:
    font = pick_font(15)
    prompt_font = pick_font(14)
    probe = Image.new("RGB", (10, 10))
    measure = ImageDraw.Draw(probe)
    inner_width = WIDTH - 2 * MARGIN - 24

    rendered: list[tuple[str, tuple[int, int, int]]] = []
    for raw in lines:
        for piece in wrap(measure, raw, font, inner_width):
            rendered.append((piece, color_for(raw)))

    command = "PS> auditloop demo --contrast"
    frames: list[Image.Image] = []

    def draw_frame(visible: int, typed: int, cursor_on: bool) -> Image.Image:
        img = Image.new("RGB", (WIDTH, HEIGHT), BG)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([MARGIN - 10, MARGIN - 10, WIDTH - MARGIN + 10, HEIGHT - MARGIN + 10],
                            radius=12, fill=PANEL, outline=BORDER, width=1)
        for i, color in enumerate([(248, 81, 73), (210, 153, 34), (63, 185, 80)]):
            cx = MARGIN + 6 + i * 18
            d.ellipse([cx, MARGIN + 4, cx + 10, MARGIN + 14], fill=color)

        y = MARGIN + TITLE_H
        d.text((MARGIN + 6, y), command[:typed], font=prompt_font, fill=DIM)
        y += LINE_H + 6

        for i, (text, color) in enumerate(rendered):
            if i >= visible:
                break
            if y > HEIGHT - MARGIN - LINE_H:
                break
            d.text((MARGIN + 6, y), text, font=font, fill=color)
            y += LINE_H

        if cursor_on and visible >= len(rendered):
            d.rectangle([MARGIN + 6, y + 2, MARGIN + 14, y + LINE_H - 4], fill=ACCENT)
        return img

    # 1) 命令逐字出现
    for typed in range(3, len(command) + 1, 4):
        frames.append(draw_frame(0, typed, True))
    # 2) 输出逐行出现
    for i in range(1, len(rendered) + 1):
        for _ in range(REVEAL_FRAMES):
            frames.append(draw_frame(i, len(command), True))
    # 3) 结尾停留 + 光标闪烁
    for i in range(HOLD_FRAMES):
        frames.append(draw_frame(len(rendered), len(command), i % 8 < 4))
    return frames


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    source, target = Path(argv[0]), Path(argv[1])
    raw = source.read_text(encoding="utf-8", errors="replace")
    lines = [line.rstrip() for line in raw.splitlines()]

    # 只保留对照块：从 "A ·" 开始
    start = next((i for i, line in enumerate(lines) if line.startswith("A ·")), 0)
    # 去掉带本机路径的收尾行：GIF 会公开在 README 里
    lines = [line for line in lines[start:] if line.strip() and not line.startswith("两份账本")]
    if not lines:
        print("没有可渲染的内容")
        return 1

    frames = build_frames(lines)
    target.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        target,
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / FPS),
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(f"wrote {target} ({target.stat().st_size / 1024:.0f} KB, {len(frames)} frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
