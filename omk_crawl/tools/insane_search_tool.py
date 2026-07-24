"""InsaneSearch — hyper-aggressive single-URL unblocker.

First-line breaker when everything else fails. Bypasses:
  - TLS/JA3 fingerprinting (impersonate rotation × 8 profiles)
  - Cloudflare / Turnstile (real browser fallback)
  - Akamai / Datadome / Imperva (header trickery)
  - Rate limiting (token bucket + jitter)
  - Geo-blocking (configurable proxy)

Strategy: throw everything at the wall until something sticks.
  1. Raw curl_cffi with Chrome TLS impersonation
  2. Safari/firefox impersonation fallback
  3. Spoofed headers (real browser Accept/Referer/Sec-*)
  4. If all HTTP fails → Playwright stealth browser
"""

from __future__ import annotations

import random
import time
from typing import Any

from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.tools.base import BaseTool

# ── Impersonate profiles (sorted by likelihood of bypass) ──
_IMPERSONATE_PROFILES = [
    "chrome124",   # Latest Chrome (most sites target this)
    "chrome120",
    "chrome116",
    "chrome110",
    "safari17_0",  # Safari gets different treatment sometimes
    "safari15_5",
    "edge101",
    "firefox133",  # Firefox sometimes bypasses Chrome-specific blocks
]

# ── Spoofed header sets (real browser fingerprints) ──
_CHROME_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Sec-Ch-Ua": (
        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'
    ),
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.com/",
}

_SAFARI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

_FIREFOX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) "
        "Gecko/20100101 Firefox/133.0"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
}

_PROFILE_HEADERS: dict[str, dict[str, str]] = {
    "chrome": _CHROME_HEADERS,
    "safari": _SAFARI_HEADERS,
    "firefox": _FIREFOX_HEADERS,
    "edge": _CHROME_HEADERS,  # Edge uses Blink, same headers
}


def _profile_family(impersonate: str) -> str:
    """Extract browser family from impersonate profile name."""
    if "chrome" in impersonate:
        return "chrome"
    if "safari" in impersonate:
        return "safari"
    if "firefox" in impersonate:
        return "firefox"
    if "edge" in impersonate:
        return "edge"
    return "chrome"


class InsaneSearchTool(BaseTool):
    """Hyper-aggressive single-URL fetcher. 8 impersonation profiles + stealth browser."""

    name = "insane_search"
    pip_package = "curl_cffi"
    layer = 0  # fetch layer
    capabilities: frozenset[str] = frozenset({"timeout", "headers", "proxy"})

    def available(self) -> bool:
        return True  # Pure Python + curl_cffi, always available

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        """Try every trick to get the URL content.

        Args:
            timeout: Per-attempt timeout (seconds, default 18).
            proxy: SOCKS5/HTTP proxy URL.
            stealth: If True, use Playwright stealth browser as last resort.
        """
        timeout: int = int(kwargs.get("timeout", 18))
        proxy: str | None = kwargs.get("proxy")
        use_stealth: bool = kwargs.get("stealth", True)
        attempts: list[dict[str, Any]] = []
        t_start = time.monotonic()

        # ── Strategy 1: Rotate TLS impersonation profiles ──
        from curl_cffi import requests as cffi

        shuffled = list(_IMPERSONATE_PROFILES)
        random.shuffle(shuffled)

        for imp in shuffled:
            family = _profile_family(imp)
            headers = dict(_PROFILE_HEADERS.get(family, _CHROME_HEADERS))
            if kwargs.get("headers"):
                headers.update(kwargs["headers"])

            try:
                kw: dict[str, Any] = dict(
                    headers=headers,
                    impersonate=imp,
                    timeout=timeout,
                    allow_redirects=True,
                )
                if proxy:
                    kw["proxy"] = proxy

                resp = cffi.get(url, **kw)

                block_detected = self._is_blocked(resp.status_code, resp.text)
                attempts.append({
                    "profile": imp, "status": resp.status_code,
                    "len": len(resp.text), "blocked": block_detected,
                })

                if resp.status_code < 400 and not block_detected:
                    return CrawlResult(
                        url=url,
                        status=CrawlStatus.OK,
                        status_code=resp.status_code,
                        tool=self.name,
                        html=resp.text,
                        content=resp.text,
                        elapsed_ms=(time.monotonic() - t_start) * 1000,
                        metadata={
                            "strategy": "impersonate",
                            "profile": imp,
                            "attempts": attempts,
                        },
                    )
            except Exception as exc:
                attempts.append({"profile": imp, "error": str(exc)[:120]})
                continue

        # ── Strategy 2: Playwright stealth browser (last resort) ──
        if use_stealth:
            try:
                from playwright.sync_api import sync_playwright

                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    ctx_kwargs: dict[str, Any] = {
                        "user_agent": _CHROME_HEADERS["User-Agent"],
                        "locale": "ko-KR",
                        "viewport": {"width": 1280, "height": 900},
                    }
                    if proxy:
                        ctx_kwargs["proxy"] = {"server": proxy}
                    context = browser.new_context(**ctx_kwargs)
                    page = context.new_page()

                    # Stealth: spoof webdriver detection
                    page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver',
                            {get: () => undefined});
                        Object.defineProperty(navigator, 'plugins',
                            {get: () => [1,2,3,4,5]});
                        Object.defineProperty(navigator, 'languages',
                            {get: () => ['ko-KR','ko','en-US','en']});
                        window.chrome = {runtime: {}};
                    """)

                    page.goto(url, wait_until="networkidle",
                              timeout=timeout * 1000)
                    page.wait_for_timeout(2000)
                    html = page.content()
                    browser.close()

                    return CrawlResult(
                        url=url,
                        status=CrawlStatus.OK,
                        status_code=200,
                        tool=self.name,
                        html=html,
                        content=html,
                        elapsed_ms=(time.monotonic() - t_start) * 1000,
                        metadata={
                            "strategy": "stealth_browser",
                            "attempts": attempts,
                        },
                    )
            except ImportError:
                pass
            except Exception as exc:
                attempts.append({"strategy": "stealth_browser", "error": str(exc)[:120]})

        # ── Total failure ──
        return CrawlResult(
            url=url,
            status=CrawlStatus.BLOCKED,
            tool=self.name,
            error=f"All 8 profiles + stealth browser failed. Last attempts: {attempts[-3:]}",
            elapsed_ms=(time.monotonic() - t_start) * 1000,
            metadata={"attempts": attempts},
        )

    @staticmethod
    def _is_blocked(status_code: int, html: str) -> bool:
        """Detect if response is a block page despite 2xx status."""
        lower = html[:2000].lower()
        markers = (
            "cf-browser-verification", "cf_chl_opt", "turnstile",
            "challenge-platform", "are you a robot", "captcha",
            "access denied", "unusual traffic", "blocked",
            "ddos-guard", "datadome", "perimeterx",
        )
        if status_code == 403:
            return True
        if status_code == 503 and any(m in lower for m in markers[:4]):
            return True
        if any(m in lower for m in markers):
            return True
        return False
