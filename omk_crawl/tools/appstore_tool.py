"""iOS App Store metadata tool — appstore://search?q= or appstore://id/TRACK_ID."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from omk_crawl.mobile.appstore import AppStoreClient
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.tools.base import BaseTool


def _parse_appstore_url(url: str) -> dict[str, Any]:
    raw = url.strip()
    if raw in {"appstore://", "appstore:///", "appstore", "ios://", "ios:///"}:
        return {"action": "help"}
    if raw.startswith("ios://"):
        raw = "appstore://" + raw[len("ios://") :]
    if not raw.startswith("appstore://"):
        return {"action": "help"}
    u = urlparse(raw)
    host = unquote(u.netloc or "")
    path = (u.path or "").strip("/")
    qs = {k: v[0] for k, v in parse_qs(u.query or "").items() if v}
    out: dict[str, Any] = {**qs}
    if host in {"search", "lookup", "bundle"} or path.startswith("search"):
        out["action"] = "search" if "search" in (host + path) else host or "search"
    elif host == "id" or path.startswith("id/"):
        out["action"] = "lookup"
        rest = path.split("id/", 1)[-1] if "id/" in path else path
        out["id"] = qs.get("id") or rest or host
    elif host.isdigit() or (path and path.isdigit()):
        out["action"] = "lookup"
        out["id"] = host if host.isdigit() else path
    elif host == "bundle" or qs.get("bundleId") or qs.get("bundle"):
        out["action"] = "bundle"
        out["bundle"] = qs.get("bundleId") or qs.get("bundle") or path
    elif qs.get("q") or qs.get("term") or qs.get("query"):
        out["action"] = "search"
        out["q"] = qs.get("q") or qs.get("term") or qs.get("query")
    elif host:
        # appstore://요기요
        out["action"] = "search"
        out["q"] = host + ((" " + path) if path else "")
    else:
        out["action"] = "help"
    return out


class AppStoreTool(BaseTool):
    """Public iTunes lookup/search — iOS surface without IPA."""

    name = "appstore"
    pip_package = "curl_cffi"
    layer = 1
    needs_browser = False
    capabilities: frozenset[str] = frozenset({"timeout"})

    def available(self) -> bool:
        return True  # urllib fallback inside client

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        t0 = time.perf_counter()
        meta = self.contract_metadata(kwargs)
        parts = _parse_appstore_url(url)
        country = str(parts.get("country") or kwargs.get("country") or "kr")
        client = AppStoreClient(country=country, timeout=float(kwargs.get("timeout") or 20))

        action = str(parts.get("action") or "help")
        if action == "help":
            md = (
                "# App Store tool\n\n"
                "```\n"
                "omk-crawl 'appstore://search?q=요기요'\n"
                "omk-crawl appstore://378084485\n"
                "omk-crawl 'appstore://bundle?bundleId=com.jawebs.baedal'\n"
                "omk-crawl ios://Freeform\n"
                "```\n"
            )
            return CrawlResult(
                url=url,
                status=CrawlStatus.OK,
                status_code=200,
                markdown=md,
                fit_markdown=md,
                tool=self.name,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata=meta,
            )

        try:
            if action == "lookup":
                app = client.lookup(parts.get("id") or "")
                apps = [app] if app else []
            elif action == "bundle":
                app = client.lookup_bundle(str(parts.get("bundle") or ""))
                apps = [app] if app else []
            else:
                q = str(parts.get("q") or parts.get("term") or "")
                limit = int(parts.get("limit") or 10)
                apps = client.search(q, limit=limit)
        except Exception as exc:
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                tool=self.name,
                error=str(exc)[:300],
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata=meta,
            )

        if not apps:
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                status_code=404,
                tool=self.name,
                error="no App Store results",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={**meta, "country": country},
            )

        md_parts = [a.to_markdown() for a in apps]
        md = "\n---\n".join(md_parts)
        extracted = [a.to_dict() for a in apps]
        return CrawlResult(
            url=url,
            status=CrawlStatus.OK,
            status_code=200,
            markdown=md,
            fit_markdown=md,
            extracted=extracted,
            tool=self.name,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            metadata={
                **meta,
                "country": country,
                "count": len(apps),
                "top": apps[0].track_name,
                "bundle_id": apps[0].bundle_id,
            },
        )
