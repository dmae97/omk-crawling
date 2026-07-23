"""crawl4ai adapter — LLM Markdown, deep crawl, MCP (default engine)."""

from __future__ import annotations

from typing import Any

from omk_crawl.detect import detect_block
from omk_crawl.result import CrawlResult, CrawlStatus, _timer
from omk_crawl.tools.base import BaseTool


class Crawl4aiTool(BaseTool):
    name = "crawl4ai"
    pip_package = "crawl4ai"
    layer = 1
    needs_browser = True

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        if not self.available():
            return self._missing(url)
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(self._fetch_sync, url, **kwargs).result()
            return loop.run_until_complete(self.fetch_async(url, **kwargs))
        except RuntimeError:
            return asyncio.run(self.fetch_async(url, **kwargs))

    async def fetch_async(self, url: str, **kwargs: Any) -> CrawlResult:
        if not self.available():
            return self._missing(url)
        _, stop = _timer()
        try:
            from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig

            cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS)
            async with AsyncWebCrawler() as crawler:
                r = await crawler.arun(url, config=cfg)

            if not r.success:
                return CrawlResult(
                    url=url,
                    status=CrawlStatus.ERROR,
                    tool=self.name,
                    elapsed_ms=stop(),
                    error=r.error_message or "crawl4ai failed",
                )

            html = r.html or ""
            det = detect_block(html, r.status_code)
            status = CrawlStatus.OK if det.block.value == 0 else CrawlStatus.BLOCKED

            md = r.markdown
            return CrawlResult(
                url=url,
                status=status,
                status_code=r.status_code,
                html=html,
                markdown=md.raw_markdown if md else None,
                fit_markdown=md.fit_markdown if md else None,
                tool=self.name,
                elapsed_ms=stop(),
                metadata={"detection": det.detail},
            )
        except Exception as exc:
            r = self._error(url, exc)
            r.elapsed_ms = stop()
            return r

    def _fetch_sync(self, url: str, **kwargs: Any) -> CrawlResult:
        import asyncio

        return asyncio.run(self.fetch_async(url, **kwargs))
