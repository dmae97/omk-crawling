"""Plan and execute the same target-aware route in sync, async, and dry-run modes."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from omk_crawl.detect import Detection, missing_tools
from omk_crawl.execution import Execution, adapter_failure, eligible_tools
from omk_crawl.pipeline import normalize_result
from omk_crawl.request_runner import fetch_async, fetch_sync
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.retry_after import retry_after_seconds
from omk_crawl.routing import CONFIDENCE_THRESHOLD, is_auth_block, preferred_order, reorder_tools
from omk_crawl.targets import Target, resolve_target
from omk_crawl.tools import get_tool
from omk_crawl.tools.base import BaseTool

if TYPE_CHECKING:
    from omk_crawl.router import SmartRouter


@dataclass(repr=False)
class _Plan:
    target: Target
    chain: list[BaseTool]
    execution: Execution
    kwargs: dict[str, Any]
    error: CrawlResult | None = None


def _prepare(router: SmartRouter, url: str, kwargs: dict[str, Any]) -> _Plan:
    merged = {**router.tool_kwargs, **kwargs}
    if not router.allow_browser:
        merged["stealth"] = False
    execution = Execution(
        router.total_timeout,
        router.max_fetches or router.max_attempts * (router.max_retries + 1),
    )
    plan = _Plan(Target(url, None, False), [], execution, merged)
    try:
        plan.target = resolve_target(url, explicit=router.tools is not None)
    except (ValueError, OSError):
        plan.error = CrawlResult(
            url=url,
            status=CrawlStatus.ERROR,
            error="Invalid crawl target",
            metadata={"stop_reason": "invalid_target"},
        )
        return plan
    timeout = merged.get("timeout")
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout < math.inf
    ):
        plan.error = CrawlResult(
            url=url,
            status=CrawlStatus.ERROR,
            error="timeout must be positive and finite",
            metadata={"stop_reason": "invalid_options"},
        )
        return plan
    chain = (
        [get_tool(plan.target.tool)]
        if plan.target.tool and router.tools is None
        else router._get_chain()
    )
    if not plan.target.is_web and router.tools is not None and len(chain) == 1:
        plan.target = Target(url, chain[0].name, False)
    execution.target_tool = plan.target.tool
    execution.rate_limit = plan.target.is_web
    plan.chain, execution.skipped = eligible_tools(
        chain, merged, allow_browser=router.allow_browser, allow_llm=router.allow_llm
    )
    if router.learn_sites and router.tools is None and plan.target.is_web:
        plan.chain = reorder_tools(
            plan.chain, router.site_memory.order(url, [tool.name for tool in plan.chain])
        )
    if not plan.chain:
        reason = "no_eligible_tools" if execution.skipped else "tool_missing"
        plan.error = CrawlResult(
            url=url,
            status=CrawlStatus.ERROR if execution.skipped else CrawlStatus.TOOL_MISSING,
            error="No eligible crawling tools. Check execution.skipped and installed dependencies.",
            metadata={"stop_reason": reason},
        )
    return plan


def _stop_reason(result: CrawlResult, detection: Detection, method: str) -> str | None:
    if result.status_code in (401, 407) or is_auth_block(detection.block):
        result.metadata["auth_stop"] = True
        return "auth_required"
    if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        return "unsafe_method"
    if result.metadata.get("retry_stop") == "retry_after_limit":
        return "retry_after_limit"
    if result.status_code == 429:
        return "rate_limited"
    if result.status_code is not None and result.status_code >= 400:
        delay = retry_after_seconds(result.headers)
        if delay is not None:
            result.metadata["retry_after_seconds"] = delay
            return "retry_exhausted"
    if result.status is CrawlStatus.ERROR and (
        result.status_code is not None or result.metadata.get("failure_kind") != "adapter_exception"
    ):
        return "hard_error"
    return None


def _finish(
    router: SmartRouter, plan: _Plan, result: CrawlResult, attempts: int, reason: str
) -> CrawlResult:
    result.metadata.update(attempts=attempts, stop_reason=reason)
    router._ensure_markdown(result)
    plan.execution.stopped()
    return plan.execution.attach(result)


def _observe(router: SmartRouter, plan: _Plan, tool: BaseTool, result: CrawlResult, attempt: int):
    try:
        detection = (
            normalize_result(result)
            if plan.target.is_web
            else Detection(status_code=result.status_code)
        )
    except (TypeError, ValueError, AttributeError) as error:
        result = adapter_failure(plan.target.value, tool, error)
        detection = Detection()
    if plan.execution.stopped():
        result.status = CrawlStatus.ERROR
    result.metadata.update(detection=detection.detail, block_type=detection.block.name)
    if plan.execution.trace:
        plan.execution.trace[-1]["status"] = result.status.value
    router.history.append(result)
    if router.learn_sites and router.tools is None and plan.target.is_web:
        router.site_memory.record(plan.target.value, tool.name, result.ok, result.elapsed_ms)
    router._record_decision(tool.name, router._escalation_reason(result), attempt, detection.detail)
    return result, detection


def _reroute(router: SmartRouter, detection: Detection, pending: list[BaseTool]) -> list[BaseTool]:
    if router.tools is None and detection.confidence >= CONFIDENCE_THRESHOLD:
        names = [tool.name for tool in pending]
        return reorder_tools(pending, preferred_order(detection.block, detection.confidence, names))
    return pending


def _exhausted(
    router: SmartRouter,
    plan: _Plan,
    best: CrawlResult | None,
    attempts: int,
    pending: list[BaseTool],
) -> CrawlResult:
    result = best or CrawlResult(url=plan.target.value, status=CrawlStatus.ERROR)
    result.metadata["escalation_exhausted"] = True
    return _finish(router, plan, result, attempts, "max_attempts" if pending else "tools_exhausted")


def diagnose(router: SmartRouter, url: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    from omk_crawl.routing import ROUTE_TABLE

    plan = _prepare(router, url, kwargs)
    names = [tool.name for tool in plan.chain]
    return {
        "url": url,
        "target_tool": plan.target.tool,
        "available_tools": [tool.name for tool in plan.chain if tool.available()],
        "missing_tools": missing_tools(),
        "escalation_order": names[: router.max_attempts],
        "routing": {block.name: preferred_order(block, 0.9, names) for block in ROUTE_TABLE},
        "skipped": plan.execution.skipped,
        "execution_limits": {
            "total_timeout": router.total_timeout,
            "max_fetches": plan.execution.max_fetches,
            "max_attempts": router.max_attempts,
            "allow_browser": router.allow_browser,
            "allow_llm": router.allow_llm,
        },
        "error": plan.error.error if plan.error is not None else None,
        "install_hint": "pip install omk-crawl[all]",
    }


def crawl_sync(router: SmartRouter, url: str, kwargs: dict[str, Any]) -> CrawlResult:
    plan = _prepare(router, url, kwargs)
    if plan.error is not None:
        return plan.execution.attach(plan.error)
    if plan.target.is_web and router.respect_robots and not router._check_robots(url):
        return _finish(
            router,
            plan,
            CrawlResult(url=url, status=CrawlStatus.ERROR, error="Blocked by robots.txt."),
            0,
            "robots_denied",
        )
    best = None
    pending = list(plan.chain)
    attempts = 0
    rerouted = False
    while pending and attempts < router.max_attempts:
        if plan.execution.stopped(before_fetch=True):
            break
        tool = pending.pop(0)
        attempts += 1
        router._log(f"[{attempts}/{len(plan.chain)}] Trying {tool.name}...")
        result = fetch_sync(router, plan.execution, tool, url, plan.kwargs)
        result, detection = _observe(router, plan, tool, result, attempts)
        if result.ok:
            router._log(f"  ✓ {tool.name} succeeded ({result.elapsed_ms:.0f}ms)")
            return _finish(router, plan, result, attempts, "success")
        reason = _stop_reason(result, detection, str(plan.kwargs.get("method", "GET")))
        if reason or plan.execution.stop_reason:
            return _finish(router, plan, result, attempts, reason or "execution_stopped")
        if best is None or router._score(result) > router._score(best):
            best = result
        if not rerouted and pending:
            reordered = _reroute(router, detection, pending)
            if reordered != pending:
                result.metadata["rerouted_to"] = [tool.name for tool in reordered]
            pending = reordered
            rerouted = detection.confidence >= CONFIDENCE_THRESHOLD
    return _exhausted(router, plan, best, attempts, pending)


async def crawl_async(router: SmartRouter, url: str, kwargs: dict[str, Any]) -> CrawlResult:
    plan = _prepare(router, url, kwargs)
    if plan.error is not None:
        return plan.execution.attach(plan.error)
    if (
        plan.target.is_web
        and router.respect_robots
        and not await asyncio.to_thread(router._check_robots, url)
    ):
        return _finish(
            router,
            plan,
            CrawlResult(url=url, status=CrawlStatus.ERROR, error="Blocked by robots.txt."),
            0,
            "robots_denied",
        )
    best = None
    pending = list(plan.chain)
    attempts = 0
    rerouted = False
    while pending and attempts < router.max_attempts:
        if plan.execution.stopped(before_fetch=True):
            break
        tool = pending.pop(0)
        attempts += 1
        result = await fetch_async(router, plan.execution, tool, url, plan.kwargs)
        result, detection = _observe(router, plan, tool, result, attempts)
        if result.ok:
            return _finish(router, plan, result, attempts, "success")
        reason = _stop_reason(result, detection, str(plan.kwargs.get("method", "GET")))
        if reason or plan.execution.stop_reason:
            return _finish(router, plan, result, attempts, reason or "execution_stopped")
        if best is None or router._score(result) > router._score(best):
            best = result
        if not rerouted and pending:
            reordered = _reroute(router, detection, pending)
            if reordered != pending:
                result.metadata["rerouted_to"] = [tool.name for tool in reordered]
            pending = reordered
            rerouted = detection.confidence >= CONFIDENCE_THRESHOLD
    return _exhausted(router, plan, best, attempts, pending)
