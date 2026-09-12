"""Patchright adapter — patched, undetected Playwright.

Why it is on the ladder: Patchright keeps the Playwright API and mental
model while shipping patches against the automation artifacts that
Akamai-class vendors fingerprint (isolated-world leaks, CDC variables,
Runtime.enable CDP tells). It is the drop-in upgrade when crawl4ai's
vanilla Playwright gets detected but a full anti-detect browser
(camoufox/nodriver) would be overkill.
"""

from __future__ import annotations

from typing import Any

from omk_crawl.detect import detect_block, detection_to_status
from omk_crawl.fingerprint import PROFILES, profile_for
from omk_crawl.result import CrawlResult, _timer
from omk_crawl.tools.base import BaseTool

_PROFILES_BY_NAME = {p.name: p for p in PROFILES}


class PatchrightTool(BaseTool):
    """Patched-Playwright fetcher (patchright) with coherent fingerprints."""

    name = "patchright"
    pip_package = "patchright"
    layer = 2  # browser layer
    needs_browser = True
    capabilities: frozenset[str] = frozenset(
        {"timeout", "proxy", "js_render", "stealth", "fingerprint"}
    )

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        """Render with patchright. Extra kwargs: headless, fingerprint (profile name), wait_ms."""
        if not self.available():
            return self._missing(url)
        _, stop = _timer()
        try:
            timeout = int(kwargs.get("timeout", 30))
        except (TypeError, ValueError):
            timeout = 30
        headless = bool(kwargs.get("headless", True))
        proxy: str | None = kwargs.get("proxy")
        try:
            wait_ms = max(0, int(kwargs.get("wait_ms", 1200)))
        except (TypeError, ValueError):
            wait_ms = 1200

        # Cross-layer coherence: the browser context tells the same story as
        # the site's assigned TLS/header identity (fingerprint.py).
        requested = kwargs.get("fingerprint")
        profile = _PROFILES_BY_NAME.get(requested) if requested else None
        if profile is None:
            profile = profile_for(url)

        try:
            from patchright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=headless)
                try:
                    ctx_kwargs: dict[str, Any] = profile.browser_context_kwargs()
                    if proxy:
                        ctx_kwargs["proxy"] = {"server": proxy}
                    context = browser.new_context(**ctx_kwargs)
                    page = context.new_page()
                    response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                    if wait_ms:
                        page.wait_for_timeout(wait_ms)
                    html = page.content()
                    status_code = response.status if response is not None else None
                finally:
                    browser.close()

            det = detect_block(html, status_code)
            meta: dict[str, Any] = {
                "detection": det.detail,
                "stealth": True,
                "engine": "patchright",
                "fingerprint": profile.name,
            }
            meta.update(self.contract_metadata(kwargs))
            return CrawlResult(
                url=url,
                status=detection_to_status(det),
                status_code=status_code,
                html=html,
                tool=self.name,
                elapsed_ms=stop(),
                metadata=meta,
            )
        except Exception as exc:
            r = self._error(url, exc)
            r.elapsed_ms = stop()
            return r
