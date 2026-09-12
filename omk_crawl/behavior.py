"""Human behavior simulation — deterministic, seeded, offline-testable.

Anti-bot systems score *interaction dynamics* as much as fingerprints:
request cadence (constant-rate = bot), dwell time (zero reading time = bot),
mouse trajectories (straight lines = bot), scroll patterns (teleport = bot).
FP-Inconsistent (arXiv:2406.07647) shows detectors also compare behavior
*over time* — so randomness must be reproducible per session, not fresh
entropy per call.

Everything here is a pure function of an explicit seed: same seed → same
timings, same paths. That keeps runs replayable, diff-able in tests, and
stable for the over-time consistency axis.
"""

from __future__ import annotations

import hashlib
import math
import random

__all__ = ["BehaviorClock"]


def _seed_int(seed: int | str | bytes) -> int:
    """Normalize any seed material into a 64-bit int (fail-closed)."""
    if isinstance(seed, int):
        return seed & 0xFFFFFFFFFFFFFFFF
    if isinstance(seed, str):
        seed = seed.encode("utf-8")
    try:
        return int.from_bytes(hashlib.sha256(seed).digest()[:8], "big")
    except (TypeError, ValueError) as exc:
        raise TypeError(f"unsupported seed type: {type(seed).__name__}") from exc


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _to_int(value: float, fallback: int) -> int:
    """float → int, fail-closed (NaN/inf can raise on conversion)."""
    try:
        return int(value)
    except (OverflowError, ValueError):
        return fallback


class BehaviorClock:
    """Seeded generator of human-plausible timings and interaction plans.

    Usage:
        clock = BehaviorClock("example.com|session-1")
        page.wait_for_timeout(int(clock.think_time() * 1000))
        for stop in clock.scroll_plan(page_height=8000, viewport_height=1080):
            page.mouse.wheel(0, stop); page.wait_for_timeout(...)
    """

    def __init__(self, seed: int | str | bytes) -> None:
        self._rng = random.Random(_seed_int(seed))

    # ── Timing ──────────────────────────────────────────────────────────

    def think_time(self, lo: float = 0.4, hi: float = 4.0) -> float:
        """Pause before the next action (seconds), lognormal ~1.1s median.

        Humans pause with a long right tail; a fixed sleep() is the clearest
        bot cadence tell. Clamped so tests and timeouts stay sane.
        """
        value = self._rng.lognormvariate(0.1, 0.55)
        return _clamp(value, lo, hi)

    def dwell_time(self, content_chars: int, lo: float = 0.8, hi: float = 30.0) -> float:
        """Time spent "reading" a page (seconds) for its content length.

        ~180 chars/sec reading speed with gaussian jitter, plus a base
        orientation pause. Zero dwell before an action is a bot tell.
        """
        base = self._rng.uniform(0.5, 1.2)
        reading = max(content_chars, 0) / 180.0
        jitter = self._rng.gauss(0.0, max(reading * 0.15, 0.05))
        return _clamp(base + reading + jitter, lo, hi)

    def inter_request_delay(self, base: float = 1.0) -> float:
        """Pacing between requests (seconds) — bursty human cadence, not metronome."""
        value = base * self._rng.lognormvariate(0.35, 0.5)
        return _clamp(value, base * 0.5, base * 8.0)

    def jitter(self, scale: float = 1.0) -> float:
        """Symmetric gaussian jitter, stddev ``scale``."""
        return self._rng.gauss(0.0, scale)

    # ── Interaction geometry ────────────────────────────────────────────

    def mouse_path(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        steps: int | None = None,
    ) -> list[tuple[int, int]]:
        """Quadratic-Bezier mouse trajectory with per-point jitter.

        Real cursor movement curves and overshoots slightly; a straight line
        between two points is a classic synthetic-event tell. Endpoints are
        preserved exactly (start at index 0, end at index -1).
        """
        x0, y0 = start
        x1, y1 = end
        distance = math.hypot(x1 - x0, y1 - y0)
        if steps is None:
            steps = _to_int(_clamp(distance / 40.0, 8, 48), 8)
        steps = max(steps, 2)
        # Control point: offset perpendicular to the segment, seeded.
        mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        nx, ny = (-(y1 - y0), (x1 - x0))
        norm = math.hypot(nx, ny) or 1.0
        offset = self._rng.uniform(-0.25, 0.25) * distance
        cx, cy = mx + nx / norm * offset, my + ny / norm * offset

        points: list[tuple[int, int]] = []
        for i in range(steps):
            t = i / (steps - 1)
            # Quadratic Bezier: B(t) = (1-t)²P0 + 2(1-t)t·C + t²P1
            bx = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t**2 * x1
            by = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t**2 * y1
            if 0 < i < steps - 1:
                bx += self._rng.gauss(0.0, 1.5)
                by += self._rng.gauss(0.0, 1.5)
                points.append((round(bx), round(by)))
            else:
                points.append((round(bx), round(by)))
        points[0] = (round(x0), round(y0))
        points[-1] = (round(x1), round(y1))
        return points

    def scroll_plan(self, page_height: int, viewport_height: int) -> list[int]:
        """Scroll stops (pixels) covering the page like a reading human.

        Chunk sizes vary between 55–85% of the viewport with jitter; the
        final stop lands exactly at the bottom. Empty for short pages.
        """
        scrollable = max(page_height - viewport_height, 0)
        if scrollable <= 0 or viewport_height <= 0:
            return []
        stops: list[int] = []
        position = 0
        while position < scrollable:
            chunk = viewport_height * self._rng.uniform(0.55, 0.85)
            chunk += self._rng.gauss(0.0, viewport_height * 0.03)
            position += max(_to_int(chunk, viewport_height // 2), 1)
            stops.append(min(position, scrollable))
        stops[-1] = scrollable
        return stops
