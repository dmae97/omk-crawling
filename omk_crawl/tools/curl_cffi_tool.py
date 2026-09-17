"""curl_cffi adapter — TLS/JA3 fingerprint impersonation (lightest)."""

from __future__ import annotations

from typing import Any

from omk_crawl.detect import detect_block, detection_to_status
from omk_crawl.result import CrawlResult, _timer
from omk_crawl.tools.base import BaseTool


class CurlCffiTool(BaseTool):
    name = "curl_cffi"
    pip_package = "curl_cffi"
    layer = 0
    needs_browser = False
    capabilities = frozenset({"timeout", "proxy", "headers", "cookies"})

    def __init__(self, impersonate: str = "chrome131") -> None:
        self.impersonate = impersonate

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        if not self.available():
            return self._missing(url)
        # `evade=True` opts into the full six-layer identity: the TLS target and
        # the header set are taken from the same plan, and the result carries the
        # cross-layer audit. Off by default so existing callers see exactly the
        # behaviour they had before (the router turns it on for stealth routes).
        plan = None
        if kwargs.get("evade"):
            from omk_crawl.evasion import plan_for

            plan = plan_for(url)
        impersonate = kwargs.get("impersonate") or (
            plan.profile.impersonate if plan else self.impersonate
        )
        headers = kwargs.get("headers")
        if headers is None and plan is not None:
            headers = plan.headers()

        _, stop = _timer()
        try:
            from curl_cffi import requests as cffi_requests
            from curl_cffi.requests import ProxySpec

            # Normalize the common `proxy` (str) into curl_cffi's `proxies` spec.
            proxy = kwargs.get("proxy")
            proxies: ProxySpec | None = kwargs.get("proxies")
            if proxies is None and proxy:
                proxies = ProxySpec(http=str(proxy), https=str(proxy))

            resp = cffi_requests.get(
                url,
                impersonate=impersonate,
                timeout=kwargs.get("timeout", 30),
                proxies=proxies,
                headers=headers,
                cookies=kwargs.get("cookies"),
            )
            det = detect_block(resp.text, resp.status_code)
            status = detection_to_status(det)
            meta = {"impersonate": impersonate, "detection": det.detail}
            if plan is not None:
                meta["evasion"] = plan.as_metadata()
                # Audit the headers actually sent against the plan's TLS target: a
                # 403 that comes back while this list is non-empty is a cross-layer
                # contradiction, not a mystery. curl_cffi versions differ in how
                # much of the request they expose, so fall back to the intended
                # header set rather than failing the fetch over diagnostics.
                sent = getattr(getattr(resp, "request", None), "headers", None)
                meta["tls_coherence"] = plan.profile.coherence_issues(
                    dict(sent) if sent else plan.headers()
                )
            meta.update(self.contract_metadata(kwargs))
            return CrawlResult(
                url=url,
                status=status,
                status_code=resp.status_code,
                html=resp.text,
                tool=self.name,
                elapsed_ms=stop(),
                headers={str(k): str(v) for k, v in resp.headers.items() if v is not None},
                metadata=meta,
            )
        except Exception as exc:
            r = self._error(url, exc)
            r.elapsed_ms = stop()
            return r
