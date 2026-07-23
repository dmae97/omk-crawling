"""omk-crawl — Smart web crawling toolbox.

Auto-escalating router across 6 adapters:
  curl_cffi → crawl4ai → scrapling → browser-use (+ autoscraper, markitdown)

Usage:
    from omk_crawl import crawl
    result = crawl("https://example.com")
    print(result.markdown)
"""

from omk_crawl.adaptive import (
    AdaptiveConfig,
    AdaptiveFetcher,
    CapturedCall,
    FetchResult,
)
from omk_crawl.async_batch import (
    AsyncBatchFetcher,
    BatchConfig,
    BatchItem,
    BatchResult,
)
from omk_crawl.baemin import BaeminClient, BaeminConfig, BaeminResult
from omk_crawl.cookies import Cookie, CookieManager
from omk_crawl.naver import NaverCafeClient, NaverConfig, NaverLandClient, NaverResult
from omk_crawl.resilience import (
    Endpoint,
    EndpointChain,
    HeaderStore,
    ImpersonateRotator,
    ResponseCache,
    RetryPolicy,
    TokenBucket,
    ensure_playwright,
    retry,
)
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.router import SmartRouter, crawl, crawl_async
from omk_crawl.stability import (
    BreakerRegistry,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    SessionManager,
    TimeoutBudget,
    get_logger,
)

__all__ = [
    "CrawlResult", "CrawlStatus", "SmartRouter", "crawl", "crawl_async",
    # resilience
    "TokenBucket", "RetryPolicy", "retry", "ResponseCache", "HeaderStore",
    "ImpersonateRotator", "EndpointChain", "Endpoint", "ensure_playwright",
    # stability
    "CircuitBreaker", "CircuitState", "CircuitOpenError", "BreakerRegistry",
    "SessionManager", "TimeoutBudget", "get_logger",
    # adaptive
    "AdaptiveFetcher", "AdaptiveConfig", "FetchResult", "CapturedCall",
    # async batch
    "AsyncBatchFetcher", "BatchConfig", "BatchItem", "BatchResult",
    # cookies (your own session)
    "CookieManager", "Cookie",
    # targets
    "BaeminClient", "BaeminConfig", "BaeminResult",
    "NaverLandClient", "NaverCafeClient", "NaverConfig", "NaverResult",
]
__version__ = "2.5.0"
