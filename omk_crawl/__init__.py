"""omk-crawl — Smart crawling toolbox (web + Android/iOS).

Web auto-escalation:
  insane_search → curl_cffi → crawl4ai → scrapling → browser-use
Plus extract/convert (autoscraper, markitdown) and mobile (apk, ipa, adb/scrcpy).

Usage:
    from omk_crawl import crawl
    result = crawl("https://example.com")
    print(result.markdown)

    from omk_crawl.mobile import analyze_apk, list_adb_devices
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
from omk_crawl.baemin import (
    BaeminClient,
    BaeminConfig,
    BaeminResult,
    BaeminShop,
    normalize_shop,
    rank_shops,
    shops_to_markdown,
)
from omk_crawl.cookies import Cookie, CookieManager
from omk_crawl.mobile import (
    AdbDevice,
    AppStoreApp,
    AppStoreClient,
    analyze_apk,
    analyze_ipa,
    list_adb_devices,
)
from omk_crawl.naver import NaverCafeClient, NaverConfig, NaverLandClient, NaverResult
from omk_crawl.reddit import RedditClient, RedditConfig, RedditPost, RedditResult, posts_to_markdown
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
    "BaeminClient", "BaeminConfig", "BaeminResult", "BaeminShop",
    "normalize_shop", "rank_shops", "shops_to_markdown",
    "NaverLandClient", "NaverCafeClient", "NaverConfig", "NaverResult",
    "RedditClient", "RedditConfig", "RedditPost", "RedditResult", "posts_to_markdown",
    # mobile
    "AdbDevice", "AppStoreApp", "AppStoreClient",
    "analyze_apk", "analyze_ipa", "list_adb_devices",
]
__version__ = "2.11.0"
