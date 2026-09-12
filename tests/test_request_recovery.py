"""Public router regressions; no external services or browser dependencies."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any

import pytest

import omk_crawl.router as router_module
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.router import SmartRouter
from omk_crawl.tools.base import BaseTool

URL = "https://recovery.example.test/page"


class ScriptedTool(BaseTool):
    name = "scripted"

    def __init__(self, *responses: CrawlResult) -> None:
        self.responses = responses
        self.calls = 0

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        result = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        result.url = url
        result.tool = self.name
        return result


def response(code: int, **kwargs: Any) -> CrawlResult:
    status = CrawlStatus.OK if code < 400 else CrawlStatus.ERROR
    if code in (401, 403, 429):
        status = CrawlStatus.BLOCKED
    return CrawlResult(url=URL, status=status, status_code=code, **kwargs)


def router_for(monkeypatch, *tools: BaseTool, **kwargs: Any) -> SmartRouter:
    router = SmartRouter(respect_robots=False, min_delay=0, learn_sites=False, **kwargs)
    monkeypatch.setattr(router, "_get_chain", lambda: list(tools))
    return router


def run(router: SmartRouter, mode: str) -> CrawlResult:
    if mode == "async":
        return asyncio.run(router.crawl_async(URL))
    return router.crawl(URL)


@pytest.fixture
def waits(monkeypatch) -> list[float]:
    delays: list[float] = []
    monkeypatch.setattr(router_module.time, "sleep", delays.append)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    return delays


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("code", [408, 429, 502, 503, 504])
def test_retryable_http_status_recovers(monkeypatch, waits, mode, code):
    tool = ScriptedTool(response(code), response(200, markdown="# Recovered"))
    router = router_for(monkeypatch, tool, max_retries=1, retry_delay=0.25)
    result = run(router, mode)
    assert result.ok
    assert tool.calls == 2
    assert waits == [0.25]
    assert result.metadata["retry_count"] == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("header", ["7", "date"])
def test_retry_after_is_honored(monkeypatch, waits, mode, header):
    now = 1_800_000_000.0
    monkeypatch.setattr(router_module.time, "time", lambda: now)
    if header == "date":
        header = format_datetime(datetime.fromtimestamp(now + 7, timezone.utc), usegmt=True)
    tool = ScriptedTool(
        response(429, headers={"rEtRy-AfTeR": header}),
        response(200, markdown="# Recovered"),
    )
    result = run(router_for(monkeypatch, tool), mode)
    assert result.ok
    assert waits == [7.0]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("header", ["no date", "-5", "NaN", "inf", "1.5"])
def test_invalid_retry_after_uses_backoff(monkeypatch, waits, mode, header):
    tool = ScriptedTool(
        response(503, headers={"Retry-After": header}), response(200, markdown="# Recovered")
    )
    assert run(router_for(monkeypatch, tool, retry_delay=0.5), mode).ok
    assert waits == [0.5]


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("header", ["120", pytest.param("9" * 5000, id="oversized-delta")])
def test_long_retry_after_stops_without_early_retry_or_escalation(monkeypatch, waits, mode, header):
    limited = ScriptedTool(response(503, headers={"Retry-After": header}))
    fallback = ScriptedTool(response(200, markdown="# Must not run"))
    router = router_for(monkeypatch, limited, fallback, max_retry_delay=10)
    result = run(router, mode)
    assert not result.ok
    assert limited.calls == 1
    assert fallback.calls == 0
    assert waits == []
    assert result.metadata["stop_reason"] == "retry_after_limit"
    assert result.metadata["retry_after_seconds"] >= 120


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_rate_limit_exhaustion_does_not_switch_tools(monkeypatch, waits, mode):
    limited = ScriptedTool(response(429))
    fallback = ScriptedTool(response(200, markdown="# Must not run"))
    router = router_for(monkeypatch, limited, fallback, max_retries=2, retry_delay=0.1)
    result = run(router, mode)
    assert not result.ok
    assert limited.calls == 3
    assert fallback.calls == 0
    assert waits == [0.1, 0.2]
    assert result.metadata["stop_reason"] == "rate_limited"
    assert result.metadata["retry_count"] == 2


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_auth_stop_returns_auth_result_not_larger_earlier_page(monkeypatch, waits, mode):
    blocked = ScriptedTool(response(403, html="Access denied " * 100))
    auth = ScriptedTool(response(401, html="Login required", error="session timed out"))
    fallback = ScriptedTool(response(200, markdown="# Must not run"))
    result = run(router_for(monkeypatch, blocked, auth, fallback), mode)
    assert result.status_code == 401
    assert result.metadata["auth_stop"]
    assert result.metadata["stop_reason"] == "auth_required"
    assert result.metadata["attempts"] == 2
    assert auth.calls == 1
    assert fallback.calls == 0


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_attempts_are_per_crawl_not_lifetime_history(monkeypatch, mode):
    router = router_for(monkeypatch, ScriptedTool(response(403, html="Access denied")))
    run(router, mode)
    result = run(router, mode)
    assert len(router.history) == 2
    assert result.metadata["attempts"] == 1


def test_async_rate_wait_does_not_block_event_loop(monkeypatch):
    main_thread = threading.get_ident()
    router = SmartRouter(min_delay=0.02, respect_robots=False, learn_sites=False)
    tool = ScriptedTool(response(200, markdown="# OK"))
    monkeypatch.setattr(router, "_get_chain", lambda: [tool])
    original_sleep = router_module.time.sleep

    def check_sleep(delay):
        assert threading.get_ident() != main_thread, "blocking sleep on event loop"
        original_sleep(delay)

    monkeypatch.setattr(router_module.time, "sleep", check_sleep)
    monkeypatch.setattr(router_module, "_last_request", {})

    async def scenario():
        first = await router.crawl_async(URL)
        second = await router.crawl_async(URL)
        assert first.ok and second.ok

    asyncio.run(scenario())


def test_async_robots_check_runs_off_event_loop(monkeypatch):
    main_thread = threading.get_ident()
    router = SmartRouter(min_delay=0, learn_sites=False)
    tool = ScriptedTool(response(200, markdown="# OK"))
    monkeypatch.setattr(router, "_get_chain", lambda: [tool])

    def check_robots(_url):
        assert threading.get_ident() != main_thread, "robots I/O on event loop"
        return True

    monkeypatch.setattr(router, "_check_robots", check_robots)
    assert asyncio.run(router.crawl_async(URL)).ok


def test_public_async_convenience_applies_router_options(monkeypatch, tmp_path):
    monkeypatch.setenv("OMK_CRAWL_SITE_MEMORY", str(tmp_path / "site-memory.json"))
    tool = ScriptedTool(response(200, markdown="# OK"))
    monkeypatch.setattr(SmartRouter, "_get_chain", lambda _self: [tool])

    def forbidden(_url):
        pytest.fail("respect_robots=False was forwarded to the tool instead of the router")

    monkeypatch.setattr(SmartRouter, "_check_robots", staticmethod(forbidden))
    result = asyncio.run(router_module.crawl_async(URL, respect_robots=False, min_delay=0))
    assert result.ok


def test_cancelled_retry_never_starts_another_request(monkeypatch):
    tool = ScriptedTool(response(429))
    router = router_for(monkeypatch, tool, retry_delay=30)

    async def scenario():
        waiting = asyncio.Event()

        async def sleep(_delay):
            waiting.set()
            await asyncio.Future()

        monkeypatch.setattr(asyncio, "sleep", sleep)
        task = asyncio.create_task(router.crawl_async(URL))
        try:
            await asyncio.wait_for(waiting.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert tool.calls == 1

    asyncio.run(scenario())
