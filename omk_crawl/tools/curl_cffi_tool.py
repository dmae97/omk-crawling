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

    def __init__(self, impersonate: str = "chrome131") -> None:
        self.impersonate = impersonate

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        if not self.available():
            return self._missing(url)
        _, stop = _timer()
        try:
            from curl_cffi import requests as cffi_requests

            resp = cffi_requests.get(
                url,
                impersonate=kwargs.get("impersonate", self.impersonate),
                timeout=kwargs.get("timeout", 30),
                proxies=kwargs.get("proxies"),
                headers=kwargs.get("headers"),
            )
            det = detect_block(resp.text, resp.status_code)
            status = detection_to_status(det)
            return CrawlResult(
                url=url,
                status=status,
                status_code=resp.status_code,
                html=resp.text,
                tool=self.name,
                elapsed_ms=stop(),
                headers=dict(resp.headers),
                metadata={"impersonate": self.impersonate, "detection": det.detail},
            )
        except Exception as exc:
            r = self._error(url, exc)
            r.elapsed_ms = stop()
            return r
