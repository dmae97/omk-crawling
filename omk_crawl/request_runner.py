"""Invoke adapters with a shared execution budget and bounded retries."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from omk_crawl.execution import Execution, adapter_failure
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.tools.base import BaseTool

if TYPE_CHECKING:
    from omk_crawl.router import SmartRouter


def _checked(value: CrawlResult, tool: BaseTool, url: str) -> CrawlResult:
    if not isinstance(value, CrawlResult) or not isinstance(value.status, CrawlStatus):
        return adapter_failure(url, tool, TypeError("invalid adapter result"))
    return value


def fetch_sync(
    router: SmartRouter, execution: Execution, tool: BaseTool, url: str, kwargs: dict[str, Any]
) -> CrawlResult:
    result = CrawlResult(url=url, tool=tool.name, status=CrawlStatus.ERROR)
    for retry in range(router.max_retries + 1):
        if execution.stopped():
            break
        if retry:
            delay = router._retry_delay(result, retry - 1, str(kwargs.get("method", "GET")))
            if delay is None or execution.stopped(delay=delay, before_fetch=True):
                break
            time.sleep(delay)
        if execution.rate_limit:
            if execution.budget is None:
                router._rate_limit(url)
            else:
                while wait := router._rate_wait(url):
                    if execution.stopped(delay=wait):
                        break
                    time.sleep(wait)
        call_kwargs = execution.tool_kwargs(tool, kwargs)
        if execution.stopped(before_fetch=True):
            break
        started = time.monotonic()
        execution.fetches += 1
        try:
            result = _checked(tool.fetch(url, **call_kwargs), tool, url)
        except Exception as error:
            result = adapter_failure(url, tool, error)
        result.metadata["retry_count"] = retry
        execution.record(tool, result, retry, started)
        if execution.stopped():
            break
    return result


async def fetch_async(
    router: SmartRouter, execution: Execution, tool: BaseTool, url: str, kwargs: dict[str, Any]
) -> CrawlResult:
    result = CrawlResult(url=url, tool=tool.name, status=CrawlStatus.ERROR)
    for retry in range(router.max_retries + 1):
        if execution.stopped():
            break
        if retry:
            delay = router._retry_delay(result, retry - 1, str(kwargs.get("method", "GET")))
            if delay is None or execution.stopped(delay=delay, before_fetch=True):
                break
            await asyncio.sleep(delay)
        if execution.rate_limit:
            while wait := router._rate_wait(url):
                if execution.stopped(delay=wait):
                    break
                await asyncio.sleep(wait)
        call_kwargs = execution.tool_kwargs(tool, kwargs)
        if execution.stopped(before_fetch=True):
            break
        started = time.monotonic()
        execution.fetches += 1
        try:
            result = _checked(await tool.fetch_async(url, **call_kwargs), tool, url)
        except Exception as error:
            result = adapter_failure(url, tool, error)
        result.metadata["retry_count"] = retry
        execution.record(tool, result, retry, started)
        if execution.stopped():
            break
    return result
