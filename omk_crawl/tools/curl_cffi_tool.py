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
                impersonate=kwargs.get("impersonate", self.impersonate),
                timeout=kwargs.get("timeout", 30),
                proxies=proxies,
                headers=kwargs.get("headers"),
                cookies=kwargs.get("cookies"),
            )
            det = detect_block(resp.text, resp.status_code)
            status = detection_to_status(det)
            meta = {"impersonate": self.impersonate, "detection": det.detail}
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
