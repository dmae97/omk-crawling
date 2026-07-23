#!/usr/bin/env python3
"""Render asciinema .cast files to static SVG terminal screenshots."""
import json
import sys
import html as html_mod
from pathlib import Path

BG = "#1a1b26"
FG = "#c0caf5"
GREEN = "#9ece6a"
CYAN = "#7dcfff"
MAGENTA = "#bb9af7"
YELLOW = "#e0af68"
RED = "#f7768e"
TITLE_BG = "#24283b"
FONT = "14px 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace"

def parse_cast(path: str) -> tuple[int, int, list[str]]:
    """Parse .cast v2 → (cols, rows, final_lines)."""
    lines = Path(path).read_text().strip().split("\n")
    header = json.loads(lines[0])
    cols, rows = header.get("width", 80), header.get("height", 24)
    # Accumulate output
    output = ""
    for line in lines[1:]:
        try:
            t, event, data = json.loads(line)
            if event == "o":
                output += data
        except (json.JSONDecodeError, ValueError):
            continue
    # Strip ANSI escape sequences
    import re
    output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", output)
    output = re.sub(r"\x1b\][^\x07]*\x07", "", output)
    output = re.sub(r"\x1b[()][0-9A-B]", "", output)
    output = output.replace("\r\n", "\n").replace("\r", "")
    final_lines = output.split("\n")
    # Trim trailing empty lines
    while final_lines and not final_lines[-1].strip():
        final_lines.pop()
    return cols, rows, final_lines

def colorize(line: str) -> str:
    """Simple syntax coloring for terminal output."""
    escaped = html_mod.escape(line)
    # Color patterns
    if "✓" in line or "✅" in line:
        return f'<tspan fill="{GREEN}">{escaped}</tspan>'
    if "✗" in line or "❌" in line:
        return f'<tspan fill="{RED}">{escaped}</tspan>'
    if line.strip().startswith("$") or line.strip().startswith("python"):
        return f'<tspan fill="{CYAN}">{escaped}</tspan>'
    if line.strip().startswith("{") or line.strip().startswith('"'):
        return f'<tspan fill="{YELLOW}">{escaped}</tspan>'
    if "---" in line:
        return f'<tspan fill="{MAGENTA}">{escaped}</tspan>'
    return f'<tspan fill="{FG}">{escaped}</tspan>'

def render_svg(lines: list[str], title: str, cols: int = 80) -> str:
    """Render lines to an SVG terminal window."""
    line_h = 20
    pad_x, pad_y = 16, 40
    title_h = 32
    width = cols * 8.4 + pad_x * 2
    height = len(lines) * line_h + pad_y + title_h + 16
    
    # Title bar dots
    dots = ""
    for i, color in enumerate(["#f7768e", "#e0af68", "#9ece6a"]):
        dots += f'<circle cx="{pad_x + 8 + i * 20}" cy="{title_h // 2 + 4}" r="6" fill="{color}"/>'
    
    # Title text
    title_text = f'<text x="{width // 2}" y="{title_h // 2 + 9}" text-anchor="middle" fill="#565f89" font-size="12" font-family="{FONT}">{html_mod.escape(title)}</text>'
    
    # Content lines
    text_lines = ""
    for i, line in enumerate(lines):
        y = title_h + pad_y + i * line_h
        colored = colorize(line[:cols])
        text_lines += f'<text x="{pad_x}" y="{y}" font-size="13" font-family="{FONT}">{colored}</text>\n'
    
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}">
  <rect width="100%" height="100%" rx="10" fill="{BG}"/>
  <rect width="100%" height="{title_h}" rx="10" fill="{TITLE_BG}"/>
  <rect y="{title_h - 10}" width="100%" height="10" fill="{TITLE_BG}"/>
  {dots}
  {title_text}
  {text_lines}
</svg>'''

def main():
    demos = [
        ("assets/demos/01.cast", "01-auto-escalation.svg", "omk-crawl https://example.com -v"),
        ("assets/demos/02.cast", "02-tool-discovery.svg", "omk-crawl --tools"),
        ("assets/demos/03.cast", "03-diagnose.svg", "omk-crawl --diagnose https://example.com"),
        ("assets/demos/04.cast", "04-json-output.svg", "omk-crawl https://example.com --json"),
        ("assets/demos/05.cast", "05-python-api.svg", "python3 -c \"from omk_crawl import crawl; ...\""),
    ]
    
    for cast_path, svg_name, title in demos:
        if not Path(cast_path).exists():
            print(f"  SKIP {cast_path} (not found)")
            continue
        cols, rows, lines = parse_cast(cast_path)
        # Add the command as first line
        cmd_line = f"$ {title}"
        all_lines = [cmd_line, ""] + lines
        svg = render_svg(all_lines, title, cols)
        out = Path("assets/demos") / svg_name
        out.write_text(svg)
        print(f"  ✅ {svg_name}: {len(all_lines)} lines, {len(svg)} bytes")

if __name__ == "__main__":
    main()
