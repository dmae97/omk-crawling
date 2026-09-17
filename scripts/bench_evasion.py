#!/usr/bin/env python3
"""Offline benchmark for the deep evasion layers (v2.14).

Answers three questions with numbers instead of adjectives:

  1. **Does the coherent plan beat the alternatives?** Three strategies are
     scored by the offline mock detector (:mod:`omk_crawl.verify`): a plain
     library client, the "browser headers glued onto a non-browser stack"
     mistake the research warns about, and :func:`omk_crawl.evasion.plan_for`.
  2. **What does the plan cost?** Wall-clock p50/p95 for building a plan and
     the size of the init script it produces — the parts of this layer that run
     on every request.
  3. **Is it reproducible?** The same input must produce byte-identical output,
     because a fingerprint that drifts between sessions is itself the signal.

Everything runs offline and deterministically; no site is contacted.

Usage:
    python3 scripts/bench_evasion.py                 # human-readable table
    python3 scripts/bench_evasion.py --json          # machine-readable
    python3 scripts/bench_evasion.py --write         # also refresh benchmarks/evasion/latest.json
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from omk_crawl import __version__  # noqa: E402
from omk_crawl.evasion import plan_for  # noqa: E402
from omk_crawl.verify import (  # noqa: E402
    CHECKS,
    evasion_surface,
    naive_stealth_surface,
    stock_surface,
)
from omk_crawl.verify import score as _score  # noqa: E402  (clarity at call sites)

OUT_DIR = ROOT / "benchmarks" / "evasion"

# A spread of real-looking targets. Diversity matters: the point of per-site
# identities is that a fleet does not collapse into one machine.
SITES: tuple[str, ...] = (
    "https://example.com",
    "https://news.ycombinator.com",
    "https://en.wikipedia.org",
    "https://github.com",
    "https://www.bbc.co.uk",
    "https://www.rakuten.co.jp",
    "https://www.naver.com",
    "https://www.amazon.com",
    "https://stackoverflow.com",
    "https://www.reddit.com",
    "https://medium.com",
    "https://www.nytimes.com",
)

STRATEGY_LABELS = ("stock", "naive_stealth", "omk_evasion")


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile; stable for the small samples used here."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _time_plans(url: str, runs: int) -> list[float]:
    """Per-run plan latency in milliseconds (plan_for is pure, so this is CPU cost)."""
    samples: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        plan_for(url)
        samples.append((time.perf_counter() - start) * 1000.0)
    return samples


def _determinism_check(url: str) -> dict[str, Any]:
    """The same URL must yield byte-identical layers, twice and across processes."""
    first, second = plan_for(url), plan_for(url)
    return {
        "script_identical": first.init_script() == second.init_script(),
        "seed_identical": first.seed == second.seed,
        "profile_identical": first.profile.name == second.profile.name,
        "ja3_identical": first.hello.ja3() == second.hello.ja3(),
    }


def _fleet_diversity(sites: tuple[str, ...]) -> dict[str, Any]:
    """How distinct is the identity space across a fleet of targets?"""
    plans = [plan_for(site) for site in sites]
    profiles = {plan.profile.name for plan in plans}
    stacks = {plan.tcp.name for plan in plans}
    ja3s = {plan.hello.ja3() for plan in plans}
    scripts = {plan.init_script() for plan in plans}
    return {
        "sites": len(sites),
        "distinct_profiles": len(profiles),
        "distinct_tcp_stacks": len(stacks),
        "distinct_ja3": len(ja3s),
        "distinct_init_scripts": len(scripts),
    }


def _strategy_result(name: str) -> dict[str, Any]:
    """Score one strategy and record what failed, with reasons."""
    surface = {
        "stock": stock_surface,
        "naive_stealth": naive_stealth_surface,
        "omk_evasion": evasion_surface,
    }[name]()
    verdict = _score(surface)
    return {
        "strategy": name,
        "score": round(verdict.score, 4),
        "detected": list(verdict.detected),
        "passed": list(verdict.passed),
        "unscored": list(verdict.unscored),
        "failures_by_check": {k: v for k, v in verdict.details.items()},
    }


def run(runs: int = 40, sites: tuple[str, ...] = SITES) -> dict[str, Any]:
    """Execute the whole benchmark and return a JSON-safe report."""
    results = [_strategy_result(name) for name in STRATEGY_LABELS]

    latencies: list[float] = []
    for site in sites:
        latencies.extend(_time_plans(site, max(1, runs // len(sites))))

    determinism = {site: _determinism_check(site) for site in sites[:5]}
    coherent = sum(1 for site in sites if plan_for(site).coherence_report().ok)

    plan = plan_for(sites[0])
    report: dict[str, Any] = {
        "benchmark": "evasion",
        "version": __version__,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": platform.python_version(),
        "mode": "offline",
        "runs": runs,
        "checks": [
            {"key": c.key, "weight": c.weight, "description": c.description} for c in CHECKS
        ],
        "strategies": results,
        "ranking": [r["strategy"] for r in sorted(results, key=lambda r: -r["score"])],
        "plan_cost": {
            "p50_ms": round(_percentile(latencies, 0.50), 4),
            "p95_ms": round(_percentile(latencies, 0.95), 4),
            "max_ms": round(max(latencies), 4) if latencies else 0.0,
            "samples": len(latencies),
        },
        "artifacts": {
            "init_script_bytes": len(plan.init_script()),
            "patch_fragments": len(plan.cdp.patches),
            "cdp_tells_covered": len(plan.cdp.covered_tells()),
            "cdp_tells_driver_fixed": len(plan.cdp.driver_fixed),
        },
        "coherence": {"self_check_passed": coherent, "sites": len(sites)},
        "determinism": {
            "all_identical": all(
                all(check.values()) if isinstance(check, dict) else check
                for check in determinism.values()
            ),
            "detail": determinism,
        },
        "fleet_diversity": _fleet_diversity(sites),
    }

    # The headline claim, computed rather than asserted.
    by_name = {r["strategy"]: r["score"] for r in results}
    report["findings"] = {
        "coherent_beats_naive_by": round(by_name["omk_evasion"] - by_name["naive_stealth"], 4),
        "naive_is_worse_than_stock": by_name["naive_stealth"] < by_name["stock"],
        "omk_evasion_is_clean": by_name["omk_evasion"] == 1.0,
    }
    # Stated the way AdaptOrch states its own orchestration value: a result from a
    # local model is not a correctness proof. The detector, the evasion plan and
    # this benchmark share one author, so the ranking is a *consistency check*
    # within one design, not independent evidence about what a real vendor would
    # decide. Recording that here keeps the artifact from being quoted as if it
    # were a measured detection rate.
    report["claim_boundary"] = {
        "detector_is_independent_oracle": False,
        "is_real_vendor_detection_rate": False,
        "measured_against": "offline mock detector (omk_crawl.verify)",
        "note": (
            "Only a real anti-bot vendor's decision on a real request would confirm "
            "that the ordering holds in production. Nothing here was sent to a "
            "protected site."
        ),
    }
    return report


def _render(report: dict[str, Any]) -> str:
    """Human-readable rendering of the report."""
    lines: list[str] = []
    lines.append(f"omk-crawl evasion benchmark  v{report['version']}  (offline)")
    lines.append("")
    lines.append(f"{'strategy':<16} {'score':>7}  detected")
    lines.append("-" * 72)
    for result in report["strategies"]:
        detected = ", ".join(result["detected"]) or "-"
        lines.append(f"{result['strategy']:<16} {result['score']:>7.3f}  {detected}")
    lines.append("")
    lines.append(f"ranking            {' > '.join(report['ranking'])}")
    lines.append(
        f"plan cost          p50={report['plan_cost']['p50_ms']}ms "
        f"p95={report['plan_cost']['p95_ms']}ms "
        f"(n={report['plan_cost']['samples']})"
    )
    lines.append(
        f"artifacts          init script {report['artifacts']['init_script_bytes']}B, "
        f"{report['artifacts']['patch_fragments']} fragments, "
        f"{report['artifacts']['cdp_tells_covered']} tells covered, "
        f"{report['artifacts']['cdp_tells_driver_fixed']} driver-fixed"
    )
    lines.append(
        f"coherence          {report['coherence']['self_check_passed']}"
        f"/{report['coherence']['sites']} sites pass the self-check"
    )
    lines.append(f"determinism        {report['determinism']['all_identical']}")
    diversity = report["fleet_diversity"]
    lines.append(
        f"fleet diversity    {diversity['distinct_profiles']} profiles, "
        f"{diversity['distinct_tcp_stacks']} TCP stacks, "
        f"{diversity['distinct_ja3']} JA3, "
        f"{diversity['distinct_init_scripts']} init scripts "
        f"across {diversity['sites']} sites"
    )
    lines.append("")
    findings = report["findings"]
    lines.append("findings")
    lines.append(
        f"  coherent plan beats glued-on stealth by {findings['coherent_beats_naive_by']:+.3f}"
    )
    lines.append(
        f"  naive stealth is worse than an honest client: "
        f"{findings['naive_is_worse_than_stock']}"
    )
    lines.append(f"  coherent plan sweeps every check: {findings['omk_evasion_is_clean']}")
    boundary = report["claim_boundary"]
    lines.append("")
    lines.append(
        f"claim boundary     independent oracle: {boundary['detector_is_independent_oracle']} | "
        f"real vendor detection rate: {boundary['is_real_vendor_detection_rate']}"
    )
    lines.append(f"                   {boundary['note']}")
    lines.append("")
    lines.append("per-check failures by strategy")
    for result in report["strategies"]:
        if not result["failures_by_check"]:
            continue
        for key, issues in result["failures_by_check"].items():
            lines.append(f"  {result['strategy']:<14} {key}: {issues[0]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=40, help="plan-build samples (default 40)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument(
        "--write", action="store_true", help="refresh benchmarks/evasion/latest.json"
    )
    args = parser.parse_args(argv)

    report = run(runs=args.runs)
    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        target = OUT_DIR / "latest.json"
        target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {target.relative_to(ROOT)}", file=sys.stderr)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(_render(report))

    # Exit non-zero when the headline property regresses, so CI can gate on it.
    findings = report["findings"]
    return 0 if (findings["naive_is_worse_than_stock"] and findings["omk_evasion_is_clean"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
