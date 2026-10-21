"""Rotation crawling — per-request proxy rotation through the self-healing pool.

무한의 굴레 (the infinite yoke), closed into one loop:

    free proxy sources → FreshnessEngine (harvest → validate → rank, forever)
                          ↓
    RotationCrawler.crawl(url)  — one pool proxy per attempt, curl_cffi TLS
    profile rotation included, every outcome fed back:
        ok            → mark_success (score up)
        429           → mark_rate_limited (per-proxy backoff)
        407           → mark_dead (burn: proxy demands auth)
        blocked/error → mark_failure (consecutive fails → long backoff)
                          ↓
    pool low → emergency harvest → newest lists → back to the top
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

from omk_crawl.result import CrawlResult, CrawlStatus

logger = logging.getLogger("omk_crawl.rotate")


class FetcherLike(Protocol):
    """Duck type shared by BaseTool adapters and test doubles."""

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult: ...


# Outcome buckets fed back to the pool.
_OUTCOME_OK = "ok"
_OUTCOME_RATE_LIMITED = "rate_limited"
_OUTCOME_PROXY_AUTH = "proxy_auth"  # 407 — burn immediately
_OUTCOME_BLOCKED = "blocked"
_OUTCOME_ERROR = "error"


def _classify(result: CrawlResult) -> str:
    """Map a crawl outcome onto the pool-feedback vocabulary."""
    if result.ok:
        return _OUTCOME_OK
    code = result.status_code
    if code == 407:
        return _OUTCOME_PROXY_AUTH
    if code == 429:
        return _OUTCOME_RATE_LIMITED
    if result.blocked or code in (403, 503):
        return _OUTCOME_BLOCKED
    return _OUTCOME_ERROR


@dataclass
class RotationStats:
    """Counters for one crawl or a whole batch."""

    requests: int = 0
    attempts: int = 0
    ok: int = 0
    failed: int = 0
    burned: int = 0
    distinct_proxies: int = 0
    exhausted_pool: int = 0  # attempts that found no proxy at all


class RotationCrawler:
    """Crawl with a different free-pool proxy per attempt.

    ``engine`` defaults to a :class:`FreshnessEngine` (self-healing pool).
    The daemon is NOT auto-started — pool-empty attempts trigger one bounded
    synchronous refill when ``auto_refill`` is on, and API users may
    ``await engine.start()`` for the continuous 무한 loop.
    """

    def __init__(
        self,
        engine: Any | None = None,
        tool: FetcherLike | None = None,
        attempts_per_url: int = 4,
        timeout: float = 15.0,
        auto_refill: bool = True,
    ) -> None:
        if attempts_per_url < 1:
            raise ValueError("attempts_per_url must be >= 1")
        self.engine = engine
        self.tool = tool
        self.attempts_per_url = attempts_per_url
        self.timeout = timeout
        self.auto_refill = auto_refill
        self.stats = RotationStats()
        self._mark_lock = threading.Lock()
        self._engine_lock = threading.Lock()

    # ── engine lifecycle ──
    def _get_engine(self) -> Any:
        if self.engine is None:
            from omk_crawl.tools.freshness_engine import FreshnessEngine

            self.engine = FreshnessEngine()
        return self.engine

    def _get_tool(self) -> FetcherLike:
        if self.tool is None:
            from omk_crawl.tools.curl_cffi_tool import CurlCffiTool

            self.tool = CurlCffiTool()
        return self.tool

    def _sync_refill(self, target: int = 100) -> bool:
        """One bounded harvest+validate cycle from sync code.

        Returns True if the pool gained proxies. Never raises: a missing
        optional dependency or a network failure degrades to False.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                return asyncio.run(self._refill_once(target))
            except Exception:  # noqa: BLE001 - refill is best-effort by contract
                return False

        # Inside a running loop, hop to a worker thread with its own loop.
        try:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self._refill_once(target)).result()
        except Exception:  # noqa: BLE001 - refill is best-effort by contract
            return False

    async def _refill_once(self, target: int) -> bool:
        engine = self._get_engine()
        inner = getattr(engine, "_engine", engine)  # FreshnessEngine wraps ProxyEngineV2
        added = await inner.harvest(target_count=target)
        if added <= 0:
            return False
        try:
            await inner.validate_batch(batch_size=20)
        except RuntimeError:
            pass  # no validation key configured — freshness-only cycle
        return True

    # ── feedback ──
    def _feedback(self, node: Any, outcome: str) -> None:
        engine = self._get_engine()
        with self._mark_lock:
            if outcome == _OUTCOME_OK:
                engine.mark_success(node)
            elif outcome == _OUTCOME_RATE_LIMITED:
                engine.mark_rate_limited(node)
            elif outcome == _OUTCOME_PROXY_AUTH:
                engine.mark_dead(node)
                self.stats.burned += 1
            else:
                node.mark_failure()
            self.stats.attempts += 1

    # ── crawl ──
    def crawl(self, url: str, **kwargs: Any) -> CrawlResult:
        """Fetch ``url`` rotating through pool proxies until one succeeds."""
        engine = self._get_engine()
        tool = self._get_tool()
        request_kwargs = dict(kwargs)
        request_kwargs.setdefault("timeout", self.timeout)
        used: list[str] = []
        last: CrawlResult | None = None

        self.stats.requests += 1
        for attempt in range(1, self.attempts_per_url + 1):
            with self._engine_lock:
                node = engine.next(exclude=set(used) if used else None)
            if node is None:
                self.stats.exhausted_pool += 1
                if self.auto_refill and self._sync_refill():
                    with self._engine_lock:
                        node = engine.next(exclude=set(used) if used else None)
                if node is None:
                    logger.warning("Proxy pool exhausted for %s", url)
                    break

            used.append(node.url)
            request_kwargs["proxy"] = node.proxy_url
            if node.tls_profile:
                request_kwargs["impersonate"] = node.tls_profile
            else:
                request_kwargs.pop("impersonate", None)
            result = tool.fetch(url, **request_kwargs)
            outcome = _classify(result)
            self._feedback(node, outcome)
            last = result
            logger.debug("attempt %d %s via %s → %s", attempt, url, node.url, outcome)
            if result.ok:
                self.stats.ok += 1
                self.stats.distinct_proxies = len(set(used))
                result.metadata.setdefault("rotation", {})
                result.metadata["rotation"] = {
                    "attempts": len(used),
                    "proxies": used,
                    "last_outcome": outcome,
                }
                return result

        self.stats.failed += 1
        self.stats.distinct_proxies = len(set(used))
        if last is None:
            last = CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                error="proxy pool exhausted (no proxy available)",
            )
        last.metadata.setdefault("rotation", {})
        last.metadata["rotation"] = {
            "attempts": len(used),
            "proxies": used,
            "last_outcome": _classify(last) if last.status is not CrawlStatus.ERROR else "error",
        }
        return last

    async def crawl_many(
        self, urls: list[str], concurrency: int = 5, **kwargs: Any
    ) -> list[CrawlResult]:
        """Crawl many URLs concurrently; the shared pool arbitrates politeness."""
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        semaphore = asyncio.Semaphore(concurrency)

        async def one(target: str) -> CrawlResult:
            async with semaphore:
                return await asyncio.to_thread(self.crawl, target, **kwargs)

        return list(await asyncio.gather(*(one(u) for u in urls)))


