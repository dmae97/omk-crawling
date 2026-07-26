"""IPA static analysis adapter."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from omk_crawl.mobile.ipa_analyze import analyze_ipa
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.tools.base import BaseTool


def _resolve_ipa_path(url: str) -> Path | None:
    raw = url.strip()
    if raw.startswith("ipa://"):
        raw = unquote(raw[len("ipa://") :])
    elif raw.startswith("file://"):
        raw = unquote(urlparse(raw).path)
    p = Path(raw).expanduser()
    if p.is_file() and p.suffix.lower() in {".ipa", ".zip"}:
        return p.resolve()
    return None


class IpaTool(BaseTool):
    """Static reverse of iOS packages → bundle id, schemes, URLs."""

    name = "ipa"
    pip_package = ""
    layer = 5
    needs_browser = False
    capabilities: frozenset[str] = frozenset({"timeout"})

    def available(self) -> bool:
        return True

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        t0 = time.perf_counter()
        path = _resolve_ipa_path(url)
        if path is None:
            cand = Path(url)
            if cand.is_file():
                path = cand.resolve()
        if path is None:
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                tool=self.name,
                error="Not an IPA path. Use /path/app.ipa or ipa:///path/app.ipa",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata=self.contract_metadata(kwargs),
            )
        report = analyze_ipa(path)
        md = report.to_markdown()
        ok = bool(report.bundle_id or report.urls)
        return CrawlResult(
            url=str(path),
            status=CrawlStatus.OK if ok else CrawlStatus.ERROR,
            status_code=200 if ok else None,
            markdown=md,
            fit_markdown=md,
            extracted=[report.to_dict()],
            tool=self.name,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            error="; ".join(report.errors) if report.errors and not ok else None,
            metadata={
                **self.contract_metadata(kwargs),
                "bundle_id": report.bundle_id,
                "url_count": len(report.urls),
                "json": json.dumps(report.to_dict(), ensure_ascii=False)[:50_000],
            },
        )
