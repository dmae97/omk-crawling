"""Base tool adapter interface."""

from __future__ import annotations

import abc
from typing import Any

from omk_crawl.detect import tool_available
from omk_crawl.result import CrawlResult, CrawlStatus

# Common kwargs every adapter understands *by name*. Each adapter declares which
# it actually implements via `capabilities`; a requested-but-unsupported feature
# is reported explicitly (metadata) rather than silently ignored.
COMMON_KWARGS: tuple[str, ...] = ("timeout", "proxy", "headers", "cookies", "session")


class BaseTool(abc.ABC):
    """Abstract adapter for a crawling tool."""

    name: str = "base"
    pip_package: str = ""
    layer: int = 0  # 0=fetch, 1=crawl, 2=browser, 3=extract, 4=convert, 5=mobile
    needs_browser: bool = False
    needs_llm: bool = False
    # Which COMMON_KWARGS features this adapter actually implements. Declared so
    # the router can route by capability and callers get explicit unsupported
    # feedback instead of silent no-ops.
    capabilities: frozenset[str] = frozenset()

    def available(self) -> bool:
        return tool_available(self.name)

    def supports(self, feature: str) -> bool:
        """Whether this adapter implements a common feature (timeout/proxy/…)."""
        return feature in self.capabilities

    def unsupported_features(self, kwargs: dict[str, Any]) -> list[str]:
        """Common kwargs the caller passed (non-None) that we do NOT support."""
        return [
            k for k in COMMON_KWARGS
            if k in kwargs and kwargs[k] is not None and not self.supports(k)
        ]

    def contract_metadata(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Standard contract metadata: declared capabilities + unsupported reqs."""
        meta: dict[str, Any] = {"capabilities": sorted(self.capabilities)}
        unsupported = self.unsupported_features(kwargs)
        if unsupported:
            meta["unsupported_requested"] = unsupported
        return meta

    def install_hint(self) -> str:
        return f"pip install {self.pip_package}" if self.pip_package else ""

    @abc.abstractmethod
    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        """Synchronous fetch. Must not raise — return CrawlResult with error.

        Common contract kwargs (see COMMON_KWARGS; each adapter declares which
        it implements via `capabilities`):
            timeout (int): Request timeout in SECONDS (default 30). Adapters
                using milliseconds internally convert it.
            proxy (str): Proxy URL, e.g. "http://user:pass@host:port".
            headers (dict[str, str]): Extra request headers.
            cookies (dict[str, str]): Request cookies.
            session: Optional shared session object for connection reuse.
        Tool-specific options are passed as extra **kwargs.
        """
        ...

    async def fetch_async(self, url: str, **kwargs: Any) -> CrawlResult:
        """Async fetch. Default: run sync in executor."""
        import asyncio

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.fetch(url, **kwargs))

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