@dataclass
class CycleReport:
    """One harvest→validate pass over the pool."""

    cycle: int
    added: int
    validated: int
    pool: dict[str, Any] = field(default_factory=dict)


async def pool_cycles(
    engine: Any,
    cycles: int = 1,
    interval: float = 60.0,
    harvest_target: int = 200,
    validate_batch: int = 30,
) -> list[CycleReport]:
    """무한의 굴레, bounded: harvest→validate→(sleep)→repeat.

    ``cycles=0`` means infinite — stop by cancelling the task (Ctrl+C).
    """
    if cycles < 0:
        raise ValueError("cycles must be >= 0 (0 = infinite)")
    if interval < 0:
        raise ValueError("interval must be >= 0")
    reports: list[CycleReport] = []
    cycle = 0
    while cycles == 0 or cycle < cycles:
        cycle += 1
        added = await engine.harvest(target_count=harvest_target)
        validated = 0
        try:
            validated = await engine.validate_batch(batch_size=validate_batch)
        except RuntimeError:
            pass  # no validation key — freshness-only cycle
        reports.append(
            CycleReport(cycle=cycle, added=added, validated=validated, pool=engine.stats)
        )
        if cycles != 0 and cycle >= cycles:
            break
        await asyncio.sleep(interval)
    return reports
