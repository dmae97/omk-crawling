#!/usr/bin/env python3
"""Print a promo post ready to paste into Reddit.

  python promo/reddit/print_post.py 01
  python promo/reddit/print_post.py sideproject
  python promo/reddit/print_post.py --list
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POSTS = ROOT / "posts"
IMAGES = ROOT / "images"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("key", nargs="?", help="01 / webscraping / sideproject …")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    files = sorted(POSTS.glob("*.md"))
    files = [f for f in files if f.name != "INDEX.md"]
    if args.list or not args.key:
        for f in files:
            print(f.name)
        return
    key = args.key.lower().removesuffix(".md")
    match = None
    for f in files:
        stem = f.stem.lower()
        if key == stem or key in stem or stem.startswith(key) or key in stem.replace("_", " "):
            match = f
            break
    if match is None:
        print(f"no post matched {args.key!r}", file=sys.stderr)
        sys.exit(1)
    text = match.read_text(encoding="utf-8")
    print("=" * 60)
    print(match.name)
    print("=" * 60)
    # image hint
    m = re.search(r"`(images/[^`]+)`", text)
    if m:
        img = ROOT / m.group(1)
        print(f"IMAGE: {img}  exists={img.is_file()}")
    print()
    print(text)


if __name__ == "__main__":
    main()
