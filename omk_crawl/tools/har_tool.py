"""Local HAR adapter; deliberately excluded from web escalation."""

from __future__ import annotations

from typing import Any

from omk_crawl.har import analyze_har
from omk_crawl.result import CrawlResult
from omk_crawl.tools.base import BaseTool


class HarTool(BaseTool):
    name = "har"
    layer = 3

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        result = analyze_har(
            url,
            include_bodies=kwargs.get("include_bodies", False),
            max_bytes=kwargs.get("max_bytes", 32 * 1024 * 1024),
            max_entries=kwargs.get("max_entries", 10_000),
            max_body_bytes=kwargs.get("max_body_bytes", 2 * 1024 * 1024),
        )
        result.metadata.update(self.contract_metadata(kwargs))
        return result
