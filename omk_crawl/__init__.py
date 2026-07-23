"""omk-crawl — Smart web crawling toolbox.

Auto-routes across 10 tools with escalation:
  curl_cffi → crawl4ai → scrapling → browser-use

Usage:
    from omk_crawl import crawl
    result = crawl("https://example.com")
    print(result.markdown)
"""

from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.router import SmartRouter, crawl, crawl_async

__all__ = ["CrawlResult", "CrawlStatus", "SmartRouter", "crawl", "crawl_async"]
__version__ = "2.0.0"
