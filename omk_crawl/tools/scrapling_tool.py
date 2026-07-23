"""scrapling adapter — stealth browser + adaptive selectors."""

from __future__ import annotations

from typing import Any

from omk_crawl.detect import detect_block, detection_to_status
from omk_crawl.result import CrawlResult, _timer
from omk_crawl.tools.base import BaseTool


class ScraplingTool(BaseTool):
    name = "scrapling"
    pip_package = "scrapling"
    layer = 0
    needs_browser = True

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        if not self.available():
            return self._missing(url)
        _, stop = _timer()
        try:
            from scrapling import StealthyFetcher

            page = StealthyFetcher.fetch(
                url,
                headless=kwargs.get("headless", True),
                timeout=kwargs.get("timeout", 30),
            )

            html = page.html_content if hasattr(page, "html_content") else str(page)
            status_code = page.status if hasattr(page, "status") else None
            det = detect_block(html, status_code)
            status = detection_to_status(det)

            return CrawlResult(
                url=url,
                status=status,
                status_code=status_code,
                html=html,
                tool=self.name,
                elapsed_ms=stop(),
                metadata={"detection": det.detail, "stealth": True},
            )
        except Exception as exc:
            r = self._error(url, exc)
            r.elapsed_ms = stop()
            return r
