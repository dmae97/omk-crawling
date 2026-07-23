"""AdaptiveFetcher — multi-strategy fetcher for hard-to-crawl targets.

Strategy ladder (escalates only on failure):
  1. DIRECT  — curl_cffi with browser TLS impersonation (fastest, no browser)
  2. SESSION — persistent session + cookies (for stateful public content)
  3. RENDER  — Playwright render + automatic XHR/fetch interception
               (captures the site's real API responses WITHOUT knowing them)

The RENDER strategy is the "very powerful" capability: instead of
reverse-engineering each site's API, it lets a real browser load the page
and records every JSON API call the page makes. Works for any JS-heavy site
(Naver Cafe, etc.) — public content only; never bypasses authentication.

Legitimate user-session support: callers may inject their OWN cookies
(their logged-in browser session) to access content they have rights to.
This is session reuse, not auth bypass.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from omk_crawl.resilience import (
    ImpersonateRotator,
    RetryPolicy,
    TokenBucket,
    retry,
)
from omk_crawl.stability import (
    BreakerRegistry,
    CircuitOpenError,
    SessionManager,
    get_logger,
)

log = get_logger("omk_crawl.adaptive")


@dataclass
class CapturedCall:
    """One XHR/fetch call captured during rendering."""
    url: str
    method: str
    status: int
    request_body: str | None = None
    response_body: str | None = None
    response_bytes: bytes | None = None  # raw bytes for legacy-encoding targets
    resource_type: str = ""

    def json(self) -> Any:
        if self.response_body:
            try:
                return json.loads(self.response_body)
            except (json.JSONDecodeError, ValueError):
                return None
        return None

    def decoded_text(self) -> str | None:
        """Decode response bytes with automatic encoding detection.

        Handles legacy Korean encodings (EUC-KR/CP949) common on older Naver
        endpoints. Strategy: score candidate encodings by how many real Hangul
        syllables (가-힣) they produce vs. replacement chars, and pick the best.
        Falls back to charset_normalizer, then to the pre-decoded response_body.
        """
        if not self.response_bytes:
            return self.response_body

        import re
        raw = self.response_bytes
        hangul = re.compile(r"[가-힣]")
        best_text: str | None = None
        best_score = -1
        # Korean-first candidate order; utf-8 included for modern endpoints.
        for enc in ("cp949", "euc-kr", "utf-8"):
            try:
                text = raw.decode(enc, errors="replace")
            except (UnicodeDecodeError, LookupError):
                continue
            score = len(hangul.findall(text)) - text.count("\ufffd") * 2
            if score > best_score:
                best_score = score
                best_text = text
        if best_text is not None and best_score > 0:
            return best_text

        # Non-Korean content: defer to charset_normalizer
        try:
            from charset_normalizer import from_bytes
            result = from_bytes(raw).best()
            if result is not None:
                return str(result)
        except Exception:
            pass
        return self.response_body


@dataclass
class FetchResult:
    ok: bool
    url: str
    strategy: str = ""          # direct | session | render
    status_code: int | None = None
    html: str | None = None
    json_data: Any = None
    captured: list[CapturedCall] = field(default_factory=list)  # RENDER-only
    error: str = ""

    def find_captured(self, substr: str) -> CapturedCall | None:
        """Find a captured API call whose URL contains substr."""
        for c in self.captured:
            if substr in c.url:
                return c
        return None

    def captured_json(self, substr: str) -> Any:
        c = self.find_captured(substr)
        return c.json() if c else None


@dataclass
class AdaptiveConfig:
    rate: float = 0.5
    burst: float = 3.0
    timeout: int = 12
    render_timeout: int = 25
    max_retries: int = 2
    impersonate_pool: list[str] | None = None
    user_cookies: dict[str, str] | None = None  # user's OWN session (legitimate)
    capture_filter: Callable[[str], bool] | None = None  # which API calls to keep


class AdaptiveFetcher:
    """Escalating multi-strategy fetcher."""

    def __init__(self, config: AdaptiveConfig | None = None) -> None:
        self.cfg = config or AdaptiveConfig()
        self.bucket = TokenBucket(rate=self.cfg.rate, capacity=self.cfg.burst)
        self.rotator = ImpersonateRotator(self.cfg.impersonate_pool)
        self.sessions = SessionManager()
        self.breakers = BreakerRegistry(failure_threshold=4, recovery_timeout=20.0)
        self.retry_policy = RetryPolicy(
            max_retries=self.cfg.max_retries,
            retryable_statuses=frozenset({429, 500, 502, 503, 504}),
        )
        if self.cfg.user_cookies:
            self.sessions.set_cookies(self.cfg.user_cookies)

    @staticmethod
    def _host(url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).netloc

    # ── Strategy 1 & 2: HTTP (direct / session) ──

    def _http(self, url: str, *, use_session: bool, params: dict | None = None,
              headers: dict | None = None, method: str = "GET",
              json_body: Any = None) -> FetchResult:
        from curl_cffi import requests as cffi

        host = self._host(url)
        breaker = self.breakers.get(host)
        imp = self.rotator.next()
        base_headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36"),
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
        if headers:
            base_headers.update(headers)

        self.bucket.acquire()

        def do() -> FetchResult:
            client = self.sessions.get() if use_session else cffi
            kwargs: dict[str, Any] = dict(
                params=params, headers=base_headers, timeout=self.cfg.timeout,
            )
            if not use_session:
                kwargs["impersonate"] = imp
            if method.upper() == "POST":
                kwargs["json"] = json_body
                resp = client.post(url, **kwargs)
            else:
                resp = client.get(url, **kwargs)

            if resp.status_code == 403:
                self.rotator.mark_failed(imp)

            ct = resp.headers.get("content-type", "")
            json_data = None
            if "json" in ct:
                try:
                    json_data = resp.json()
                except Exception:
                    pass

            ok = resp.status_code < 400
            return FetchResult(
                ok=ok, url=url, strategy="session" if use_session else "direct",
                status_code=resp.status_code, html=resp.text if "json" not in ct else None,
                json_data=json_data,
                error="" if ok else f"HTTP {resp.status_code}",
            )

        try:
            return breaker.call(lambda: retry(do, policy=self.retry_policy))
        except CircuitOpenError as e:
            return FetchResult(ok=False, url=url, strategy="direct", error=str(e))
        except Exception as e:
            return FetchResult(ok=False, url=url, strategy="direct", error=str(e)[:200])

    # ── Strategy 3: Playwright render + intercept ──

    def _render(self, url: str, *, wait_ms: int = 3000,
                capture_filter: Callable[[str], bool] | None = None) -> FetchResult:
        """Render page in a real browser and capture all JSON API calls.

        This is the powerful fallback: it discovers the site's real API
        responses automatically, no prior knowledge needed.
        """
        filt = capture_filter or self.cfg.capture_filter or (lambda u: True)
        captured: list[CapturedCall] = []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return FetchResult(ok=False, url=url, strategy="render",
                               error="playwright not installed")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx_kwargs: dict[str, Any] = {
                    "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"),
                    "locale": "ko-KR",
                    "viewport": {"width": 1280, "height": 900},
                }
                context = browser.new_context(**ctx_kwargs)
                # Inject user's own cookies if provided (legitimate session reuse)
                if self.cfg.user_cookies:
                    host = self._host(url)
                    domain = host.split(":")[0]
                    context.add_cookies([
                        {"name": k, "value": v, "domain": domain, "path": "/"}
                        for k, v in self.cfg.user_cookies.items()
                    ])
                page = context.new_page()

                def on_response(resp):
                    try:
                        if resp.request.resource_type not in ("xhr", "fetch"):
                            return
                        if not filt(resp.url):
                            return
                        body = None
                        raw = None
                        try:
                            raw = resp.body()
                            body = raw.decode("utf-8", errors="replace")
                        except Exception:
                            try:
                                body = resp.text()
                            except Exception:
                                pass
                        captured.append(CapturedCall(
                            url=resp.url, method=resp.request.method,
                            status=resp.status, request_body=resp.request.post_data,
                            response_body=body, response_bytes=raw,
                            resource_type=resp.request.resource_type,
                        ))
                    except Exception:
                        pass

                page.on("response", on_response)
                page.goto(url, wait_until="networkidle", timeout=self.cfg.render_timeout * 1000)
                page.wait_for_timeout(wait_ms)
                html = page.content()
                browser.close()

            return FetchResult(
                ok=True, url=url, strategy="render", status_code=200,
                html=html, captured=captured,
            )
        except Exception as e:
            return FetchResult(ok=False, url=url, strategy="render",
                               captured=captured, error=str(e)[:200])

    # ── Public API: adaptive fetch ──

    def fetch(self, url: str, *, params: dict | None = None, headers: dict | None = None,
              strategies: tuple[str, ...] = ("direct", "session", "render"),
              capture_filter: Callable[[str], bool] | None = None,
              render_wait_ms: int = 3000) -> FetchResult:
        """Fetch with automatic escalation through the strategy ladder.

        Args:
            strategies: ordered strategies to try. Default: direct → session → render.
            capture_filter: for render strategy, which API URLs to capture.
        """
        last: FetchResult | None = None
        for strat in strategies:
            if strat == "direct":
                r = self._http(url, use_session=False, params=params, headers=headers)
            elif strat == "session":
                r = self._http(url, use_session=True, params=params, headers=headers)
            elif strat == "render":
                r = self._render(url, wait_ms=render_wait_ms, capture_filter=capture_filter)
            else:
                continue
            last = r
            if r.ok and (r.json_data is not None or r.html or r.captured):
                log.info("fetch ok via %s: %s", strat, url[:80])
                return r
            log.info("strategy %s insufficient (%s), escalating", strat, r.error or "empty")
        return last or FetchResult(ok=False, url=url, error="no strategy succeeded")

    def fetch_json_api(self, url: str, *, params: dict | None = None,
                       headers: dict | None = None) -> FetchResult:
        """Fetch a known JSON API (direct HTTP only, fast path)."""
        return self._http(url, use_session=False, params=params,
                          headers={**(headers or {}), "Accept": "application/json"})

    def close(self) -> None:
        self.sessions.close()
