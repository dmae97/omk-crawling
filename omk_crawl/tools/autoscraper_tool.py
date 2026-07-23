"""autoscraper adapter — example-based extraction rule learning."""

from __future__ import annotations

from typing import Any

from omk_crawl.result import CrawlResult, CrawlStatus, _timer
from omk_crawl.tools.base import BaseTool


class AutoscraperTool(BaseTool):
    name = "autoscraper"
    pip_package = "autoscraper"
    layer = 3
    needs_browser = False

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        """Learn extraction rules from examples and apply.

        kwargs:
            wanted_list: list of example values to learn from
            similar_url: URL to apply learned rules to (default: same url)
            model_path: path to save/load learned model
        """
        if not self.available():
            return self._missing(url)
        _, stop = _timer()
        try:
            from autoscraper import AutoScraper

            scraper = AutoScraper()
            wanted = kwargs.get("wanted_list", [])
            model_path = kwargs.get("model_path")

            if model_path:
                try:
                    scraper.load(model_path)
                except FileNotFoundError:
                    if not wanted:
                        return CrawlResult(
                            url=url,
                            status=CrawlStatus.ERROR,
                            tool=self.name,
                            error=f"No model at {model_path} and no wanted_list to learn",
                        )
                    scraper.build(url, wanted_list=wanted)
                    scraper.save(model_path)
            elif wanted:
                scraper.build(url, wanted_list=wanted)
            else:
                return CrawlResult(
                    url=url,
                    status=CrawlStatus.ERROR,
                    tool=self.name,
                    error="Provide wanted_list or model_path",
                )

            target = kwargs.get("similar_url", url)
            results = scraper.get_result_similar(target)
            return CrawlResult(
                url=url,
                status=CrawlStatus.OK,
                extracted=[{"value": v} for v in results],
                tool=self.name,
                elapsed_ms=stop(),
                metadata={"matches": len(results)},
            )
        except Exception as exc:
            r = self._error(url, exc)
            r.elapsed_ms = stop()
            return r
