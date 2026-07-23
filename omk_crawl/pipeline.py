"""Pipeline — compose fetch → extract → convert steps."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.router import SmartRouter


@dataclass
class Pipeline:
    """Composable crawl pipeline.

    Usage:
        p = Pipeline()
        p.fetch()                          # auto-escalating fetch
        p.extract_css("div.product", {     # CSS extraction
            "title": "h2", "price": ".price"
        })
        p.to_markdown()                    # ensure markdown output
        result = p.run("https://example.com")
    """

    steps: list[Callable[[CrawlResult], CrawlResult]] = field(default_factory=list)
    router: SmartRouter = field(default_factory=SmartRouter)

    def fetch(self, tool: str | None = None, **kwargs: Any) -> Pipeline:
        """Step 1: fetch with auto-escalation (or specific tool)."""
        def _fetch(r: CrawlResult) -> CrawlResult:
            if tool:
                self.router.tools = [tool]
            return self.router.crawl(r.url, **kwargs)
        self.steps.append(_fetch)
        return self

    def extract_css(self, base: str, fields: dict[str, str]) -> Pipeline:
        """Step 2: CSS extraction on HTML (requires selectolax)."""
        def _extract(r: CrawlResult) -> CrawlResult:
            if not r.html:
                r.error = "No HTML to extract from"
                return r
            try:
                from selectolax.parser import HTMLParser

                tree = HTMLParser(r.html)
                rows = []
                for node in tree.css(base):
                    row = {}
                    for name, sel in fields.items():
                        child = node.css_first(sel)
                        row[name] = child.text(strip=True) if child else None
                    rows.append(row)
                r.extracted = rows
                r.metadata["extract_count"] = len(rows)
            except ImportError:
                # selectolax not installed — no extraction possible
                r.metadata["extract_note"] = "pip install selectolax for CSS extraction"
            return r
        self.steps.append(_extract)
        return self

    def to_markdown(self) -> Pipeline:
        """Step 3: ensure markdown output (uses markitdown for files, crawl4ai for web)."""
        def _convert(r: CrawlResult) -> CrawlResult:
            if r.markdown:
                return r  # already have markdown
            if r.html:
                try:
                    import os
                    import tempfile

                    from markitdown import MarkItDown

                    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
                        f.write(r.html)
                        path = f.name
                    try:
                        r.markdown = MarkItDown().convert(path).text_content
                    finally:
                        os.unlink(path)
                except ImportError:
                    # Strip tags as fallback (remove script/style first)
                    import html as html_mod
                    import re

                    text = re.sub(
                        r"<script[^>]*>.*?</script>", "", r.html,
                        flags=re.DOTALL | re.IGNORECASE,
                    )
                    text = re.sub(
                        r"<style[^>]*>.*?</style>", "", text,
                        flags=re.DOTALL | re.IGNORECASE,
                    )
                    text = re.sub(r"<[^>]+>", "", text)
                    text = html_mod.unescape(text).strip()
                    if text:
                        r.markdown = text
                    r.metadata["markdown_fallback"] = (
                        "tag-strip (pip install markitdown for proper conversion)"
                    )
            return r
        self.steps.append(_convert)
        return self

    def run(self, url: str) -> CrawlResult:
        """Execute the pipeline."""
        result = CrawlResult(url=url, status=CrawlStatus.OK)
        for step in self.steps:
            result = step(result)
            if result.status is CrawlStatus.ERROR:
                break
        return result
