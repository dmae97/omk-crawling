"""Resilience primitives — rate limiting, retry, caching, header management.

Solves the 7 bottlenecks found during live testing:
  1. 429 rate-limit  → TokenBucket + exponential backoff
  2. 400 missing hdr → HeaderStore (mitmproxy capture loader)
  3. 403 WAF         → impersonate rotation
  4. DNS failure     → endpoint fallback chain
  5. CSR empty body  → Playwright auto-fallback
  6. Playwright miss → auto-install check
  7. Wrong endpoints → verified-only endpoint registry
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

# ─────────────────────────────────────────────
# 1. Token-bucket rate limiter
# ─────────────────────────────────────────────

class TokenBucket:
    """Token-bucket rate limiter. Thread-safe enough for single-process crawlers."""

    def __init__(self, rate: float = 1.0, capacity: float = 5.0) -> None:
        """
        Args:
            rate: Tokens added per second.
            capacity: Max burst size.
        """
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()

    def acquire(self, tokens: float = 1.0) -> float:
        """Block until tokens available. Returns wait time in seconds."""
        now = time.monotonic()
        elapsed = now - self._last
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last = now

        if self._tokens >= tokens:
            self._tokens -= tokens
            return 0.0

        deficit = tokens - self._tokens
        wait = deficit / self.rate
        time.sleep(wait)
        self._tokens = 0.0
        self._last = time.monotonic()
        return wait

    async def acquire_async(self, tokens: float = 1.0) -> float:
        """Async variant of acquire — sleeps without blocking the event loop."""
        import asyncio

        now = time.monotonic()
        elapsed = now - self._last
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last = now

        if self._tokens >= tokens:
            self._tokens -= tokens
            return 0.0

        deficit = tokens - self._tokens
        wait = deficit / self.rate
        await asyncio.sleep(wait)
        self._tokens = 0.0
        self._last = time.monotonic()
        return wait


# ─────────────────────────────────────────────
# 2. Retry with exponential backoff + jitter
# ─────────────────────────────────────────────

@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    backoff_factor: float = 2.0
    jitter: float = 0.3
    retryable_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504})

    def delay_for(self, attempt: int) -> float:
        d = min(self.base_delay * (self.backoff_factor ** attempt), self.max_delay)
        j = d * self.jitter * random.random()
        return d + j


def retry(
    fn: Callable[..., T],
    *args: Any,
    policy: RetryPolicy | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
    **kwargs: Any,
) -> T:
    """Call fn with retry. Raises last exception if all retries fail."""
    p = policy or RetryPolicy()
    last_exc: Exception | None = None
    for attempt in range(p.max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            status = getattr(exc, "status_code", None) or _extract_status(exc)
            if attempt < p.max_retries and (status is None or status in p.retryable_statuses):
                wait = p.delay_for(attempt)
                if on_retry:
                    on_retry(attempt, exc)
                time.sleep(wait)
            else:
                raise
    raise last_exc  # type: ignore[misc]


def _extract_status(exc: Exception) -> int | None:
    """Pull HTTP status from various exception types."""
    for attr in ("status_code", "code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    s = str(exc)
    for code in (429, 500, 502, 503, 504):
        if str(code) in s:
            return code
    return None


# ─────────────────────────────────────────────
# 3. Response cache (file-backed)
# ─────────────────────────────────────────────

class ResponseCache:
    """Simple file-backed response cache to avoid re-hitting rate-limited APIs."""

    def __init__(self, cache_dir: str | Path = ".crawl_cache", ttl: float = 300.0) -> None:
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl

    def _key(self, url: str) -> Path:
        import hashlib
        h = hashlib.sha256(url.encode()).hexdigest()[:16]
        return self.dir / f"{h}.json"

    def get(self, url: str) -> dict[str, Any] | None:
        p = self._key(url)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - data.get("_ts", 0) > self.ttl:
                p.unlink(missing_ok=True)
                return None
            return data
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, url: str, data: dict[str, Any]) -> None:
        p = self._key(url)
        data["_ts"] = time.time()
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ─────────────────────────────────────────────
# 4. Header store (mitmproxy capture loader)
# ─────────────────────────────────────────────

class HeaderStore:
    """Load and manage headers captured by mitmproxy or manual config.

    Solves: 400 '필수 헤더 값이 누락되었습니다'
    """

    DEFAULT_HEADERS: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": "application/json",
    }

    def __init__(self, capture_file: str | Path | None = None) -> None:
        self._headers: dict[str, str] = dict(self.DEFAULT_HEADERS)
        self._loaded = False
        if capture_file:
            self.load_capture(capture_file)

    def load_capture(self, path: str | Path) -> bool:
        """Load headers from mitmproxy capture JSON."""
        p = Path(path)
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            headers = data.get("headers", {})
            if headers:
                self._headers.update(headers)
                self._loaded = True
                return True
            # Try review_api specific headers
            review = data.get("review_api", {})
            if review.get("headers"):
                self._headers.update(review["headers"])
                self._loaded = True
                return True
        except (json.JSONDecodeError, OSError):
            pass
        return False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def get(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = dict(self._headers)
        if extra:
            h.update(extra)
        return h


# ─────────────────────────────────────────────
# 5. Impersonate rotation (anti-WAF)
# ─────────────────────────────────────────────

IMPERSONATE_POOL: list[str] = [
    "chrome124", "chrome120", "chrome116", "chrome110",
    "safari17_0", "safari15_5", "edge101", "firefox133",
]

class ImpersonateRotator:
    """Rotate TLS fingerprints to avoid WAF pattern matching."""

    def __init__(self, pool: list[str] | None = None) -> None:
        self.pool = pool or IMPERSONATE_POOL
        self._idx = 0
        self._failed: set[str] = set()

    def next(self) -> str:
        available = [p for p in self.pool if p not in self._failed]
        if not available:
            self._failed.clear()
            available = self.pool
        imp = available[self._idx % len(available)]
        self._idx += 1
        return imp

    def mark_failed(self, imp: str) -> None:
        self._failed.add(imp)


# ─────────────────────────────────────────────
# 6. Playwright availability check
# ─────────────────────────────────────────────

def ensure_playwright() -> bool:
    """Check if Playwright browsers are installed. Returns True if ready."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            # Try to find chromium executable
            exe = p.chromium.executable_path
            return Path(exe).exists() if exe else False
    except Exception:
        return False


