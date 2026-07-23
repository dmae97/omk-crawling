"""browser-use adapter — LLM agent drives a real browser (last resort)."""

from __future__ import annotations

from typing import Any

from omk_crawl.result import CrawlResult, CrawlStatus, _timer
from omk_crawl.tools.base import BaseTool


class BrowserUseTool(BaseTool):
    name = "browser_use"
    pip_package = "browser-use"
    layer = 2
    needs_browser = True
    needs_llm = True

    def __init__(self, model: str = "bu-2-0") -> None:
        self.model = model

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        if not self.available():
            return self._missing(url)
        import asyncio

        try:
            return asyncio.run(self.fetch_async(url, **kwargs))
        except RuntimeError:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(lambda: asyncio.run(self.fetch_async(url, **kwargs))).result()

    async def fetch_async(self, url: str, **kwargs: Any) -> CrawlResult:
        if not self.available():
            return self._missing(url)
        _, stop = _timer()
        try:
            from browser_use import Agent, ChatBrowserUse

            task = kwargs.get(
                "task",
                f"Navigate to {url} and extract the full page content as text.",
            )
            agent = Agent(task=task, llm=ChatBrowserUse(model=self.model))
            history = await agent.run()

            content = history.final_result() or ""
            return CrawlResult(
                url=url,
                status=CrawlStatus.OK if content else CrawlStatus.ERROR,
                tool=self.name,
                markdown=content,
                elapsed_ms=stop(),
                metadata={"model": self.model, "agent": True},
            )
        except Exception as exc:
            r = self._error(url, exc)
            r.elapsed_ms = stop()
            return r
