"""Bounded HTTP profile fallback followed by optional browser rendering.

Authentication and server backpressure are terminal responses, not reasons to
rotate clients. The timeout is shared by all work inside this adapter.
"""

from __future__ import annotations

import math
import time
from typing import Any

from omk_crawl.fingerprint import match_impersonate
from omk_crawl.pipeline import normalize_result
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.retry_after import retry_after_seconds
from omk_crawl.routing import is_auth_block
from omk_crawl.stability import TimeoutBudget
from omk_crawl.tools.base import BaseTool

_IMPERSONATE_PROFILES = (
    "chrome124",
    "chrome120",
    "chrome116",
    "chrome110",
    "safari17_0",
    "safari15_5",
    "edge101",
    "firefox133",
)


class InsaneSearchTool(BaseTool):
    name = "insane_search"
    pip_package = "curl_cffi"
    layer = 0
    default_timeout = 18.0
    capabilities = frozenset({"timeout", "headers", "proxy"})

    def available(self) -> bool:
        from omk_crawl.detect import tool_available

        return tool_available("curl_cffi") or tool_available("playwright")

    def _finish(
        self, result: CrawlResult, attempts: list[dict[str, Any]], started: float, reason: str
    ):
        result.elapsed_ms = (time.monotonic() - started) * 1000
        result.metadata.update(attempts=attempts, profile_attempts=attempts, adapter_stop=reason)
        return result

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        """Try HTTP profiles and an optional renderer within one timeout budget."""
        try:
            timeout = float(kwargs.get("timeout", self.default_timeout))
            if not math.isfinite(timeout) or timeout <= 0:
                timeout = self.default_timeout
        except (TypeError, ValueError, OverflowError):
            timeout = self.default_timeout
        budget = TimeoutBudget(timeout)
        started = time.monotonic()
        attempts: list[dict[str, Any]] = []
        best = self._missing(url)
        try:
            from curl_cffi import requests as cffi
        except ImportError:
            cffi = None
            attempts.append({"strategy": "impersonate", "error": "dependency_missing"})

        if cffi is not None:
            for profile in _IMPERSONATE_PROFILES:
                if budget.expired:
                    break
                headers = match_impersonate(profile).headers()
                headers.update(kwargs.get("headers") or {})
                try:
                    response = cffi.get(
                        url,
                        **{
                            "headers": headers,
                            "impersonate": profile,
                            "timeout": budget.remaining,
                            "allow_redirects": True,
                            "proxy": kwargs.get("proxy"),
                        },
                    )
                    result = CrawlResult(
                        url=url,
                        tool=self.name,
                        status=CrawlStatus.OK,
                        status_code=response.status_code,
                        html=response.text,
                        headers={str(k): str(v) for k, v in response.headers.items()},
                        metadata={"strategy": "impersonate", "profile": profile},
                    )
                    detection = normalize_result(result)
                    result.metadata.update(
                        block_type=detection.block.name, detection=detection.detail
                    )
                    attempts.append(
                        {
                            "profile": profile,
                            "status": result.status_code,
                            "len": len(response.text),
                            "blocked": result.blocked,
                        }
                    )
                    best = result
                    if budget.expired:
                        break
                    if is_auth_block(detection.block):
                        return self._finish(result, attempts, started, "auth_required")
                    if result.ok:
                        return self._finish(result, attempts, started, "success")
                    if result.status_code == 429 or retry_after_seconds(result.headers) is not None:
                        return self._finish(result, attempts, started, "server_backpressure")
                    if result.status is CrawlStatus.ERROR:
                        return self._finish(result, attempts, started, "http_error")
                    if result.status is CrawlStatus.JS_REQUIRED:
                        break
                except Exception as error:
                    attempts.append({"profile": profile, "error": type(error).__name__})
                    best = CrawlResult(
                        url=url,
                        tool=self.name,
                        status=CrawlStatus.ERROR,
                        error=f"HTTP adapter raised {type(error).__name__}",
                        metadata={"failure_kind": "adapter_exception"},
                    )

        if budget.expired:
            best.status = CrawlStatus.ERROR
            best.error = "Adapter deadline exceeded"
            best.metadata["failure_kind"] = "adapter_exception"
            return self._finish(best, attempts, started, "deadline_exceeded")
        if kwargs.get("stealth", True):
            if kwargs.get("headers"):
                # Avoid silently dropping custom headers or broadcasting credentials
                # to third-party browser subresources. Let an explicit renderer handle them.
                return self._finish(best, attempts, started, "headers_require_renderer")
            rendered = self._browser_fetch(url, budget, kwargs.get("proxy"))
            if (
                rendered.status is not CrawlStatus.TOOL_MISSING
                or best.status is CrawlStatus.TOOL_MISSING
            ):
                best = rendered
            attempts.append({"strategy": "stealth_browser", "status": rendered.status_code})
        if budget.expired:
            best.status = CrawlStatus.ERROR
            best.error = "Adapter deadline exceeded"
            best.metadata["failure_kind"] = "adapter_exception"
            return self._finish(best, attempts, started, "deadline_exceeded")
        return self._finish(best, attempts, started, "success" if best.ok else "exhausted")

    def _browser_fetch(self, url: str, budget: TimeoutBudget, proxy: str | None) -> CrawlResult:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return self._missing(url)
        try:
            if budget.expired:
                raise TimeoutError("adapter deadline")
            with sync_playwright() as playwright:
                remaining = budget.remaining
                if remaining <= 0:
                    raise TimeoutError("adapter deadline")
                browser = playwright.chromium.launch(headless=True, timeout=remaining * 1000)
                try:
                    context_kwargs = match_impersonate("chrome124").browser_context_kwargs()
                    if proxy:
                        context_kwargs["proxy"] = {"server": proxy}
                    context = browser.new_context(**context_kwargs)
                    page = context.new_page()
                    page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                        Object.defineProperty(navigator, 'languages', {
                            get: () => ['ko-KR','ko','en-US','en']
                        });
                        window.chrome = {runtime: {}};
                    """)
                    remaining = budget.remaining
                    if remaining <= 0:
                        raise TimeoutError("adapter deadline")
                    response = page.goto(url, wait_until="networkidle", timeout=remaining * 1000)
                    result = CrawlResult(
                        url=url,
                        tool=self.name,
                        status=CrawlStatus.OK,
                        status_code=response.status if response is not None else None,
                        html=page.content(),
                        headers=response.all_headers() if response is not None else {},
                        metadata={"strategy": "stealth_browser"},
                    )
                    detection = normalize_result(result)
                    result.metadata.update(
                        block_type=detection.block.name, detection=detection.detail
                    )
                    return result
                finally:
                    browser.close()
        except Exception as error:
            return CrawlResult(
                url=url,
                tool=self.name,
                status=CrawlStatus.ERROR,
                error=f"Browser adapter raised {type(error).__name__}",
                metadata={"failure_kind": "adapter_exception"},
            )

    @staticmethod
    def _is_blocked(status_code: int, html: str) -> bool:
        """Compatibility predicate using the engine's shared content validator."""
        result = CrawlResult(url="", status=CrawlStatus.OK, status_code=status_code, html=html)
        normalize_result(result)
        return not result.ok
