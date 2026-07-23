#!/usr/bin/env python3
"""Benchmark harness — measure the router against the site set in sites.yaml.

Records per site: success@1 (first tool), success@final (any tool), p50/p95
latency over N runs, content bytes, the tool path actually tried, and a cost
proxy (how many browser/LLM-tier tools were invoked — escalation is expensive).

Modes:
  --mock        deterministic synthetic results (CI; no network)
  (default)     live crawl. Combine with --category / --live-only to stay polite.

Examples:
  python scripts/bench.py --mock                       # CI
  python scripts/bench.py --category static,js --live-only --runs 2
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from omk_crawl.router import SmartRouter  # noqa: E402

BENCH_DIR = ROOT / "benchmarks"
SITES_YAML = BENCH_DIR / "sites.yaml"
LATEST_JSON = BENCH_DIR / "latest.json"

# Tools that spend real money/resources (browser render or LLM).
HEAVY_TOOLS = {"crawl4ai", "scrapling", "browser_use"}


def load_sites(category: str | None, live_only: bool, limit: int | None):
    import yaml

    data = yaml.safe_load(SITES_YAML.read_text(encoding="utf-8"))
    sites = data["sites"]
    if category:
        wanted = {c.strip() for c in category.split(",")}
        sites = [s for s in sites if s["category"] in wanted]
    if live_only:
        sites = [s for s in sites if s.get("live")]
    if limit:
        sites = sites[:limit]
    return sites


def _mock_result(site: dict, rng: random.Random) -> dict:
    """Deterministic synthetic outcome by category (for CI)."""
    cat = site["category"]
    base = {"static": 120, "js": 900, "soft-wall": 600, "hard-wall": 2500}[cat]
    latency = base * rng.uniform(0.8, 1.3)
    if cat == "static":
        path, ok1, okf = ["curl_cffi"], True, True
    elif cat == "js":
        path, ok1, okf = ["curl_cffi", "crawl4ai"], False, True
    elif cat == "soft-wall":
        path, ok1, okf = ["curl_cffi", "scrapling"], False, rng.random() > 0.2
    else:  # hard-wall
        path, ok1, okf = ["curl_cffi", "scrapling", "browser_use"], False, rng.random() > 0.5
    return {
        "success_at_1": ok1, "success_at_final": okf,
        "latency_ms": latency, "bytes": rng.randint(2000, 80000) if okf else 0,
        "tool_path": path, "cost_proxy": sum(1 for t in path if t in HEAVY_TOOLS),
    }


def _live_result(site: dict) -> dict:
    router = SmartRouter(respect_robots=True, min_delay=1.0, verbose=False)
    t0 = time.perf_counter()
    result = router.crawl(site["url"])
    latency = (time.perf_counter() - t0) * 1000
    path = [d.tool for d in router.decisions]
    history = router.history
    return {
        "success_at_1": bool(history and history[0].ok),
        "success_at_final": bool(result.ok),
        "latency_ms": latency,
        "bytes": len(result.content or ""),
        "tool_path": path,
        "cost_proxy": sum(1 for h in history if h.tool in HEAVY_TOOLS),
        "status": result.status.value,
    }


def _percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    vals = sorted(vals)
    k = (len(vals) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def bench_site(site: dict, runs: int, mock: bool, rng: random.Random) -> dict:
    samples = [_mock_result(site, rng) if mock else _live_result(site) for _ in range(runs)]
    lats = [s["latency_ms"] for s in samples]
    last = samples[-1]
    return {
        "name": site["name"],
        "category": site["category"],
        "url": site["url"],
        "success_at_1": last["success_at_1"],
        "success_at_final": last["success_at_final"],
        "p50_ms": round(_percentile(lats, 0.5), 1),
        "p95_ms": round(_percentile(lats, 0.95), 1),
        "bytes": last["bytes"],
        "tool_path": last["tool_path"],
        "cost_proxy": last["cost_proxy"],
    }


def to_markdown(rows: list[dict]) -> str:
    lines = [
        "| site | category | ok@1 | ok@final | p50 ms | p95 ms | KB | tool path | cost |",
        "|------|----------|:----:|:--------:|-------:|-------:|---:|-----------|:----:|",
    ]
    for r in rows:
        ok1 = "✓" if r["success_at_1"] else "✗"
        okf = "✓" if r["success_at_final"] else "✗"
        path = "→".join(r["tool_path"]) or "—"
        lines.append(
            f"| {r['name']} | {r['category']} | {ok1} | {okf} | {r['p50_ms']} | "
            f"{r['p95_ms']} | {r['bytes'] // 1024} | {path} | {r['cost_proxy']} |"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--category", help="comma-separated categories to include")
    ap.add_argument("--live-only", action="store_true", help="only scrape-friendly sites")
    ap.add_argument("--limit", type=int, help="max sites")
    ap.add_argument("--runs", type=int, default=1, help="runs per site (for p50/p95)")
    ap.add_argument("--mock", action="store_true", help="synthetic results (CI)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=str(LATEST_JSON))
    args = ap.parse_args()

    sites = load_sites(args.category, args.live_only, args.limit)
    if not sites:
        print("no sites matched", file=sys.stderr)
        sys.exit(1)

    rng = random.Random(args.seed)
    mode = "mock" if args.mock else "live"
    print(f"# benchmark ({mode}, {len(sites)} sites, {args.runs} run(s) each)\n")

    rows = [bench_site(s, args.runs, args.mock, rng) for s in sites]

    out = {
        "mode": mode, "runs": args.runs,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "results": rows,
    }
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(to_markdown(rows))
    n = len(rows)
    s1 = sum(r["success_at_1"] for r in rows)
    sf = sum(r["success_at_final"] for r in rows)
    print(f"\nsuccess@1 {s1}/{n} · success@final {sf}/{n} · wrote {args.out}")


if __name__ == "__main__":
    main()
