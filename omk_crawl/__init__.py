"""omk-crawl — Smart crawling toolbox (web + Android/iOS).

Web auto-escalation:
  insane_search → curl_cffi → crawl4ai → scrapling → camoufox → patchright
  → nodriver → browser-use
Breakthrough layer (v2.12): cross-layer fingerprint coherence (fingerprint.py),
seeded human behavior (behavior.py), session warm-up & clearance reuse (warmup.py).
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
from omk_crawl.behavior import BehaviorClock, Keystroke, SessionPhase
from omk_crawl.browser_props import (
    UNSUPPORTED_SURFACES,
    BrowserPropertySpec,
    audit_props,
    property_spec,
    spoof_script,
    webgl_identity,
)
from omk_crawl.captcha import (
    CaptchaChallenge,
    CaptchaKind,
    CaptchaPlan,
    HttpSolver,
    MockSolver,
    NullSolver,
    SolverBackend,
    SolveResult,
    SolveStatus,
    classify_captcha,
    resolve_challenge,
)
from omk_crawl.cdp import (
    CDP_TELLS,
    DRIVER_FIXED,
    PatchPlan,
    audit_cdp,
    full_script,
    patch_plan,
)
from omk_crawl.cdp import (
    probe_script as cdp_probe_script,
)
from omk_crawl.cookies import Cookie, CookieManager
from omk_crawl.evasion import CoherenceReport, EvasionPlan, plan_for
from omk_crawl.fingerprint import (
    PROFILES,
    FingerprintProfile,
    coherence_issues,
    match_impersonate,
    profile_for,
)
from omk_crawl.har import analyze_har
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
from omk_crawl.tcp import EmulationReport, TcpStackProfile, apply_to_socket, stack_for
from omk_crawl.tls import TlsClientHello, hello_for, normalize_grease
from omk_crawl.tools.x_search import XSearchTool, x_search
from omk_crawl.trends import (
    KNOWN_WOEIDS,
    get_trends,
    trend_to_tweets,
    trending_with_content,
)
from omk_crawl.verify import (
    BehaviorSummary,
    RequestSurface,
    Verdict,
    evasion_surface,
    naive_stealth_surface,
    stock_surface,
)
from omk_crawl.verify import score as evasion_score
from omk_crawl.warmup import (
    CLEARANCE_COOKIES,
    SessionWarmup,
    WarmSession,
    warm_crawl,
)

__all__ = [
    "CrawlResult",
    "CrawlStatus",
    "SmartRouter",
    "crawl",
    "crawl_async",
    "analyze_har",
    # breakthrough (v2.12): fingerprint coherence + behavior + warm sessions
    "FingerprintProfile",
    "PROFILES",
    "profile_for",
    "match_impersonate",
    "coherence_issues",
    "BehaviorClock",
    "SessionWarmup",
    "WarmSession",
    "warm_crawl",
    "CLEARANCE_COOKIES",
    # deep evasion (v2.14): TCP stack, TLS model, CDP patches, JS surface,
    # behavior extensions, challenge policy, and the offline self-check
    "EvasionPlan",
    "CoherenceReport",
    "plan_for",
    "TcpStackProfile",
    "EmulationReport",
    "stack_for",
    "apply_to_socket",
    "TlsClientHello",
    "hello_for",
    "normalize_grease",
    "PatchPlan",
    "patch_plan",
    "full_script",
    "audit_cdp",
    "cdp_probe_script",
    "CDP_TELLS",
    "DRIVER_FIXED",
    "BrowserPropertySpec",
    "property_spec",
    "spoof_script",
    "audit_props",
    "webgl_identity",
    "UNSUPPORTED_SURFACES",
    "Keystroke",
    "SessionPhase",
    "CaptchaKind",
    "CaptchaChallenge",
    "CaptchaPlan",
    "SolveStatus",
    "SolveResult",
    "SolverBackend",
    "NullSolver",
    "MockSolver",
    "HttpSolver",
    "classify_captcha",
    "resolve_challenge",
    "RequestSurface",
    "BehaviorSummary",
    "Verdict",
    "evasion_score",
    "stock_surface",
    "naive_stealth_surface",
    "evasion_surface",
    # resilience
    "TokenBucket",
    "RetryPolicy",
    "retry",
    "ResponseCache",
    "HeaderStore",
    "ImpersonateRotator",
    "EndpointChain",
    "Endpoint",
    "ensure_playwright",
    # stability
    "CircuitBreaker",
    "CircuitState",
    "CircuitOpenError",
    "BreakerRegistry",
    "SessionManager",
    "TimeoutBudget",
    "get_logger",
    # adaptive
    "AdaptiveFetcher",
    "AdaptiveConfig",
    "FetchResult",
    "CapturedCall",
    # async batch
    "AsyncBatchFetcher",
    "BatchConfig",
    "BatchItem",
    "BatchResult",
    # cookies (your own session)
    "CookieManager",
    "Cookie",
    # targets
    "BaeminClient",
    "BaeminConfig",
    "BaeminResult",
    "BaeminShop",
    "normalize_shop",
    "rank_shops",
    "shops_to_markdown",
    "NaverLandClient",
    "NaverCafeClient",
    "NaverConfig",
    "NaverResult",
    "RedditClient",
    "RedditConfig",
    "RedditPost",
    "RedditResult",
    "posts_to_markdown",
    # mobile
    "AdbDevice",
    "AppStoreApp",
    "AppStoreClient",
    "analyze_apk",
    "analyze_ipa",
    "list_adb_devices",
    # x_search (v2.13): session-based X search, no API/OAuth
    "XSearchTool",
    "x_search",
    # trends (v2.13): guest-token X trends, no auth
    "KNOWN_WOEIDS",
    "get_trends",
    "trend_to_tweets",
    "trending_with_content",
]
__version__ = "2.14.0"
