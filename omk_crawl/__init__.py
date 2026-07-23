"""omk-crawl — Smart web crawling toolbox.

Auto-escalating router across 6 adapters:
  curl_cffi → crawl4ai → scrapling → browser-use (+ autoscraper, markitdown)

Usage:
    from omk_crawl import crawl
    result = crawl("https://example.com")
    print(result.markdown)
"""

from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.router import SmartRouter, crawl, crawl_async

__all__ = ["CrawlResult", "CrawlStatus", "SmartRouter", "crawl", "crawl_async"]
__version__ = "2.0.0"
