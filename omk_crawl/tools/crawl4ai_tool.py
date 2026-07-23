"""crawl4ai adapter — LLM Markdown, deep crawl, MCP (default engine)."""

from __future__ import annotations

from typing import Any

from omk_crawl.detect import detect_block, detection_to_status
from omk_crawl.result import CrawlResult, CrawlStatus, _timer
from omk_crawl.tools.base import BaseTool


class Crawl4aiTool(BaseTool):
    name = "crawl4ai"
    pip_package = "crawl4ai"
    layer = 1
    needs_browser = True
    capabilities = frozenset({"timeout", "headers", "js_render", "markdown"})

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        if not self.available():
            return self._missing(url)
        import asyncio

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(self._fetch_sync, url, **kwargs).result()
            return asyncio.run(self.fetch_async(url, **kwargs))
        except RuntimeError:
            return asyncio.run(self.fetch_async(url, **kwargs))

    async def fetch_async(self, url: str, **kwargs: Any) -> CrawlResult:
        if not self.available():
            return self._missing(url)
        _, stop = _timer()
        try:
            from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig

            run_kwargs: dict[str, Any] = {
                "cache_mode": CacheMode.BYPASS,
                "page_timeout": kwargs.get("timeout", 30) * 1000,
            }
            if kwargs.get("headers"):
                run_kwargs["headers"] = kwargs["headers"]
            cfg = CrawlerRunConfig(**run_kwargs)
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
            status = detection_to_status(det)

            md = r.markdown
            meta = {"detection": det.detail}
            meta.update(self.contract_metadata(kwargs))
            return CrawlResult(
                url=url,
                status=status,
                status_code=r.status_code,
                html=html,
                markdown=md.raw_markdown if md else None,
                fit_markdown=md.fit_markdown if md else None,
                tool=self.name,
                elapsed_ms=stop(),
                metadata=meta,
            )
        except Exception as exc:
            r = self._error(url, exc)
            r.elapsed_ms = stop()
            return r

    def _fetch_sync(self, url: str, **kwargs: Any) -> CrawlResult:
        import asyncio

        return asyncio.run(self.fetch_async(url, **kwargs))
