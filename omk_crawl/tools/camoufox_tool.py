"""Camoufox adapter — anti-detect Firefox with C++-level fingerprint injection.

Why it is on the ladder: Camoufox does not patch JS from the outside (the
mistake arXiv:2606.30119 shows makes agents more visible); it injects
statistically modeled fingerprints at the browser-engine level, hides
Playwright from JS inspection, and can humanize interactions and pin its
exit geo to a proxy (geoip). Strongest general-purpose stealth browser in
the 2026 landscape for Cloudflare-class defenses.
"""

from __future__ import annotations

from typing import Any

from omk_crawl.detect import detect_block, detection_to_status
from omk_crawl.result import CrawlResult, _timer
from omk_crawl.tools.base import BaseTool


class CamoufoxTool(BaseTool):
    """Anti-detect Firefox fetcher (camoufox)."""

    name = "camoufox"
    pip_package = "camoufox[geoip]"
    layer = 2  # browser layer
    needs_browser = True
    capabilities: frozenset[str] = frozenset(
        {"timeout", "proxy", "js_render", "stealth", "humanize", "geoip"}
    )

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        """Render with Camoufox. Extra kwargs: headless, humanize, geoip, wait_ms."""
        if not self.available():
            return self._missing(url)
        _, stop = _timer()
        try:
            timeout = int(kwargs.get("timeout", 30))
        except (TypeError, ValueError):
            timeout = 30
        headless = bool(kwargs.get("headless", True))
        humanize = bool(kwargs.get("humanize", True))
        proxy: str | None = kwargs.get("proxy")
        wait_ms_raw = kwargs.get("wait_ms", 1500)
        try:
            wait_ms = max(0, int(wait_ms_raw))
        except (TypeError, ValueError):
            wait_ms = 1500

        try:
            from camoufox.sync_api import Camoufox  # pyright: ignore[reportMissingImports]

            config: dict[str, Any] = {"headless": headless, "humanize": humanize}
            if proxy:
                config["proxy"] = {"server": proxy}
                config["geoip"] = bool(kwargs.get("geoip", True))
            with Camoufox(**config) as browser:
                page = browser.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                if wait_ms:
                    page.wait_for_timeout(wait_ms)
                html = page.content()
                status_code = response.status if response is not None else None

            det = detect_block(html, status_code)
            status = detection_to_status(det)
            meta: dict[str, Any] = {
                "detection": det.detail,
                "stealth": True,
                "engine": "camoufox",
                "humanize": humanize,
            }
            meta.update(self.contract_metadata(kwargs))
            return CrawlResult(
                url=url,
                status=status,
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

    async def fetch_async(self, url: str, **kwargs: Any) -> CrawlResult:
        """Async variant via camoufox's async Playwright-compatible API."""
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
            from camoufox.async_api import AsyncCamoufox  # pyright: ignore[reportMissingImports]

            config: dict[str, Any] = {
                "headless": headless,
                "humanize": bool(kwargs.get("humanize", True)),
            }
            if proxy:
                config["proxy"] = {"server": proxy}
                config["geoip"] = bool(kwargs.get("geoip", True))
            async with AsyncCamoufox(**config) as browser:
                page = await browser.new_page()
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=timeout * 1000
                )
                await page.wait_for_timeout(1500)
                html = await page.content()
                status_code = response.status if response is not None else None

            det = detect_block(html, status_code)
            meta: dict[str, Any] = {
                "detection": det.detail,
                "stealth": True,
                "engine": "camoufox",
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