def install_playwright_chromium() -> bool:
    """Attempt to install Playwright Chromium. Returns True on success."""
    import subprocess
    try:
        r = subprocess.run(
            ["playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=120,
        )
        return r.returncode == 0
    except Exception:
        return False


# ─────────────────────────────────────────────
# 7. Endpoint fallback chain
# ─────────────────────────────────────────────

@dataclass
class Endpoint:
    url: str
    method: str = "GET"
    params: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    needs_auth: bool = False
    verified: bool = False  # True = confirmed from APK/network capture


class EndpointChain:
    """Try endpoints in order until one succeeds. Solves DNS failures + wrong URLs."""

    def __init__(
        self, endpoints: list[Endpoint], rotator: ImpersonateRotator | None = None,
    ) -> None:
        self.endpoints = endpoints
        self.rotator = rotator or ImpersonateRotator()

    def fetch(self, **overrides: Any) -> tuple[Endpoint, Any] | None:
        """Try each endpoint. Returns (endpoint, response) or None."""
        from curl_cffi import requests as cffi

        for ep in self.endpoints:
            imp = self.rotator.next()
            try:
                params = {**ep.params, **overrides.get("params", {})}
                headers = {**ep.headers, **overrides.get("headers", {})}
                if ep.method.upper() == "POST":
                    resp = cffi.post(
                        ep.url, json=overrides.get("json"),
                        params=params, headers=headers,
                        impersonate=imp, timeout=overrides.get("timeout", 10),
                    )
                else:
                    resp = cffi.get(
                        ep.url, params=params, headers=headers,
                        impersonate=imp, timeout=overrides.get("timeout", 10),
                    )
                if resp.status_code < 400:
                    return ep, resp
                if resp.status_code == 403:
                    self.rotator.mark_failed(imp)
            except Exception:
                continue
        return None
