"""Baemin food-shop-list adapter — baemin://lat,lng or baemin://geo?lat=&lng=."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from omk_crawl.baemin import (
    BaeminClient,
    BaeminConfig,
    rank_shops,
    shops_to_markdown,
)
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.tools.base import BaseTool


def _parse_baemin_url(url: str) -> dict[str, Any]:
    """Parse baemin://36.83,127.13 or baemin://shops?lat=&lng=&limit=."""
    raw = url.strip()
    if raw in {"baemin://", "baemin:///", "baemin"}:
        return {"action": "status"}
    if not raw.startswith("baemin://"):
        return {"action": "status"}
    u = urlparse(raw)
    host = unquote(u.netloc or "")
    path = (u.path or "").strip("/")
    qs = {k: v[0] for k, v in parse_qs(u.query or "").items() if v}
    out: dict[str, Any] = {"action": path or "shops", **qs}
    # baemin://lat,lng
    if host and "," in host and "lat" not in out:
        a, b = host.split(",", 1)
        try:
            out["lat"] = float(a)
            out["lng"] = float(b)
            out["action"] = path or "shops"
        except ValueError:
            out["action"] = host or "status"
    elif host and host not in {"shops", "status"}:
        out["action"] = host
    return out


class BaeminTool(BaseTool):
    """List Baemin shops near coordinates (food-shop-list live path)."""

    name = "baemin"
    pip_package = "curl_cffi"
    layer = 1
    needs_browser = False
    capabilities: frozenset[str] = frozenset({"timeout"})

    def available(self) -> bool:
        try:
            import curl_cffi  # noqa: F401
        except ImportError:
            return False
        return True

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        t0 = time.perf_counter()
        meta = self.contract_metadata(kwargs)
        if not self.available():
            return self._missing(url)

        parts = _parse_baemin_url(url)
        action = str(parts.get("action") or "shops")
        cfg = BaeminConfig(
            timeout=int(kwargs.get("timeout") or 15),
            rate=float(kwargs.get("rate") or 1.0),
        )
        if "lat" in parts:
            try:
                cfg.lat = float(parts["lat"])
            except (TypeError, ValueError):
                pass
        if "lng" in parts:
            try:
                cfg.lng = float(parts["lng"])
            except (TypeError, ValueError):
                pass
        client = BaeminClient(cfg)

        if action == "status":
            st = client.status()
            md = "# Baemin client status\n\n```json\n" + __import__("json").dumps(
                st, ensure_ascii=False, indent=2
            ) + "\n```\n"
            return CrawlResult(
                url=url,
                status=CrawlStatus.OK,
                status_code=200,
                markdown=md,
                fit_markdown=md,
                extracted=[st],
                tool=self.name,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={**meta, **st},
            )

        # default: shops
        try:
            limit = int(parts.get("limit") or parts.get("max") or 40)
        except (TypeError, ValueError):
            limit = 40
        shops = client.collect_shops(lat=cfg.lat, lng=cfg.lng, max_shops=limit)
        if not shops:
            # probe once for error detail
            probe = client.list_shops(lat=cfg.lat, lng=cfg.lng, limit=5)
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                status_code=probe.status_code,
                tool=self.name,
                error=probe.error or "no shops returned",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={**meta, "lat": cfg.lat, "lng": cfg.lng},
            )

        ranked = rank_shops(shops, limit=min(20, len(shops)))
        title = f"Baemin shops near {cfg.lat:.4f},{cfg.lng:.4f}"
        md = shops_to_markdown(ranked, title=title)
        extracted = [
            {
                "number": s.number,
                "name": s.name,
                "latestReviewCount": s.latest_review_count,
                "starScore": s.star_score,
                "isBaeminClub": s.is_baemin_club,
                "menus": list(s.menus),
            }
            for s in ranked
        ]
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
                "lat": cfg.lat,
                "lng": cfg.lng,
                "count": len(shops),
                "top": extracted[0]["name"] if extracted else None,
            },
        )
