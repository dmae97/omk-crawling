"""nodriver adapter — CDP-native Chrome, successor to undetected-chromedriver.

Why it is on the ladder: nodriver speaks the Chrome DevTools Protocol
directly — no WebDriver, no ``navigator.webdriver`` flag, no CDC variables
for DataDome/Kasada-class JS probes to find. It is the strongest option
against the hardest behavioral vendors (DataDome, Kasada, PerimeterX/HUMAN)
whose checks specifically hunt Playwright/Puppeteer automation artifacts.
"""

from __future__ import annotations

import asyncio
from typing import Any

from omk_crawl.detect import detect_block, detection_to_status
from omk_crawl.result import CrawlResult, CrawlStatus, _timer
from omk_crawl.tools.base import BaseTool


class NodriverTool(BaseTool):
    """CDP-native Chrome fetcher (nodriver)."""

    name = "nodriver"
    pip_package = "nodriver"
    layer = 2  # browser layer
    needs_browser = True
    capabilities: frozenset[str] = frozenset({"timeout", "proxy", "js_render", "stealth"})

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        """Render with nodriver (sync wrapper). Extra kwargs: headless, proxy, wait_s."""
        # nodriver owns its event loop, so this sync entry point cannot run
        # inside one. RuntimeError from get_running_loop() means "no loop" —
        # exactly the case we want.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            inside_running_loop = False
        else:
            inside_running_loop = True

        if inside_running_loop:
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                tool=self.name,
                error="nodriver is async-native inside a running loop; use fetch_async",
            )
        return asyncio.run(self.fetch_async(url, **kwargs))

    async def fetch_async(self, url: str, **kwargs: Any) -> CrawlResult:
        """Render with nodriver. Extra kwargs: headless, proxy, wait_s."""
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
            wait_s = max(0.0, float(kwargs.get("wait_s", 2.0)))
        except (TypeError, ValueError):
            wait_s = 2.0

        try:
            import nodriver as nd

            browser_args = [f"--proxy-server={proxy}"] if proxy else None
            browser = await nd.start(headless=headless, browser_args=browser_args)
            try:
                page = await asyncio.wait_for(browser.get(url), timeout=timeout)
                if wait_s:
                    await page.wait(wait_s)
                html = await page.get_content()
            finally:
                browser.stop()

            det = detect_block(html, None)
            meta: dict[str, Any] = {
                "detection": det.detail,
                "stealth": True,
                "engine": "nodriver-cdp",
            }
            meta.update(self.contract_metadata(kwargs))
            return CrawlResult(
                url=url,
                status=detection_to_status(det),
                html=html,
                tool=self.name,
                elapsed_ms=stop(),
                metadata=meta,
            )
        except Exception as exc:
            r = self._error(url, exc)
            r.elapsed_ms = stop()
            return r
