"""Async batch fetcher — concurrent crawling with shared rate limiting.

Sophistication layer: fetch many URLs/pages/articles concurrently while a
single shared token bucket keeps the aggregate request rate polite. Built on
asyncio + curl_cffi's async client. Each host also gets a circuit breaker so
one failing endpoint can't stall the batch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from omk_crawl.stability import BreakerRegistry, get_logger
from omk_crawl.resilience import TokenBucket

log = get_logger("omk_crawl.async_batch")


@dataclass
class BatchItem:
    key: str                       # caller-defined id (e.g. url or article_id)
    url: str
    params: dict[str, Any] | None = None
    headers: dict[str, str] | None = None
    method: str = "GET"


@dataclass
class BatchResult:
    key: str
    ok: bool
    status_code: int | None = None
    json_data: Any = None
    html: str | None = None
    error: str = ""


@dataclass
class BatchConfig:
    rate: float = 1.0          # aggregate requests/sec across all workers
    burst: float = 4.0
    concurrency: int = 4       # max parallel workers
    timeout: int = 12
    impersonate: str = "chrome124"


class AsyncBatchFetcher:
    """Concurrent fetcher with a shared polite rate limit and per-host breakers."""

    def __init__(self, config: BatchConfig | None = None) -> None:
        self.cfg = config or BatchConfig()
        self.bucket = TokenBucket(rate=self.cfg.rate, capacity=self.cfg.burst)
        self.breakers = BreakerRegistry(failure_threshold=4, recovery_timeout=20.0)
        self._sem = asyncio.Semaphore(self.cfg.concurrency)

    @staticmethod
    def _host(url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).netloc

    async def _fetch_one(self, client, item: BatchItem) -> BatchResult:
        breaker = self.breakers.get(self._host(item.url))
        if not breaker.allow():
            return BatchResult(key=item.key, ok=False, error="circuit open")

        await self.bucket.acquire_async()
        async with self._sem:
            try:
                headers = {
                    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"),
                    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                }
                if item.headers:
                    headers.update(item.headers)
                if item.method.upper() == "POST":
                    resp = await client.post(item.url, params=item.params,
                                             headers=headers, timeout=self.cfg.timeout)
                else:
                    resp = await client.get(item.url, params=item.params,
                                            headers=headers, timeout=self.cfg.timeout)
                ct = resp.headers.get("content-type", "")
                json_data = None
                if "json" in ct:
                    try:
                        json_data = resp.json()
                    except Exception:
                        pass
                ok = resp.status_code < 400
                if ok:
                    breaker.record_success()
                else:
                    breaker.record_failure()
                return BatchResult(
                    key=item.key, ok=ok, status_code=resp.status_code,
                    json_data=json_data, html=resp.text if "json" not in ct else None,
                    error="" if ok else f"HTTP {resp.status_code}",
                )
            except Exception as e:
                breaker.record_failure()
                return BatchResult(key=item.key, ok=False, error=str(e)[:200])

    async def fetch_many(self, items: list[BatchItem],
                         on_result: Callable[[BatchResult], Any] | None = None
                         ) -> list[BatchResult]:
        """Fetch all items concurrently (bounded by concurrency + rate limit).

        Args:
            items: BatchItems to fetch.
            on_result: optional callback invoked per completed result.
        """
        from curl_cffi import requests as cffi

        async with cffi.AsyncSession(impersonate=self.cfg.impersonate) as client:
            tasks = [asyncio.create_task(self._fetch_one(client, it)) for it in items]
            results: list[BatchResult] = []
            for fut in asyncio.as_completed(tasks):
                r = await fut
                results.append(r)
                if on_result:
                    res = on_result(r)
                    if asyncio.iscoroutine(res):
                        await res
        ok = sum(1 for r in results if r.ok)
        log.info("batch done: %d/%d ok", ok, len(results))
        return results

    def run(self, items: list[BatchItem],
            on_result: Callable[[BatchResult], Any] | None = None) -> list[BatchResult]:
        """Sync entrypoint — runs the async batch in an event loop."""
        return asyncio.run(self.fetch_many(items, on_result))
