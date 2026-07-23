#!/usr/bin/env python3
"""Render asciinema .cast → animated GIF (Tokyo Night terminal)."""
import json
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Tokyo Night palette
BG = (26, 27, 38)
FG = (192, 202, 245)
GREEN = (158, 206, 106)
CYAN = (125, 207, 255)
MAGENTA = (187, 154, 247)
YELLOW = (224, 175, 104)
RED = (247, 118, 142)
COMMENT = (86, 95, 137)
TITLE_BG = (36, 40, 59)

CHAR_W, CHAR_H = 9, 20
PAD_X, PAD_Y = 16, 12
TITLE_H = 36
COLS, ROWS = 80, 20
WIDTH = COLS * CHAR_W + PAD_X * 2
HEIGHT = ROWS * CHAR_H + TITLE_H + PAD_Y * 2


def get_font(size=15):
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf",
    ]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def parse_cast(path: str) -> list[tuple[float, str]]:
    """Parse .cast v2 → list of (time, output_chunk)."""
    lines = Path(path).read_text().strip().split("\n")
    events = []
    for line in lines[1:]:
        try:
            t, event, data = json.loads(line)
            if event == "o":
                events.append((t, data))
        except (json.JSONDecodeError, ValueError):
            continue
    return events


def strip_ansi(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\][^\x07]*\x07", "", text)
    text = re.sub(r"\x1b[()][0-9A-B]", "", text)
    return text.replace("\r\n", "\n").replace("\r", "")


def line_color(line: str) -> tuple:
    if "✓" in line or "✅" in line:
        return GREEN
    if "✗" in line or "❌" in line:
        return RED
    if line.strip().startswith("$"):
        return CYAN
    if line.strip().startswith(("{", '"', "[")):
        return YELLOW
    if "---" in line:
        return MAGENTA
    if "omk-crawl" in line.lower() and "[" in line:
        return CYAN
    return FG


def render_frame(lines: list[str], title: str, font) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # Title bar
    draw.rectangle([0, 0, WIDTH, TITLE_H], fill=TITLE_BG)
    for i, c in enumerate([(247, 118, 142), (224, 175, 104), (158, 206, 106)]):
        draw.ellipse([PAD_X + 4 + i * 22, 10, PAD_X + 18 + i * 22, 24], fill=c)
    draw.text((WIDTH // 2 - len(title) * 3, 10), title, fill=COMMENT, font=font)

    # Content
    visible = lines[-ROWS:]
    for i, line in enumerate(visible):
        y = TITLE_H + PAD_Y + i * CHAR_H
        color = line_color(line)
        draw.text((PAD_X, y), line[:COLS], fill=color, font=font)

    return img


def cast_to_gif(cast_path: str, gif_path: str, title: str, cmd: str):
    events = parse_cast(cast_path)
    font = get_font()

    # Build frames: accumulate output, snapshot at each event
    frames = []
    durations = []
    output = ""

    # Frame 0: just the command
    all_lines = [f"$ {cmd}", ""]
    frames.append(render_frame(all_lines, title, font))
    durations.append(800)

    for i, (t, data) in enumerate(events):
        output += strip_ansi(data)
        current_lines = [f"$ {cmd}", ""] + output.split("\n")
        # Trim trailing empties
        while current_lines and not current_lines[-1].strip():
            current_lines.pop()
        frames.append(render_frame(current_lines, title, font))
        # Duration: time until next event, min 100ms, max 2000ms
        if i + 1 < len(events):
            dt = int((events[i + 1][0] - t) * 1000)
        else:
            dt = 2500  # hold last frame
        durations.append(max(100, min(dt, 2000)))

    # Hold last frame longer
    durations[-1] = 3000

    # Save frames as temp PNGs → ffmpeg → GIF
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, frame in enumerate(frames):
            frame.save(f"{tmpdir}/frame_{i:04d}.png")

        # Write ffmpeg concat file with durations
        concat = f"{tmpdir}/concat.txt"
        with open(concat, "w") as f:
            for i, dur in enumerate(durations):
                f.write(f"file 'frame_{i:04d}.png'\n")
                f.write(f"duration {dur / 1000:.3f}\n")
            # Last frame again (ffmpeg concat demuxer quirk)
            f.write(f"file 'frame_{len(frames) - 1:04d}.png'\n")

        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", concat,
                "-vf", "fps=10,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
                "-loop", "0",
                gif_path,
            ],
            capture_output=True,
        )

    size = Path(gif_path).stat().st_size
    print(f"  ✅ {Path(gif_path).name}: {len(frames)} frames, {size // 1024}KB")


def main():
    demos = [
        ("assets/demos/01.cast", "assets/demos/01-auto-escalation.gif",
         "omk-crawl -v", "omk-crawl https://example.com -v"),
        ("assets/demos/02.cast", "assets/demos/02-tool-discovery.gif",
         "omk-crawl --tools", "omk-crawl --tools"),
        ("assets/demos/03.cast", "assets/demos/03-diagnose.gif",
         "omk-crawl --diagnose", "omk-crawl --diagnose https://example.com"),
        ("assets/demos/04.cast", "assets/demos/04-json-output.gif",
         "omk-crawl --json", "omk-crawl https://example.com --json"),
        ("assets/demos/05.cast", "assets/demos/05-python-api.gif",
         "Python API", "from omk_crawl import crawl; r = crawl('https://example.com')"),
    ]

    for cast, gif, title, cmd in demos:
        if not Path(cast).exists():
            print(f"  SKIP {cast}")
            continue
        cast_to_gif(cast, gif, title, cmd)


if __name__ == "__main__":
    main()
