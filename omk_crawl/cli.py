"""CLI — omk-crawl <url> [options]

Usage:
    omk-crawl https://example.com                    # auto-escalate
    omk-crawl https://example.com --tool curl_cffi   # force tool
    omk-crawl https://example.com -o out.md          # save markdown
    omk-crawl https://example.com --json             # JSON output
    omk-crawl https://example.com -v                 # verbose escalation
    omk-crawl --diagnose https://example.com         # dry-run: what would we try?
    omk-crawl --tools                                # list installed tools
    omk-crawl report.pdf                             # file → markdown (markitdown)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from omk_crawl.detect import available_tools, missing_tools
from omk_crawl.result import CrawlResult
from omk_crawl.router import SmartRouter, crawl


def _print_result(r: CrawlResult, *, as_json: bool = False, output: str | None = None) -> None:
    if as_json:
        data = {
            "url": r.url,
            "status": r.status.value,
            "status_code": r.status_code,
            "tool": r.tool,
            "elapsed_ms": round(r.elapsed_ms, 1),
            "content_length": len(r.content) if r.content else 0,
            "markdown": r.markdown,
            "fit_markdown": r.fit_markdown,
            "extracted": r.extracted,
            "error": r.error,
            "metadata": r.metadata,
        }
        text = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        text = r.content or r.error or "(empty)"

    if output:
        Path(output).write_text(text, encoding="utf-8")
        print(f"Saved {len(text)} chars → {output}")
    else:
        print(text)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="omk-crawl",
        description="Smart web crawling toolbox — auto-routes across 10 tools",
        epilog=(
            "This product uses Crawl4AI"
            " (https://github.com/unclecode/crawl4ai) for web data extraction."
        ),
    )
    parser.add_argument("url", nargs="?", help="URL or file path to crawl/convert")
    parser.add_argument("--tool", "-t", help="Force a specific tool (skip auto-escalation)")
    parser.add_argument("--output", "-o", help="Save output to file")
    parser.add_argument("--json", "-j", action="store_true", help="JSON output")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose escalation log")
    parser.add_argument(
        "--diagnose", action="store_true",
        help="Dry-run: show what tools would be tried",
    )
    parser.add_argument("--tools", action="store_true", help="List installed/missing tools")
    parser.add_argument("--version", action="version", version="omk-crawl 2.0.0")

    args = parser.parse_args(argv)

    if args.tools:
        avail = available_tools()
        miss = missing_tools()
        print("Installed tools:")
        for t in avail:
            print(f"  ✅ {t}")
        if miss:
            print("\nMissing tools:")
            for t in miss:
                print(f"  ❌ {t}")
            print("\nInstall all: pip install omk-crawl[all]")
        return

    if not args.url:
        parser.print_help()
        sys.exit(1)

    if args.diagnose:
        router = SmartRouter(verbose=True)
        info = router.diagnose(args.url)
        print(json.dumps(info, indent=2))
        return

    # File path → markitdown
    if Path(args.url).is_file():
        from omk_crawl.tools.markitdown_tool import MarkitdownTool

        tool = MarkitdownTool()
        r = tool.fetch(args.url)
        _print_result(r, as_json=args.json, output=args.output)
        sys.exit(0 if r.ok else 1)

    # Web crawl with auto-escalation
    r = crawl(args.url, tool=args.tool, verbose=args.verbose)

    if args.verbose:
        print(f"\n--- {r.summary()} ---\n", file=sys.stderr)

    _print_result(r, as_json=args.json, output=args.output)
    sys.exit(0 if r.ok else 1)


if __name__ == "__main__":
    main()
