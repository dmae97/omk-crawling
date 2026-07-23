"""markitdown adapter — file → Markdown conversion."""

from __future__ import annotations

from typing import Any

from omk_crawl.result import CrawlResult, CrawlStatus, _timer
from omk_crawl.tools.base import BaseTool


class MarkitdownTool(BaseTool):
    name = "markitdown"
    pip_package = "markitdown[all]"
    layer = 4
    needs_browser = False

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        """Convert a local file path to Markdown. 'url' is a file path here."""
        if not self.available():
            return self._missing(url)
        _, stop = _timer()
        try:
            from markitdown import MarkItDown

            md = MarkItDown()
            result = md.convert(url)
            return CrawlResult(
                url=url,
                status=CrawlStatus.OK,
                markdown=result.text_content,
                tool=self.name,
                elapsed_ms=stop(),
            )
        except Exception as exc:
            r = self._error(url, exc)
            r.elapsed_ms = stop()
            return r
