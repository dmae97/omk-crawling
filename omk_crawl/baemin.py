"""Baemin (배달의민족) API client — APK-verified endpoints only.

Package: com.sampleapp v16.15.0
All endpoints extracted from DEX reverse engineering (1,226 API references).

Bottleneck resolutions:
  - 400 '필수 헤더 누락' → HeaderStore loads mitmproxy-captured headers
  - 403 WAF on search-gateway → ImpersonateRotator cycles TLS fingerprints
  - DNS failure on bm-store-api → EndpointChain falls back to reachable hosts
  - 429 rate-limit → TokenBucket + RetryPolicy with backoff
  - CSR webview → Playwright fallback for web.baemin.com pages
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omk_crawl.resilience import (
    Endpoint,
    EndpointChain,
    HeaderStore,
    ImpersonateRotator,
    ResponseCache,
    RetryPolicy,
    TokenBucket,
    retry,
)

# ─────────────────────────────────────────────
# Verified endpoints (from APK DEX analysis)
# ─────────────────────────────────────────────

REVIEW_ENDPOINTS = [
    Endpoint("https://review-api.baemin.com/v1/reviews", verified=True),
    Endpoint("https://review-api.baemin.com/v1/reviews/{review_id}", verified=True),
    Endpoint("https://review-api.baemin.com/v1/members/me/reviews", verified=True, needs_auth=True),
]

SEARCH_ENDPOINTS = [
    Endpoint("https://search-gateway.baemin.com/v1/search", verified=True),
    Endpoint("https://search-gateway.baemin.com/v3/home/food", verified=True),
    Endpoint("https://search-gateway.baemin.com/v2/home/food", verified=True),
]

SHOP_ENDPOINTS = [
    Endpoint("https://shop-detail-api.baemin.com/", verified=True),
    Endpoint("https://web.baemin.com/food/shops", verified=True),
    Endpoint("https://web.baemin.com/food/shopDetail", verified=True),
]

GATEWAY_ENDPOINTS = [
    Endpoint("https://gateway-api.baemin.com/v1/tabs/review", verified=True),
    Endpoint("https://gateway-api.baemin.com/v1/tabs/zzim", verified=True),
    Endpoint("https://gateway-api.baemin.com/v1/search-placeholders", verified=True),
    Endpoint("https://gateway-api.baemin.com/v4/gateway/elements", verified=True),
]

WEBVIEW_ENDPOINTS = [
    Endpoint("https://web.baemin.com/food/shopReviews", verified=True),
    Endpoint("https://web.baemin.com/food/shops", verified=True),
    Endpoint("https://web.baemin.com/search/commerce/shop", verified=True),
    Endpoint("https://web.baemin.com/commerce/home", verified=True),
]

LOCATION_ENDPOINTS = [
    Endpoint("https://location-api.baemin.com/v1/search/address", verified=True),
]

# Baemin-specific headers found in DEX
BAEMIN_HEADERS = {
    "X-BAEMIN-DEVICE-ID": "",  # filled at runtime
    "X-BAEMIN-MEMBER-NUMBER": "",
    "X-BAEMIN-AML-SESSION": "",
    "X-TRACE-ID": "",
    "App-Id": "com.sampleapp",
    "Client-SDK": "16.15.0",
    "DEVICE-BAEDAL": "android",
    "Device-Height": "2400",
    "Device-Width": "1080",
    "Protocol-Version": "2.0",
    "Request-Sent-Timestamp": "",
    "SESSION-ID": "",
}


@dataclass
class BaeminConfig:
    capture_file: str | Path | None = None
    rate: float = 0.5  # requests/sec (conservative)
    burst: float = 3.0
    cache_ttl: float = 300.0
    cache_dir: str | Path = ".crawl_cache/baemin"
    max_retries: int = 3
    timeout: int = 10


@dataclass
class BaeminResult:
    ok: bool
    endpoint: str = ""
    status_code: int | None = None
    data: Any = None
    error: str = ""
    headers_used: dict[str, str] = field(default_factory=dict)


class BaeminClient:
    """Baemin API client with full resilience stack."""

    def __init__(self, config: BaeminConfig | None = None) -> None:
        self.cfg = config or BaeminConfig()
        self.bucket = TokenBucket(rate=self.cfg.rate, capacity=self.cfg.burst)
        self.cache = ResponseCache(cache_dir=self.cfg.cache_dir, ttl=self.cfg.cache_ttl)
        self.rotator = ImpersonateRotator()
        self.headers = HeaderStore(capture_file=self.cfg.capture_file)
        self.retry_policy = RetryPolicy(
            max_retries=self.cfg.max_retries,
            retryable_statuses=frozenset({429, 500, 502, 503, 504}),
        )
        self._device_id = str(uuid.uuid4())

    def _make_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Build headers with runtime values filled in."""
        import time
        h = self.headers.get(extra)
        # Fill runtime Baemin headers if not already captured
        if not self.headers.loaded:
            h.setdefault("X-BAEMIN-DEVICE-ID", self._device_id)
            h.setdefault("X-TRACE-ID", str(uuid.uuid4()))
            h.setdefault("Request-Sent-Timestamp", str(int(time.time() * 1000)))
            h.setdefault("SESSION-ID", str(uuid.uuid4()))
            h.setdefault("App-Id", "com.sampleapp")
            h.setdefault("Client-SDK", "16.15.0")
        return h

    def _get(self, url: str, params: dict[str, Any] | None = None,
             extra_headers: dict[str, str] | None = None) -> BaeminResult:
        """Rate-limited, retried GET with caching."""
        from curl_cffi import requests as cffi

        cache_key = f"{url}?{json.dumps(params or {}, sort_keys=True)}"
        cached = self.cache.get(cache_key)
        if cached:
            return BaeminResult(ok=True, endpoint=url, status_code=cached.get("_code", 200),
                                data=cached, headers_used={})

        self.bucket.acquire()
        headers = self._make_headers(extra_headers)
        imp = self.rotator.next()

        def do_request() -> BaeminResult:
            resp = cffi.get(url, params=params, headers=headers,
                            impersonate=imp, timeout=self.cfg.timeout)
            if resp.status_code == 403:
                self.rotator.mark_failed(imp)
            try:
                data = resp.json()
            except Exception:
                data = resp.text[:500]

            if resp.status_code < 400:
                self.cache.put(cache_key, {"_code": resp.status_code, "data": data})
                return BaeminResult(ok=True, endpoint=url, status_code=resp.status_code,
                                    data=data, headers_used=headers)
            return BaeminResult(ok=False, endpoint=url, status_code=resp.status_code,
                                error=str(data)[:200], headers_used=headers)

        try:
            return retry(do_request, policy=self.retry_policy,
                         on_retry=lambda a, e: None)
        except Exception as exc:
            return BaeminResult(ok=False, endpoint=url, error=str(exc)[:200])

    # ── Public API ──

    def gateway_tabs(self) -> BaeminResult:
        """Gateway review tabs — works without auth (200 confirmed)."""
        return self._get("https://gateway-api.baemin.com/v1/tabs/review")

    def search(self, query: str, lat: float = 37.4979, lng: float = 127.0276) -> BaeminResult:
        """Search shops. May hit 403 WAF without captured headers."""
        chain = EndpointChain(SEARCH_ENDPOINTS, self.rotator)
        result = chain.fetch(
            params={"query": query, "lat": lat, "lng": lng, "page": 1},
            headers=self._make_headers(),
            timeout=self.cfg.timeout,
        )
        if result:
            ep, resp = result
            try:
                return BaeminResult(ok=True, endpoint=ep.url, status_code=resp.status_code,
                                    data=resp.json())
            except Exception:
                return BaeminResult(ok=True, endpoint=ep.url, status_code=resp.status_code,
                                    data=resp.text[:500])
        return BaeminResult(ok=False, endpoint="search-gateway",
                            error="All search endpoints failed (WAF/403). Use mitmproxy capture.")

    def reviews(self, shop_id: str, page: int = 1, size: int = 20) -> BaeminResult:
        """Get shop reviews. Needs captured headers for full access."""
        return self._get(
            "https://review-api.baemin.com/v1/reviews",
            params={"shopId": shop_id, "page": page, "size": size},
        )

    def review_detail(self, review_id: str) -> BaeminResult:
        """Get single review by ID."""
        return self._get(f"https://review-api.baemin.com/v1/reviews/{review_id}")

    def address_search(self, query: str) -> BaeminResult:
        """Search address → coordinates."""
        return self._get(
            "https://location-api.baemin.com/v1/search/address",
            params={"query": query},
        )

    def webview_page(self, path: str, params: dict[str, Any] | None = None) -> BaeminResult:
        """Fetch web.baemin.com webview page (HTML). Needs app context for data."""
        url = f"https://web.baemin.com{path}"
        return self._get(url, params=params)

    def status(self) -> dict[str, Any]:
        """Client status report."""
        return {
            "headers_loaded": self.headers.loaded,
            "device_id": self._device_id,
            "rate": self.cfg.rate,
            "cache_dir": str(self.cfg.cache_dir),
            "verified_endpoints": {
                "review": len(REVIEW_ENDPOINTS),
                "search": len(SEARCH_ENDPOINTS),
                "shop": len(SHOP_ENDPOINTS),
                "gateway": len(GATEWAY_ENDPOINTS),
                "webview": len(WEBVIEW_ENDPOINTS),
                "location": len(LOCATION_ENDPOINTS),
            },
        }
