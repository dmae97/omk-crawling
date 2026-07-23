"""Naver (네이버) public data client — Real Estate + Cafe.

Bottleneck resolutions:
  - land.naver.com/article/articleList.nhn 404 → use new.land.naver.com/api/*
  - CSR empty SSR data → Playwright fallback for JS-rendered pages
  - 429 rate-limit → TokenBucket (0.3 req/s) + RetryPolicy
  - Private cafe login wall → clearly scoped to public cafes only
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omk_crawl.resilience import (
    ResponseCache,
    RetryPolicy,
    TokenBucket,
    retry,
)

# ─────────────────────────────────────────────
# Verified endpoints
# ─────────────────────────────────────────────

# Naver Real Estate (new.land.naver.com)
LAND_API = "https://new.land.naver.com/api"
LAND_COMPLEXES = f"{LAND_API}/complexes/single-markers/2.0"
LAND_CORTARS = f"{LAND_API}/cortars"

# Naver Cafe (cafe.naver.com) — real REST API discovered via Playwright interception
# Article list: apis.naver.com/cafe-web/cafe-boardlist-api/v1/cafes/{clubid}/menus/{menuid}/articles
# (legacy ArticleList.nhn redirects to the React frontend /f-e/cafes/)
CAFE_BOARD_API = "https://apis.naver.com/cafe-web/cafe-boardlist-api/v1"
CAFE_MAIN_API = "https://apis.naver.com/cafe-web/cafe-cafemain-api/v1.0"

# Administrative district codes (cortarNo)
CORTAR_CODES: dict[str, str] = {
    "강남구 역삼동": "1168010100",
    "강남구 삼성동": "1168010400",
    "서초구 서초동": "1165010100",
    "송파구 잠실동": "1171010100",
    "마포구 망원동": "1144011400",
    "용산구 이태원동": "1117010800",
    "성동구 성수동": "1120010100",
    "영등포구 여의도동": "1156010200",
}

NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


@dataclass
class NaverConfig:
    rate: float = 0.3  # conservative: 1 req per 3s
    burst: float = 2.0
    cache_ttl: float = 600.0
    cache_dir: str | Path = ".crawl_cache/naver"
    max_retries: int = 3
    timeout: int = 10


@dataclass
class NaverResult:
    ok: bool
    endpoint: str = ""
    status_code: int | None = None
    data: Any = None
    error: str = ""


class NaverLandClient:
    """Naver Real Estate public data client."""

    def __init__(self, config: NaverConfig | None = None) -> None:
        self.cfg = config or NaverConfig()
        self.bucket = TokenBucket(rate=self.cfg.rate, capacity=self.cfg.burst)
        self.cache = ResponseCache(cache_dir=self.cfg.cache_dir, ttl=self.cfg.cache_ttl)
        self.retry_policy = RetryPolicy(
            max_retries=self.cfg.max_retries,
            base_delay=3.0,  # longer base for Naver rate limits
            retryable_statuses=frozenset({429, 500, 502, 503}),
        )

    def _get(self, url: str, params: dict[str, Any] | None = None,
             referer: str = "https://new.land.naver.com/") -> NaverResult:
        from curl_cffi import requests as cffi

        cache_key = f"{url}?{json.dumps(params or {}, sort_keys=True)}"
        cached = self.cache.get(cache_key)
        if cached:
            return NaverResult(ok=True, endpoint=url, status_code=cached.get("_code", 200),
                               data=cached.get("data"))

        self.bucket.acquire()
        headers = {**NAVER_HEADERS, "Referer": referer}

        def do_request() -> NaverResult:
            resp = cffi.get(url, params=params, headers=headers,
                            impersonate="chrome124", timeout=self.cfg.timeout)
            try:
                data = resp.json()
            except Exception:
                data = resp.text[:500]

            if resp.status_code < 400:
                self.cache.put(cache_key, {"_code": resp.status_code, "data": data})
                return NaverResult(ok=True, endpoint=url, status_code=resp.status_code, data=data)
            return NaverResult(ok=False, endpoint=url, status_code=resp.status_code,
                               error=str(data)[:200])

        try:
            return retry(do_request, policy=self.retry_policy)
        except Exception as exc:
            return NaverResult(ok=False, endpoint=url, error=str(exc)[:200])

    def complexes(self, cortar_no: str, zoom: int = 16,
                  price_type: str = "RETAIL") -> NaverResult:
        """Get apartment/complex markers for a district.

        Args:
            cortar_no: Administrative district code (e.g. '1168010100' for 역삼동).
            zoom: Map zoom level (default 16).
            price_type: RETAIL=매매, JEONSE=전세.
        """
        return self._get(LAND_COMPLEXES, params={
            "cortarNo": cortar_no, "zoom": zoom, "priceType": price_type,
        })

    def cortar_info(self, cortar_no: str) -> NaverResult:
        """Get district info."""
        return self._get(LAND_CORTARS, params={"cortarNo": cortar_no})

    def search_by_name(self, region: str, **kwargs: Any) -> NaverResult:
        """Search by Korean region name (auto-resolves cortarNo)."""
        code = CORTAR_CODES.get(region, region)
        return self.complexes(code, **kwargs)


class NaverCafeClient:
    """Naver Cafe client — powerful, robust, public-data focused.

    Discovered real API (reverse-engineered from the React frontend
    ca-fe.pstatic.net via Playwright network interception):

      Article list (NO login for public cafes):
        GET apis.naver.com/cafe-web/cafe-boardlist-api/v1/cafes/{clubid}/menus/{menuid}/articles
            ?page=1&pageSize=15&sortBy=TIME&viewType=L
      Notices:
        GET .../cafes/{clubid}/notices/menus/0
      Popular:
        GET .../cafes/{clubid}/uparticles/menus/0
      Menu list:
        GET apis.naver.com/cafe-web/cafe-cafemain-api/v1.0/cafes/{clubid}/menus

    Strategy ladder (via AdaptiveFetcher):
      1. direct  — curl_cffi browser-TLS to the REST API (fast path)
      2. render  — Playwright render + XHR interception (fallback / content)

    Legitimate session support: pass user_cookies (the caller's OWN logged-in
    browser cookies) to access cafes the caller is a member of. This reuses the
    caller's authorization — it never bypasses authentication.
    """

    BOARD_API = "https://apis.naver.com/cafe-web/cafe-boardlist-api/v1"
    MAIN_API = "https://apis.naver.com/cafe-web/cafe-cafemain-api/v1.0"
    FE_BASE = "https://cafe.naver.com/f-e/cafes"

    def __init__(self, config: NaverConfig | None = None,
                 user_cookies: dict[str, str] | None = None) -> None:
        self.cfg = config or NaverConfig()
        self.bucket = TokenBucket(rate=self.cfg.rate, capacity=self.cfg.burst)
        self.cache = ResponseCache(cache_dir=self.cfg.cache_dir, ttl=self.cfg.cache_ttl)
        self.retry_policy = RetryPolicy(
            max_retries=self.cfg.max_retries, base_delay=2.0,
            retryable_statuses=frozenset({429, 500, 502, 503}),
        )
        self._cookies = user_cookies
        self._fetcher: Any = None  # lazy AdaptiveFetcher

    @property
    def fetcher(self):
        if self._fetcher is None:
            from omk_crawl.adaptive import AdaptiveConfig, AdaptiveFetcher
            self._fetcher = AdaptiveFetcher(AdaptiveConfig(
                rate=self.cfg.rate, burst=self.cfg.burst,
                timeout=self.cfg.timeout, user_cookies=self._cookies,
            ))
        return self._fetcher

    # ── session (YOUR OWN account) ────────────────────────────
    def set_cookie_manager(self, mgr) -> NaverCafeClient:
        """Inject cookies loaded via CookieManager (your own browser session).

        Legal scope: reuse of YOUR authenticated session for content you are
        already authorized to see. Not a bypass.
        """
        self._cookies = mgr.to_curl_cffi()
        if self._fetcher is not None:
            self._fetcher.cfg.user_cookies = dict(self._cookies)
        return self

    def check_login(self) -> NaverResult:
        """Validate that the injected session is logged in to Naver.

        Works only with YOUR OWN valid session — never forges credentials.
        """
        if not self._cookies:
            return NaverResult(ok=False, endpoint="session",
                               error="no cookies injected")
        r = self.fetcher.fetch_json_api(
            "https://apis.naver.com/cafe-web/cafe-myapi/v1/my/profile",
            headers=self._api_headers(),
        )
        if r.ok and r.json_data:
            return NaverResult(ok=True, endpoint=r.url, status_code=r.status_code,
                               data=r.json_data)
        return NaverResult(ok=False, endpoint=r.url, status_code=r.status_code,
                           error="session not logged in or expired")

    def can_access(self, club_id: str) -> NaverResult:
        """Check whether YOUR session can read a (possibly private) cafe.

        If you are a joined member, your session returns the cafe's menus.
        A private cafe you have NOT joined returns an error — the correct,
        legal outcome (access requires real membership, not bypass).
        """
        r = self.menus(club_id)
        if r.ok and r.data:
            return NaverResult(ok=True, endpoint=r.endpoint, status_code=r.status_code,
                               data={"accessible": True, "menus": r.data})
        return NaverResult(ok=False, endpoint=r.endpoint, status_code=r.status_code,
                           error="not accessible with this session "
                                 "(not a member, or login required)")

    def _api_headers(self) -> dict[str, str]:
        return {
            **NAVER_HEADERS,
            "Accept": "application/json",
            "Referer": "https://cafe.naver.com/",
            "Origin": "https://cafe.naver.com",
        }

    def _get_api(self, url: str, params: dict[str, Any] | None = None) -> NaverResult:
        """Rate-limited, retried, cached GET against a Naver Cafe REST API."""
        cache_key = f"{url}?{json.dumps(params or {}, sort_keys=True)}"
        cached = self.cache.get(cache_key)
        if cached:
            return NaverResult(ok=True, endpoint=url,
                               status_code=cached.get("_code", 200), data=cached.get("data"))

        self.bucket.acquire()

        def do_request() -> NaverResult:
            r = self.fetcher.fetch_json_api(url, params=params, headers=self._api_headers())
            if r.ok and r.json_data is not None:
                self.cache.put(cache_key, {"_code": r.status_code, "data": r.json_data})
                return NaverResult(
                    ok=True, endpoint=url, status_code=r.status_code, data=r.json_data,
                )
            return NaverResult(ok=False, endpoint=url, status_code=r.status_code,
                               error=r.error or "empty")

        try:
            return retry(do_request, policy=self.retry_policy)
        except Exception as exc:
            return NaverResult(ok=False, endpoint=url, error=str(exc)[:200])

    # ── Public API ──

    def menus(self, club_id: str) -> NaverResult:
        """Get the cafe's menu (board) list with menuIds."""
        return self._get_api(f"{self.MAIN_API}/cafes/{club_id}/menus")

    def articles(self, club_id: str, menu_id: int | str = 0, page: int = 1,
                 page_size: int = 15, sort_by: str = "TIME") -> NaverResult:
        """Get article list for a cafe board.

        Args:
            club_id: Cafe club ID.
            menu_id: Board menu ID (0 = all articles across boards).
            page: Page number (1-based).
            page_size: Articles per page (max ~30).
            sort_by: TIME | READ | COMMENT | LIKE.
        """
        url = f"{self.BOARD_API}/cafes/{club_id}/menus/{menu_id}/articles"
        return self._get_api(url, params={
            "page": page, "pageSize": page_size, "sortBy": sort_by, "viewType": "L",
        })

    def notices(self, club_id: str) -> NaverResult:
        """Get pinned notices."""
        return self._get_api(f"{self.BOARD_API}/cafes/{club_id}/notices/menus/0")

    def popular(self, club_id: str) -> NaverResult:
        """Get popular articles."""
        return self._get_api(f"{self.BOARD_API}/cafes/{club_id}/uparticles/menus/0")

    def crawl_articles(self, club_id: str, menu_id: int | str = 0,
                       max_pages: int = 5, page_size: int = 15,
                       on_page: Any = None) -> list[dict[str, Any]]:
        """Crawl multiple pages of articles with polite pacing.

        Args:
            on_page: optional callback(page_no, articles) for progress.
        Returns:
            Flattened list of article items.
        """
        all_items: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            r = self.articles(club_id, menu_id=menu_id, page=page, page_size=page_size)
            if not r.ok or not isinstance(r.data, dict):
                break
            items = self.normalize_articles(r.data)
            if not items:
                break
            all_items.extend(items)
            if on_page:
                on_page(page, items)
            if len(items) < page_size:  # last page
                break
        return all_items

    def article_content(self, club_id: str, article_id: int | str) -> NaverResult:
        """Fetch full article content by rendering the page and reading the
        browser-decoded text from the article frame.

        The modern cafe frontend embeds the legacy article body in a
        `yortapaper` iframe served in EUC-KR/CP949. Rather than fight the raw
        bytes, we let the browser decode it and read `innerText` from each
        frame — always clean UTF-8. Navigation chrome is filtered out.
        """
        url = f"{self.FE_BASE}/{club_id}/articles/{article_id}"
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return NaverResult(ok=False, endpoint=url, error="playwright not installed")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx_kwargs: dict[str, Any] = {
                    "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"),
                    "locale": "ko-KR", "viewport": {"width": 1280, "height": 1000},
                }
                context = browser.new_context(**ctx_kwargs)
                if self._cookies:
                    domain = "cafe.naver.com"
                    context.add_cookies([
                        {"name": k, "value": v, "domain": domain, "path": "/"}
                        for k, v in self._cookies.items()
                    ])
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=self.cfg.timeout * 2000)
                page.wait_for_timeout(3500)

                # Collect browser-decoded text from every frame
                frame_texts: list[tuple[str, str]] = []
                for fr in page.frames:
                    try:
                        txt = fr.evaluate(
                            "() => document.body ? document.body.innerText : ''"
                        ).strip()
                        if txt:
                            frame_texts.append((fr.url, txt))
                    except Exception:
                        continue
                browser.close()

            content = self._pick_article_body(frame_texts)
            if content:
                return NaverResult(ok=True, endpoint=url, status_code=200,
                                   data={"content": content})
            return NaverResult(ok=False, endpoint=url, error="content not extracted")
        except Exception as e:
            return NaverResult(ok=False, endpoint=url, error=str(e)[:200])

    # Navigation chrome keywords to exclude when picking the article body frame
    _NAV_NOISE = ("카페홈", "내소식", "서비스 더보기", "카페 가입하기", "사용자 링크")

    def _pick_article_body(self, frame_texts: list[tuple[str, str]]) -> str:
        """Pick the article body from candidate frame texts.

        Prefers the yortapaper frame; otherwise the longest frame whose text
        is not just navigation chrome.
        """
        # 1) yortapaper frame (the legacy article body)
        for url, txt in frame_texts:
            if "yortapaper" in url and len(txt) > 50:
                return self._strip_nav(txt)
        # 2) longest non-nav frame
        best = ""
        for _url, txt in frame_texts:
            if any(n in txt[:120] for n in self._NAV_NOISE) and len(txt) < 1500:
                continue
            if len(txt) > len(best):
                best = txt
        return self._strip_nav(best) if len(best) > 50 else ""

    @classmethod
    def _strip_nav(cls, txt: str) -> str:
        """Remove obvious navigation lines from extracted text."""
        lines = [ln.strip() for ln in txt.splitlines()]
        keep = [ln for ln in lines if ln and ln not in cls._NAV_NOISE]
        return "\n".join(keep)

    # ── Parsing / normalization ──

    @staticmethod
    def normalize_articles(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize the REST API articleList into flat dicts."""
        raw = data.get("result", {}).get("articleList", [])
        out: list[dict[str, Any]] = []
        for entry in raw:
            it = entry.get("item", entry)
            writer = it.get("writerInfo", {})
            out.append({
                "article_id": it.get("articleId"),
                "cafe_id": it.get("cafeId"),
                "subject": it.get("subject", ""),
                "summary": it.get("summary", ""),
                "author": writer.get("nickName", ""),
                "author_level": writer.get("memberLevelName", ""),
                "menu_name": it.get("menuName", ""),
                "write_ts": it.get("writeDateTimestamp"),
                "read_count": it.get("readCount", 0),
                "comment_count": it.get("commentCount", 0),
                "like_count": it.get("likeCount", 0),
                "has_image": it.get("hasImage", False),
                "represent_image": it.get("representImage", ""),
                "url": f"https://cafe.naver.com/f-e/cafes/{it.get('cafeId')}/articles/{it.get('articleId')}",
            })
        return out

    @staticmethod
    def parse_article_content(html: str) -> str:
        """Extract article body text from rendered HTML (fallback)."""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one(
            ".se-main-container, #tbody, .article_content, [class*='article-body'], main"
        )
        return el.get_text(strip=True) if el else ""

    def close(self) -> None:
        if self._fetcher is not None:
            self._fetcher.close()
