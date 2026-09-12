"""Target-aware crawl entry points, execution policy, and request pacing.

Route planning lives in route_engine; budgeted adapter calls live in request_runner.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from omk_crawl.detect import check_robots_txt
from omk_crawl.pipeline import ensure_markdown
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.retry_after import retry_after_seconds
from omk_crawl.routing import SiteMemory, SiteMemoryStore
from omk_crawl.tools import ESCALATION_CHAIN, get_tool
from omk_crawl.tools.base import BaseTool

logger = logging.getLogger("omk_crawl")

# Per-domain rate limiter: {domain: last_request_timestamp}
_last_request: dict[str, float] = {}
_rate_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """Why the router picked a tool."""

    tool: str
    reason: str
    attempt: int
    detection: str = ""


@runtime_checkable
class _RouteEngine(Protocol):
    def diagnose(self, router: SmartRouter, url: str, kwargs: dict[str, Any]) -> dict[str, Any]: ...

    def crawl_sync(self, router: SmartRouter, url: str, kwargs: dict[str, Any]) -> CrawlResult: ...

    async def crawl_async(
        self, router: SmartRouter, url: str, kwargs: dict[str, Any]
    ) -> CrawlResult: ...


def _route_engine() -> _RouteEngine:
    module = import_module("omk_crawl.route_engine")
    if not isinstance(module, _RouteEngine):
        raise RuntimeError("route engine is not initialized")
    return module


@dataclass
class SmartRouter:
    """Select capable adapters and execute them within a shared cooperative budget."""

    # Tools to try, in escalation order. None = auto (all available).
    tools: list[str] | None = None
    # Stop escalating after this many attempts
    max_attempts: int = 8
    # Retry transient failures (timeout, connection reset) this many times
    max_retries: int = 1
    # Base delay for exponential backoff between retries (seconds)
    retry_delay: float = 1.0
    # Minimum seconds between requests to the same domain (rate limiting)
    min_delay: float = 0.5
    # Check robots.txt before crawling
    respect_robots: bool = True
    # Verbose logging
    verbose: bool = False
    # Extra kwargs passed to each tool
    tool_kwargs: dict[str, Any] = field(default_factory=dict)
    # History of attempts
    history: list[CrawlResult] = field(default_factory=list)
    decisions: list[RouteDecision] = field(default_factory=list)
    # Per-site strategy memory; appended to preserve positional compatibility
    site_memory: SiteMemoryStore = field(default_factory=SiteMemory)
    learn_sites: bool = True
    # Refuse longer server-directed waits; do not retry earlier than Retry-After.
    max_retry_delay: float = 30.0
    # Cooperative deadline; includes waits and does not reset during escalation.
    total_timeout: float | None = 120.0
    max_fetches: int | None = None
    allow_browser: bool = True
    allow_llm: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("retry_delay", self.retry_delay),
            ("min_delay", self.min_delay),
            ("max_retry_delay", self.max_retry_delay),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        for name, value, minimum in (
            ("max_attempts", self.max_attempts, 1),
            ("max_retries", self.max_retries, 0),
            ("max_fetches", self.max_fetches, 1),
        ):
            if name == "max_fetches" and value is None:
                continue
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if self.total_timeout is not None and (
            not math.isfinite(self.total_timeout) or self.total_timeout <= 0
        ):
            raise ValueError("total_timeout must be positive and finite, or None")
        if type(self.allow_browser) is not bool or type(self.allow_llm) is not bool:
            raise ValueError("allow_browser and allow_llm must be booleans")

    def _get_chain(self) -> list[BaseTool]:
        if self.tools is not None:
            try:
                return [get_tool(name) for name in self.tools]
            except ValueError as exc:
                logger.warning("%s", exc)
                return []
        chain = []
        for cls in ESCALATION_CHAIN:
            t = cls()
            if t.available():
                chain.append(t)
        return chain

    def crawl(self, url: str, **kwargs: Any) -> CrawlResult:
        """Synchronous crawl with auto-escalation and retry."""
        return _route_engine().crawl_sync(self, url, kwargs)

    async def crawl_async(self, url: str, **kwargs: Any) -> CrawlResult:
        """Async crawl with auto-escalation and retry."""
        return await _route_engine().crawl_async(self, url, kwargs)

    def diagnose(self, url: str, **kwargs: Any) -> dict[str, Any]:
        """Plan the actual target route and policy skips without fetching it."""
        return _route_engine().diagnose(self, url, kwargs)

    @staticmethod
    def _score(r: CrawlResult) -> float:
        """Score a result for 'best attempt' selection."""
        s = 0.0
        if r.html:
            s += len(r.html)
        if r.markdown:
            s += len(r.markdown) * 2
        if r.status_code and 200 <= r.status_code < 300:
            s += 10000
        return s

    @staticmethod
    def _escalation_reason(r: CrawlResult) -> str:
        if r.ok:
            return "success"
        if r.status is CrawlStatus.TLS_BLOCKED:
            return "TLS fingerprint blocked → need browser/stealth"
        if r.status is CrawlStatus.BLOCKED:
            return "blocked (WAF/anti-bot) → escalate"
        if r.status is CrawlStatus.JS_REQUIRED:
            return "JS rendering needed → escalate to browser"
        return r.error or "failed"

    @staticmethod
    def _ensure_markdown(result: CrawlResult) -> None:
        ensure_markdown(result)

    def _log(self, msg: str) -> None:
        if self.verbose:
            logger.info(msg)

    @staticmethod
    def _check_robots(url: str) -> bool:
        return check_robots_txt(url)

    def _record_decision(self, tool: str, reason: str, attempt: int, detection: str) -> None:
        self.decisions.append(RouteDecision(tool, reason, attempt, detection))

    def _rate_wait(self, url: str) -> float:
        """Claim a domain slot only when ready; never sleep under the shared lock."""
        if self.min_delay <= 0:
            return 0.0
        try:
            parsed = urlparse(url if "://" in url else f"//{url}")
            domain = parsed.hostname or ""
        except ValueError:
            domain = ""
        with _rate_lock:
            now = time.monotonic()
            last = _last_request.get(domain)
            wait = max(0.0, self.min_delay - (now - last)) if last is not None else 0.0
            if wait == 0:
                _last_request[domain] = now
        if wait:
            self._log(f"  Rate limit: waiting {wait:.1f}s for {domain}")
        return wait

    def _rate_limit(self, url: str) -> None:
        while wait := self._rate_wait(url):
            time.sleep(wait)

    async def _rate_limit_async(self, url: str) -> None:
        while wait := self._rate_wait(url):
            await asyncio.sleep(wait)

    def _retry_delay(self, result: CrawlResult, attempt: int, method: str) -> float | None:
        if method.upper() not in {"GET", "HEAD", "OPTIONS"} or not self._is_transient(result):
            return None
        server_delay = retry_after_seconds(result.headers)
        if server_delay is not None:
            result.metadata["retry_after_seconds"] = server_delay
            if server_delay > self.max_retry_delay:
                result.metadata["retry_stop"] = "retry_after_limit"
                return None
        # ldexp avoids constructing a huge integer for caller-supplied retry counts.
        try:
            backoff = math.ldexp(self.retry_delay, attempt)
        except OverflowError:
            backoff = self.max_retry_delay
        return max(min(backoff, self.max_retry_delay), server_delay or 0.0)

    @staticmethod
    def _is_transient(r: CrawlResult) -> bool:
        """Retry temporary HTTP/transport failures, never authentication rejection."""
        if r.status_code in (401, 403, 407):
            return False
        if r.status_code in (408, 429, 502, 503, 504):
            return True
        if r.status is CrawlStatus.ERROR and r.error:
            transient_markers = (
                "timeout",
                "timed out",
                "connection reset",
                "connection refused",
                "connection aborted",
                "remote disconnected",
                "network unreachable",
            )
            return any(m in r.error.lower() for m in transient_markers)
        return False


# --- Module-level convenience ---


def crawl(
    url: str,
    *,
    tool: str | None = None,
    verbose: bool = False,
    respect_robots: bool = True,
    min_delay: float = 0.5,
    total_timeout: float | None = 120.0,
    max_attempts: int = 8,
    max_fetches: int | None = None,
    max_retries: int = 1,
    allow_browser: bool = True,
    allow_llm: bool = False,
    **kwargs: Any,
) -> CrawlResult:
    """One-liner crawl with auto-escalation.

    >>> from omk_crawl import crawl
    >>> r = crawl("https://example.com")
    >>> print(r.markdown)
    """
    router = SmartRouter(
        tools=[tool] if tool else None,
        verbose=verbose,
        respect_robots=respect_robots,
        min_delay=min_delay,
        total_timeout=total_timeout,
        max_attempts=max_attempts,
        max_fetches=max_fetches,
        max_retries=max_retries,
        allow_browser=allow_browser,
        allow_llm=allow_llm,
    )
    return router.crawl(url, **kwargs)


async def crawl_async(
    url: str,
    *,
    tool: str | None = None,
    verbose: bool = False,
    respect_robots: bool = True,
    min_delay: float = 0.5,
    total_timeout: float | None = 120.0,
    max_attempts: int = 8,
    max_fetches: int | None = None,
    max_retries: int = 1,
    allow_browser: bool = True,
    allow_llm: bool = False,
    **kwargs: Any,
) -> CrawlResult:
    """Async one-liner with the same routing controls as crawl()."""
    router = SmartRouter(
        tools=[tool] if tool else None,
        verbose=verbose,
        respect_robots=respect_robots,
        min_delay=min_delay,
        total_timeout=total_timeout,
        max_attempts=max_attempts,
        max_fetches=max_fetches,
        max_retries=max_retries,
        allow_browser=allow_browser,
        allow_llm=allow_llm,
    )
    return await router.crawl_async(url, **kwargs)
