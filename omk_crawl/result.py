"""Unified crawl result across all tools."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CrawlStatus(Enum):
    """Outcome of a crawl attempt."""

    OK = "ok"
    BLOCKED = "blocked"  # 403 / WAF / anti-bot
    TLS_BLOCKED = "tls_blocked"  # fingerprint-based rejection
    JS_REQUIRED = "js_required"  # empty body, needs rendering
    ERROR = "error"
    TOOL_MISSING = "tool_missing"


@dataclass
class CrawlResult:
    """Tool-agnostic crawl output."""

    url: str
    status: CrawlStatus
    status_code: int | None = None
    html: str | None = None
    markdown: str | None = None
    fit_markdown: str | None = None
    extracted: list[dict[str, Any]] | None = None
    tool: str = ""
    elapsed_ms: float = 0.0
    error: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is CrawlStatus.OK

    @property
    def blocked(self) -> bool:
        return self.status in (CrawlStatus.BLOCKED, CrawlStatus.TLS_BLOCKED)

    @property
    def content(self) -> str | None:
        """Best available text content."""
        return self.fit_markdown or self.markdown or self.html

    def summary(self) -> str:
        parts = [f"[{self.status.value}] {self.url}"]
        if self.tool:
            parts.append(f"via {self.tool}")
        if self.status_code:
            parts.append(f"HTTP {self.status_code}")
        parts.append(f"{self.elapsed_ms:.0f}ms")
        if self.content:
            parts.append(f"{len(self.content)} chars")
        if self.error:
            parts.append(f"err={self.error}")
        return " | ".join(parts)


def _timer() -> tuple[float, Any]:
    """Return (start_time, stop_fn). Call stop_fn() to get elapsed ms."""
    t0 = time.perf_counter()

    def stop() -> float:
        return (time.perf_counter() - t0) * 1000

    return t0, stop
