"""Baemin (배달의민족) client — resilient API surface.

Working path (2026-07, no app session required):
  food-shop-list.baemin.com/api/display-group/FOOD_CATEGORY/.../shops
  Headers: X-BAEMIN-LATITUDE / X-BAEMIN-LONGITUDE / X-BAEMIN-DEVICE-ID

App-only hosts (search-gateway WAF, bm-store-api DNS) still need mitm capture.
"""

from __future__ import annotations

import json
import time
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

# ── Endpoint catalogs ──────────────────────────────────────────────

REVIEW_ENDPOINTS = [
    Endpoint("https://review-api.baemin.com/v1/reviews", verified=True),
    Endpoint("https://review-api.baemin.com/v1/reviews/{review_id}", verified=True),
    Endpoint(
        "https://review-api.baemin.com/v1/members/me/reviews",
        verified=True,
        needs_auth=True,
    ),
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

# Webview food list — confirmed live without login (geo headers required)
FOOD_LIST_BASE = "https://food-shop-list.baemin.com"
FOOD_LIST_ENDPOINTS = [
    Endpoint(f"{FOOD_LIST_BASE}/api/display-group/{{group}}", verified=True),
    Endpoint(
        f"{FOOD_LIST_BASE}/api/display-group/{{group}}"
        "/display-category/{category}/shops",
        verified=True,
    ),
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

DEFAULT_DISPLAY_GROUP = "FOOD_CATEGORY"
DEFAULT_CATEGORY = "FOOD_CATEGORY_ALL"
DEFAULT_PAGE_SIZE = 20


@dataclass
class BaeminConfig:
    capture_file: str | Path | None = None
    rate: float = 0.5
    burst: float = 3.0
    cache_ttl: float = 300.0
    cache_dir: str | Path = ".crawl_cache/baemin"
    max_retries: int = 3
    timeout: int = 15
    # Default geo (Seoul Gangnam) — override per call
    lat: float = 37.4979
    lng: float = 127.0276
    app_version: str = "16.15.0"
    os_version: str = "14"


@dataclass
class BaeminResult:
    ok: bool
    endpoint: str = ""
    status_code: int | None = None
    data: Any = None
    error: str = ""
    headers_used: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BaeminShop:
    """Normalized shop card from food-shop-list."""

    number: int | str
    name: str
    star_score: float = 0.0
    latest_review_count: int = 0
    is_baemin_club: bool = False
    is_new: bool = False
    deep_link: str = ""
    menus: tuple[dict[str, Any], ...] = ()
    thumbnail: str = ""
    raw: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)

    @property
    def rank_key(self) -> tuple[int, float]:
        return (self.latest_review_count, self.star_score)


def normalize_shop(item: dict[str, Any]) -> BaeminShop | None:
    """Normalize a list item (`{shop: {...}}` or bare shop dict)."""
    shop = item.get("shop") if isinstance(item.get("shop"), dict) else item
    if not isinstance(shop, dict):
        return None
    name = str(shop.get("name") or "").strip()
    number = shop.get("number") or shop.get("id") or shop.get("shopNumber")
    if not name or number is None:
        return None
    statics = shop.get("statics") if isinstance(shop.get("statics"), dict) else {}
    try:
        score = float(statics.get("starScore") or shop.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    try:
        reviews = int(
            statics.get("latestReviewCount")
            or shop.get("reviewCount")
            or shop.get("latestReviewCount")
            or 0
        )
    except (TypeError, ValueError):
        reviews = 0
    thumbs = shop.get("thumbnailImages") or []
    thumb = ""
    if isinstance(thumbs, list) and thumbs:
        thumb = str(thumbs[0])
    menus_raw = shop.get("menus") if isinstance(shop.get("menus"), list) else []
    menus: list[dict[str, Any]] = []
    for m in menus_raw[:10]:
        if not isinstance(m, dict):
            continue
        price = m.get("price") if isinstance(m.get("price"), dict) else {}
        menus.append(
            {
                "id": m.get("id"),
                "name": m.get("name"),
                "price": price.get("value"),
            }
        )
    return BaeminShop(
        number=number,
        name=name,
        star_score=score,
        latest_review_count=reviews,
        is_baemin_club=bool(shop.get("isBaeminClub")),
        is_new=bool(shop.get("isNew")),
        deep_link=str(shop.get("shopDetailDeepLink") or ""),
        menus=tuple(menus),
        thumbnail=thumb,
        raw=shop,
    )


def rank_shops(
    shops: list[BaeminShop],
    *,
    by: str = "reviews",
    limit: int = 20,
) -> list[BaeminShop]:
    """Sort shops by reviews (default) or score."""

    def by_score(s: BaeminShop) -> tuple[float, int]:
        return (s.star_score, s.latest_review_count)

    def by_reviews(s: BaeminShop) -> tuple[int, float]:
        return (s.latest_review_count, s.star_score)

    key = by_score if by == "score" else by_reviews
    return sorted(shops, key=key, reverse=True)[:limit]


def shops_to_markdown(shops: list[BaeminShop], *, title: str = "Baemin shops") -> str:
    lines = [f"# {title}", "", f"count: {len(shops)}", ""]
    for i, s in enumerate(shops, 1):
        lines.append(
            f"{i}. **{s.name}** — reviews {s.latest_review_count:,} · "
            f"★{s.star_score:.2f} · `{s.number}`"
        )
        if s.menus:
            top = s.menus[0]
            price = top.get("price")
            price_s = f" {price:,}원" if isinstance(price, int) else ""
            lines.append(f"   - {top.get('name')}{price_s}")
    return "\n".join(lines) + "\n"


class BaeminClient:
    """Baemin API client with rate limit, cache, TLS rotation, geo headers."""

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

    def _make_headers(
        self,
        extra: dict[str, str] | None = None,
        *,
        lat: float | None = None,
        lng: float | None = None,
    ) -> dict[str, str]:
        h = self.headers.get(extra)
        lat_v = self.cfg.lat if lat is None else lat
        lng_v = self.cfg.lng if lng is None else lng
        if not self.headers.loaded:
            h.setdefault("Accept", "application/json")
            h.setdefault("Accept-Language", "ko-KR")
            h.setdefault(
                "User-Agent",
                "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
                "AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36",
            )
            h.setdefault("X-BAEMIN-DEVICE-ID", self._device_id)
            h.setdefault("X-BAEMIN-CLIENT-ADID", self._device_id)
            h.setdefault("X-TRACE-ID", str(uuid.uuid4()))
            h.setdefault("Request-Sent-Timestamp", str(int(time.time() * 1000)))
            h.setdefault("SESSION-ID", str(uuid.uuid4()))
            h.setdefault("App-Id", "com.sampleapp")
            h.setdefault("Client-SDK", self.cfg.app_version)
            h.setdefault("DEVICE-BAEDAL", "android")
            h.setdefault("Protocol-Version", "2.0")
            h.setdefault("X-BAEMIN-USER-AGENT", f"BAEMINAPP_AND_{self.cfg.app_version}")
            h.setdefault("Referer", "https://web.baemin.com/food/shops")
            h.setdefault("Origin", "https://web.baemin.com")
        # Geo headers always win for list API (webview contract)
        h["X-BAEMIN-LATITUDE"] = str(lat_v)
        h["X-BAEMIN-LONGITUDE"] = str(lng_v)
        return h

    def _platform_qs(self) -> dict[str, str]:
        return {
            "os": "ANDROID",
            "osCode": "2",
            "osVersion": self.cfg.os_version,
            "appVersion": self.cfg.app_version,
            "dongCode": "",
            "adjustId": "",
            "idfv": "",
            "perseusClientId": "",
            "perseusSessionId": "",
        }

    def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        *,
        lat: float | None = None,
        lng: float | None = None,
    ) -> BaeminResult:
        from curl_cffi import requests as cffi

        lat_v = self.cfg.lat if lat is None else lat
        lng_v = self.cfg.lng if lng is None else lng
        # Geo is sent as headers — must be part of the cache key
        cache_key = (
            f"{url}?{json.dumps(params or {}, sort_keys=True)}"
            f"&geo={lat_v:.6f},{lng_v:.6f}"
        )
        cached = self.cache.get(cache_key)
        if cached:
            return BaeminResult(
                ok=True,
                endpoint=url,
                status_code=cached.get("_code", 200),
                data=cached.get("data", cached),
                headers_used={},
            )

        self.bucket.acquire()
        headers = self._make_headers(extra_headers, lat=lat_v, lng=lng_v)
        imp = self.rotator.next()

        def do_request() -> BaeminResult:
            resp = cffi.get(
                url,
                params=params,
                headers=headers,
                impersonate=imp,
                timeout=self.cfg.timeout,
            )
            if resp.status_code == 403:
                self.rotator.mark_failed(imp)
            try:
                data: Any = resp.json()
            except Exception:
                data = resp.text[:500]
            if resp.status_code < 400:
                self.cache.put(cache_key, {"_code": resp.status_code, "data": data})
                return BaeminResult(
                    ok=True,
                    endpoint=url,
                    status_code=resp.status_code,
                    data=data,
                    headers_used=headers,
                )
            return BaeminResult(
                ok=False,
                endpoint=url,
                status_code=resp.status_code,
                error=str(data)[:200],
                headers_used=headers,
            )

        try:
            return retry(do_request, policy=self.retry_policy, on_retry=lambda a, e: None)
        except Exception as exc:
            return BaeminResult(ok=False, endpoint=url, error=str(exc)[:200])

    # ── Public API ──

    def gateway_tabs(self) -> BaeminResult:
        """Gateway review tabs — works without auth."""
        return self._get("https://gateway-api.baemin.com/v1/tabs/review")

    def display_group(
        self,
        group: str = DEFAULT_DISPLAY_GROUP,
        *,
        lat: float | None = None,
        lng: float | None = None,
    ) -> BaeminResult:
        """Fetch display group metadata (categories)."""
        url = f"{FOOD_LIST_BASE}/api/display-group/{group}"
        return self._get(url, params=self._platform_qs(), lat=lat, lng=lng)

    def list_shops(
        self,
        *,
        lat: float | None = None,
        lng: float | None = None,
        group: str = DEFAULT_DISPLAY_GROUP,
        category: str | None = None,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
        exclude_shop_numbers: list[str | int] | None = None,
    ) -> BaeminResult:
        """List shops near lat/lng via food-shop-list (no login).

        Returns raw API JSON in ``result.data``. Prefer :meth:`iter_shops`
        / :meth:`collect_shops` for normalized cards.
        """
        cat = category or f"{group}_ALL"
        params: dict[str, Any] = {
            **self._platform_qs(),
            "shops.excludeShops": ",".join(str(x) for x in (exclude_shop_numbers or [])),
            "shops.limit": str(limit),
            "shops.offset": str(offset),
        }
        url = (
            f"{FOOD_LIST_BASE}/api/display-group/{group}"
            f"/display-category/{cat}/shops"
        )
        return self._get(url, params=params, lat=lat, lng=lng)

    def collect_shops(
        self,
        *,
        lat: float | None = None,
        lng: float | None = None,
        group: str = DEFAULT_DISPLAY_GROUP,
        category: str | None = None,
        max_shops: int = 100,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> list[BaeminShop]:
        """Paginate list_shops and return normalized unique shops."""
        out: list[BaeminShop] = []
        seen: set[str] = set()
        exclude: list[str] = []
        pages = max(1, (max_shops + page_size - 1) // page_size)
        for page in range(pages):
            res = self.list_shops(
                lat=lat,
                lng=lng,
                group=group,
                category=category,
                offset=page * page_size,
                limit=page_size,
                exclude_shop_numbers=exclude,
            )
            if not res.ok:
                break
            payload = res.data
            if isinstance(payload, dict) and "data" in payload:
                payload = payload["data"]
            raw_shops = []
            if isinstance(payload, dict):
                raw_shops = payload.get("shops") or []
            if not raw_shops:
                break
            for item in raw_shops:
                shop = normalize_shop(item if isinstance(item, dict) else {})
                if shop is None:
                    continue
                key = str(shop.number)
                if key in seen:
                    continue
                seen.add(key)
                exclude.append(key)
                out.append(shop)
                if len(out) >= max_shops:
                    return out
        return out

    def search(self, query: str, lat: float = 37.4979, lng: float = 127.0276) -> BaeminResult:
        """Search shops via search-gateway (often 403 without capture)."""
        chain = EndpointChain(SEARCH_ENDPOINTS, self.rotator)
        result = chain.fetch(
            params={"query": query, "lat": lat, "lng": lng, "page": 1},
            headers=self._make_headers(lat=lat, lng=lng),
            timeout=self.cfg.timeout,
        )
        if result:
            ep, resp = result
            try:
                return BaeminResult(
                    ok=True, endpoint=ep.url, status_code=resp.status_code, data=resp.json()
                )
            except Exception:
                return BaeminResult(
                    ok=True,
                    endpoint=ep.url,
                    status_code=resp.status_code,
                    data=resp.text[:500],
                )
        return BaeminResult(
            ok=False,
            endpoint="search-gateway",
            error=(
                "search-gateway blocked. Use list_shops(lat,lng) or mitm capture. "
                f"query={query!r}"
            ),
        )

    def reviews(self, shop_id: str, page: int = 1, size: int = 20) -> BaeminResult:
        """Shop reviews — usually needs captured app headers."""
        return self._get(
            "https://review-api.baemin.com/v1/reviews",
            params={"shopId": shop_id, "page": page, "size": size},
        )

    def review_detail(self, review_id: str) -> BaeminResult:
        return self._get(f"https://review-api.baemin.com/v1/reviews/{review_id}")

    def address_search(self, query: str) -> BaeminResult:
        return self._get(
            "https://location-api.baemin.com/v1/search/address",
            params={"query": query},
        )

    def webview_page(self, path: str, params: dict[str, Any] | None = None) -> BaeminResult:
        return self._get(f"https://web.baemin.com{path}", params=params)

    def status(self) -> dict[str, Any]:
        return {
            "headers_loaded": self.headers.loaded,
            "device_id": self._device_id,
            "rate": self.cfg.rate,
            "cache_dir": str(self.cfg.cache_dir),
            "default_geo": {"lat": self.cfg.lat, "lng": self.cfg.lng},
            "verified_endpoints": {
                "review": len(REVIEW_ENDPOINTS),
                "search": len(SEARCH_ENDPOINTS),
                "shop": len(SHOP_ENDPOINTS),
                "food_list": len(FOOD_LIST_ENDPOINTS),
                "gateway": len(GATEWAY_ENDPOINTS),
                "webview": len(WEBVIEW_ENDPOINTS),
                "location": len(LOCATION_ENDPOINTS),
            },
            "live_path": (
                f"{FOOD_LIST_BASE}/api/display-group/{DEFAULT_DISPLAY_GROUP}"
                f"/display-category/{DEFAULT_CATEGORY}/shops"
            ),
        }
