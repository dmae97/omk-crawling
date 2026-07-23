"""Base tool adapter interface."""

from __future__ import annotations

import abc
from typing import Any

from omk_crawl.detect import tool_available
from omk_crawl.result import CrawlResult, CrawlStatus


class BaseTool(abc.ABC):
    """Abstract adapter for a crawling tool."""

    name: str = "base"
    pip_package: str = ""
    layer: int = 0  # 0=fetch, 1=crawl, 2=browser, 3=extract, 4=convert, 5=mobile
    needs_browser: bool = False
    needs_llm: bool = False

    def available(self) -> bool:
        return tool_available(self.name)

    def install_hint(self) -> str:
        return f"pip install {self.pip_package}" if self.pip_package else ""

    @abc.abstractmethod
    def fetch(self, url: str, *, timeout: int = 30, **kwargs: Any) -> CrawlResult:
        """Synchronous fetch. Must not raise — return CrawlResult with error.

        Args:
            url: Target URL.
            timeout: Request timeout in seconds (default 30).
            **kwargs: Tool-specific options.
        """
        ...

    async def fetch_async(self, url: str, *, timeout: int = 30, **kwargs: Any) -> CrawlResult:
        """Async fetch. Default: run sync in executor."""
        import asyncio

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.fetch(url, timeout=timeout, **kwargs))

    def _missing(self, url: str) -> CrawlResult:
        return CrawlResult(
            url=url,
            status=CrawlStatus.TOOL_MISSING,
            tool=self.name,
            error=f"{self.name} not installed. {self.install_hint()}",
        )

    def _error(self, url: str, exc: Exception) -> CrawlResult:
        return CrawlResult(
            url=url,
            status=CrawlStatus.ERROR,
            tool=self.name,
            error=f"{type(exc).__name__}: {exc}",
        )
