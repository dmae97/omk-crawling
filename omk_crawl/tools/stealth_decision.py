#!/usr/bin/env python3
"""
Bayesian Stealth Decision Engine v1.0.

Warm-start strategy selection for exploit chains. Uses multi-armed bandit
(Thompson sampling) to pick which attack vector to run next, factoring in:
  - Proxy pool health (availability, latency)
  - Success history per vector
  - Backtracking risk score (IP diversity, timing entropy)
  - Current target anti-bot posture

Output: ordered attack plan with fallback chain.
"""

from __future__ import annotations

import json
import random
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

STATE_PATH = Path("/home/yu/projects/omk-crawling/.crawl_cache/bandit_state.json")


@dataclass
class VectorStats:
    """Per-vector bandit stats for Thompson sampling."""

    successes: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0
    last_success_time: float = 0.0
    backtrack_risk: float = 0.0  # 0=min, 1=max detection risk

    @property
    def alpha(self) -> float:
        """Beta distribution alpha = successes + 1 (prior strength)."""
        return self.successes + 1.0

    @property
    def beta(self) -> float:
        """Beta distribution beta = failures + 1."""
        return self.failures + 1.0

    @property
    def success_rate(self) -> float:
        total = self.successes + self.failures
        return self.successes / max(total, 1)

    @property
    def avg_latency_ms(self) -> float:
        total = self.successes + self.failures
        return self.total_latency_ms / max(total, 1)


@dataclass
class StealthDecision:
    """A single attack decision."""

    vector: str
    priority: int  # 0 = highest
    estimated_success_prob: float
    risk_score: float
    reason: str


class StealthDecisionEngine:
    """Bayesian decision engine for exploit chain ordering."""

    def __init__(self):
        self._vectors: dict[str, VectorStats] = {
            "prompt_injection": VectorStats(),
            "storage_bomb": VectorStats(),
            "data_exfiltration": VectorStats(),
            "recon_passive": VectorStats(successes=1),  # OSINT already succeeded
        }
        self._proxy_health: dict[str, float] = {}
        self._load_state()

    def _load_state(self) -> None:
        if STATE_PATH.exists():
            # Corrupt state file → keep freshly-initialised vectors.
            with suppress(Exception):
                data = json.loads(STATE_PATH.read_text())
                for k, v in data.get("vectors", {}).items():
                    if k in self._vectors:
                        self._vectors[k] = VectorStats(**v)

    def save_state(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "vectors": {
                k: {
                    "successes": v.successes,
                    "failures": v.failures,
                    "total_latency_ms": v.total_latency_ms,
                    "last_success_time": v.last_success_time,
                    "backtrack_risk": v.backtrack_risk,
                }
                for k, v in self._vectors.items()
            },
            "last_updated": time.time(),
        }
        STATE_PATH.write_text(json.dumps(data, indent=2))

    def record(self, vector: str, success: bool, latency_ms: float = 0.0) -> None:
        """Record a vector outcome."""
        if vector not in self._vectors:
            self._vectors[vector] = VectorStats()
        vs = self._vectors[vector]
        if success:
            vs.successes += 1
            vs.last_success_time = time.time()
        else:
            vs.failures += 1
        vs.total_latency_ms += latency_ms

        # Adjust backtrack risk
        vs.backtrack_risk = self._compute_backtrack_risk(vector, success)

    def _compute_backtrack_risk(self, vector: str, success: bool) -> float:
        """Compute backtracking detection risk for a vector."""
        base_risk = {
            "prompt_injection": 0.3,  # low (API call, no state change visible)
            "storage_bomb": 0.5,  # medium (creates accounts)
            "data_exfiltration": 0.7,  # high (bandwidth pattern detectable)
            "recon_passive": 0.1,  # very low (OSINT)
        }.get(vector, 0.5)

        vs = self._vectors[vector]
        # Increase risk if recent failures suggest detection
        if vs.failures > vs.successes:
            base_risk += 0.2
        # Decrease if no failures recently
        if vs.successes > 0 and vs.last_success_time > time.time() - 300:
            base_risk -= 0.1

        return max(0.05, min(0.95, base_risk))

    def thompson_sample(self, vector: str) -> float:
        """Thompson sample from Beta distribution for a vector."""
        vs = self._vectors[vector]
        # Use random.betavariate for Thompson sampling
        try:
            return random.betavariate(vs.alpha, vs.beta)
        except (ValueError, ZeroDivisionError):
            return 0.5  # prior

    def decide(self, proxy_available: int = 0) -> list[StealthDecision]:
        """Decide attack order based on Thompson sampling + proxy health."""
        decisions: list[StealthDecision] = []

        for vname, vs in self._vectors.items():
            # Skip if no proxies and vector needs them
            if proxy_available < 2 and vname != "recon_passive":
                continue

            # Thompson sample for success probability
            prob = self.thompson_sample(vname)

            reason_parts = []
            if vs.successes > 0:
                reason_parts.append(f"history: {vs.successes}/{vs.successes + vs.failures} success")
            if vs.backtrack_risk > 0.5:
                reason_parts.append(f"high backtrack risk ({vs.backtrack_risk:.1%})")
            if proxy_available < 3 and vname != "recon_passive":
                reason_parts.append(f"low proxies ({proxy_available})")

            decisions.append(
                StealthDecision(
                    vector=vname,
                    priority=0,  # sorted later
                    estimated_success_prob=prob,
                    risk_score=vs.backtrack_risk,
                    reason="; ".join(reason_parts) if reason_parts else "warm start",
                )
            )

        # Sort: highest score first
        decisions.sort(key=lambda d: d.estimated_success_prob * (1.0 - d.risk_score), reverse=True)

        for i, d in enumerate(decisions):
            d.priority = i

        self.save_state()
        return decisions

    def print_plan(self, decisions: list[StealthDecision]) -> None:
        """Pretty-print the attack plan."""
        print("""
    ╔══════════════════════════════════════════════════════════════╗
    ║  Bayesian Attack Plan (Thompson Sampling)                  ║
    ╠════╤═══════════════════════╤══════════╤═════════╤══════════╣
    ║  # │ Vector               │ Success  │ Risk    │ Reason    ║
    ╠════╪═══════════════════════╪══════════╪═════════╪══════════╣""")
        for d in decisions:
            filled = min(10, max(0, round(d.estimated_success_prob * 10)))
            bar = "█" * filled + "░" * (10 - filled)
            reason = f"{d.reason[:30]:<30}"
            head = f"    ║ {d.priority}  │ {d.vector:<21} │ {bar}"
            print(f"{head} │ {d.risk_score:.2f}    │ {reason} ║")
        print("""    ╚════╧═══════════════════════╧══════════╧═════════╧══════════╝""")


# ── Quick test ──
if __name__ == "__main__":
    engine = StealthDecisionEngine()
    engine.record("recon_passive", True, 250.0)
    plan = engine.decide(proxy_available=5)
    engine.print_plan(plan)
