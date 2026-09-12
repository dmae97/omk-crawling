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
    omk-crawl capture.har --json                     # offline web/app API inventory
    omk-crawl capture.har --json --har-bodies        # opt in to JSON response bodies
    omk-crawl app.apk                                # Android package surface
    omk-crawl app.ipa                                # iOS package surface
    omk-crawl android://                             # adb device list
    omk-crawl android://SERIAL/packages              # 3rd-party packages
    omk-crawl android://SERIAL/dump?pkg=com.app      # dumpsys package
    omk-crawl baemin://36.83,127.13                  # Baemin shops near geo
    omk-crawl 'baemin://shops?lat=36.83&lng=127.13&limit=40'
    omk-crawl 'appstore://search?q=요기요'           # iOS App Store meta
    omk-crawl appstore://378084485                   # lookup by trackId
    omk-crawl ios://Freeform                         # iOS-only style search
    omk-crawl reddit://r/programming                 # Reddit listing JSON
    omk-crawl 'reddit://search?q=rust+async&limit=10'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from omk_crawl import __version__, star
from omk_crawl.detect import available_tools, missing_tools
from omk_crawl.result import CrawlResult
from omk_crawl.router import SmartRouter


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


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="omk-crawl",
        description=("Smart crawling toolbox — web auto-escalation + Android/iOS surfaces"),
        epilog=(
            "Web: curl_cffi → crawl4ai → scrapling → browser-use. "
            "Mobile: apk / ipa / android:// (adb). "
            "Crawl4AI: https://github.com/unclecode/crawl4ai"
        ),
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="URL, file path, .har/.apk/.ipa, or android:// scheme",
    )
    parser.add_argument("--tool", "-t", help="Force a specific tool (skip auto-escalation)")
    parser.add_argument("--output", "-o", help="Save output to file")
    parser.add_argument("--json", "-j", action="store_true", help="JSON output")
    parser.add_argument(
        "--har-bodies",
        action="store_true",
        help="Include HAR JSON response bodies (may contain sensitive data; use with --json)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose escalation log")
    parser.add_argument(
        "--no-robots",
        action="store_true",
        help="Skip robots.txt check (use responsibly)",
    )
    parser.add_argument(
        "--min-delay",
        type=float,
        default=0.5,
        help="Minimum seconds between requests to same domain (default: 0.5)",
    )
    parser.add_argument("--timeout", type=float, help="Per-adapter timeout upper bound in seconds")
    parser.add_argument(
        "--total-timeout",
        type=float,
        default=120.0,
        help="Shared cooperative deadline in seconds (default: 120)",
    )
    parser.add_argument(
        "--max-attempts", type=int, default=8, help="Maximum adapters to try (default: 8)"
    )
    parser.add_argument("--max-fetches", type=int, help="Maximum adapter calls, including retries")
    parser.add_argument(
        "--max-retries", type=int, default=1, help="Retries per adapter (default: 1)"
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Disable browser adapters and fallback"
    )
    parser.add_argument(
        "--allow-llm",
        action="store_true",
        help="Opt into LLM-backed adapters; provider charges may apply",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Dry-run: show what tools would be tried",
    )
    parser.add_argument("--tools", action="store_true", help="List installed/missing tools")
    parser.add_argument(
        "--star",
        action="store_true",
        help=f"Star {star.REPO} on GitHub (uses gh CLI if authenticated, else opens browser)",
    )
    parser.add_argument("--version", action="version", version=f"omk-crawl {__version__}")
    return parser


def _main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.star:
        star.star_now()
        star.save_state({**star.load_state(), "starred": True, "prompted": True})
        return 0

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
        return 0

    if not args.url:
        parser.print_help()
        return 1

    target = args.url
    try:
        router = SmartRouter(
            tools=[args.tool] if args.tool else None,
            verbose=args.verbose,
            respect_robots=not args.no_robots,
            min_delay=args.min_delay,
            total_timeout=args.total_timeout,
            max_attempts=args.max_attempts,
            max_fetches=args.max_fetches,
            max_retries=args.max_retries,
            allow_browser=not args.no_browser,
            allow_llm=args.allow_llm,
        )
    except (ValueError, TypeError) as error:
        parser.error(str(error))
    tool_kwargs = {}
    if args.timeout is not None:
        tool_kwargs["timeout"] = args.timeout
    if args.har_bodies:
        target_tool = args.tool or router.diagnose(target)["target_tool"]
        if target_tool != "har" or not args.json:
            parser.error("--har-bodies requires a local HAR input (or --tool har) and --json")
        tool_kwargs["include_bodies"] = True
    if args.diagnose:
        print(json.dumps(router.diagnose(target, **tool_kwargs), indent=2))
        return 0

    if args.verbose:
        import logging

        logging.basicConfig(
            level=logging.INFO,
            format="  [omk-crawl] %(message)s",
        )

    r = router.crawl(target, **tool_kwargs)

    if args.verbose:
        print(f"\n--- {r.summary()} ---\n", file=sys.stderr)

    _print_result(r, as_json=args.json, output=args.output)
    return 0 if r.ok else 1


def main(argv: list[str] | None = None) -> None:
    """Console entry point: run, nudge for a star, then propagate the exit code."""
    code = _main(argv)
    star.after_run(success=code == 0)
    if code != 0:
        sys.exit(code)


if __name__ == "__main__":
    main()
