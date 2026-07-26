"""APK static analysis adapter — package:// or path to .apk."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from omk_crawl.mobile.apk_analyze import analyze_apk
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.tools.base import BaseTool


def _resolve_apk_path(url: str) -> Path | None:
    """Accept filesystem path, file://, or apk://path|package://path."""
    raw = url.strip()
    if raw.startswith("apk://"):
        raw = unquote(raw[len("apk://") :])
    elif raw.startswith("package://"):
        raw = unquote(raw[len("package://") :])
    elif raw.startswith("file://"):
        raw = unquote(urlparse(raw).path)
    p = Path(raw).expanduser()
    if p.is_file() and p.suffix.lower() in {".apk", ".xapk", ".apks"}:
        return p.resolve()
    return None


class ApkTool(BaseTool):
    """Static reverse of Android packages → URLs, perms, API hints."""

    name = "apk"
    pip_package = ""  # host tools optional (aapt/androguard)
    layer = 5
    needs_browser = False
    capabilities: frozenset[str] = frozenset({"timeout"})

    def available(self) -> bool:
        return True  # pure-python zip path always works

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        t0 = time.perf_counter()
        path = _resolve_apk_path(url)
        if path is None:
            # also try relative cwd
            cand = Path(url)
            if cand.is_file():
                path = cand.resolve()
        if path is None:
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                tool=self.name,
                error="Not an APK path. Use /path/app.apk or apk:///path/app.apk",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata=self.contract_metadata(kwargs),
            )
        report = analyze_apk(path)
        md = report.to_markdown()
        ok = bool(report.package or report.urls or report.dex_count)
        return CrawlResult(
            url=str(path),
            status=CrawlStatus.OK if ok else CrawlStatus.ERROR,
            status_code=200 if ok else None,
            markdown=md,
            fit_markdown=md,
            html=None,
            extracted=[report.to_dict()],
            tool=self.name,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            error="; ".join(report.errors) if report.errors and not ok else None,
            metadata={
                **self.contract_metadata(kwargs),
                "package": report.package,
                "url_count": len(report.urls),
                "analyzer": report.tool,
                "json": json.dumps(report.to_dict(), ensure_ascii=False)[:50_000],
            },
        )
