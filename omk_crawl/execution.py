"""Per-crawl accounting and adapter selection; no shared mutable run state."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.stability import TimeoutBudget
from omk_crawl.tools.base import BaseTool


def eligible_tools(
    tools: list[BaseTool], kwargs: dict[str, Any], *, allow_browser: bool, allow_llm: bool
) -> tuple[list[BaseTool], list[dict[str, Any]]]:
    eligible: list[BaseTool] = []
    skipped: list[dict[str, Any]] = []
    for tool in tools:
        unsupported = tool.unsupported_features(kwargs)
        reason = ""
        if tool.needs_llm and not allow_llm:
            reason = "llm_disabled"
        elif tool.needs_browser and not allow_browser:
            reason = "browser_disabled"
        elif unsupported:
            reason = "unsupported_features"
        if reason:
            skipped.append({"tool": tool.name, "reason": reason, "unsupported": unsupported})
        else:
            eligible.append(tool)
    return eligible, skipped


@dataclass
class Execution:
    total_timeout: float | None
    max_fetches: int
    target_tool: str | None = None
    rate_limit: bool = True
    skipped: list[dict[str, Any]] = field(default_factory=list)
    fetches: int = 0
    trace: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None
    started: float = field(default_factory=lambda: time.monotonic())
    budget: TimeoutBudget | None = field(init=False)

    def __post_init__(self) -> None:
        self.budget = TimeoutBudget(self.total_timeout) if self.total_timeout is not None else None

    def stopped(self, *, delay: float = 0.0, before_fetch: bool = False) -> bool:
        if self.stop_reason is not None:
            return True
        if self.budget is not None and self.budget.remaining <= delay:
            self.stop_reason = "deadline_exceeded"
        elif before_fetch and self.fetches >= self.max_fetches:
            self.stop_reason = "fetch_limit"
        return self.stop_reason is not None

    def tool_kwargs(self, tool: BaseTool, kwargs: dict[str, Any]) -> dict[str, Any]:
        copied = dict(kwargs)
        if self.budget is not None and tool.supports("timeout"):
            copied["timeout"] = min(
                copied.get("timeout") or tool.default_timeout, self.budget.remaining
            )
        return copied

    def record(self, tool: BaseTool, result: CrawlResult, retry: int, started: float) -> None:
        self.trace.append(
            {
                "tool": tool.name,
                "status": result.status.value,
                "status_code": result.status_code,
                "retry": retry,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
            }
        )

    def attach(self, result: CrawlResult) -> CrawlResult:
        if self.stop_reason:
            result.status = CrawlStatus.ERROR
            result.error = f"Execution stopped: {self.stop_reason}"
            result.metadata["stop_reason"] = self.stop_reason
        result.metadata["execution"] = {
            "target_tool": self.target_tool,
            "fetches": self.fetches,
            "max_fetches": self.max_fetches,
            "total_timeout": self.total_timeout,
            "timeout_enforcement": "cooperative",
            "elapsed_ms": round((time.monotonic() - self.started) * 1000, 2),
            "trace": list(self.trace),
            "skipped": list(self.skipped),
        }
        return result


def adapter_failure(url: str, tool: BaseTool, error: Exception) -> CrawlResult:
    result = BaseTool._error(tool, url, error)
    result.error = f"{tool.name} raised {type(error).__name__}"
    if isinstance(error, ImportError):
        result.status = CrawlStatus.TOOL_MISSING
        result.metadata["failure_kind"] = "dependency_missing"
    return result
