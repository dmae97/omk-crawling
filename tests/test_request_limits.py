"""Bounds and concurrency guards for retry scheduling."""

import asyncio
import math

import pytest

import omk_crawl.router as router_module
from omk_crawl.router import SmartRouter

from .test_request_recovery import URL, ScriptedTool, response, router_for


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_unsafe_method_is_not_retried_or_escalated(monkeypatch, mode):
    tool = ScriptedTool(response(503))
    fallback = ScriptedTool(response(200, markdown="# Must not run"))
    router = router_for(monkeypatch, tool, fallback, retry_delay=0)
    if mode == "sync":
        result = router.crawl(URL, method="POST")
    else:
        result = asyncio.run(router.crawl_async(URL, method="POST"))
    assert not result.ok
    assert result.metadata["stop_reason"] == "unsafe_method"
    assert tool.calls == 1
    assert fallback.calls == 0


def test_server_backpressure_stops_even_without_retry_budget(monkeypatch):
    tool = ScriptedTool(response(503, html="Checking your browser", headers={"Retry-After": "3"}))
    fallback = ScriptedTool(response(200, markdown="# Must not run"))
    result = router_for(monkeypatch, tool, fallback, max_retries=0).crawl(URL)
    assert not result.ok
    assert result.metadata["stop_reason"] == "retry_exhausted"
    assert fallback.calls == 0


@pytest.mark.parametrize("field", ["min_delay", "retry_delay", "max_retry_delay"])
@pytest.mark.parametrize("value", [-1, math.nan, math.inf])
def test_invalid_delay_rejected(field, value):
    with pytest.raises(ValueError, match=field):
        SmartRouter(**{field: value})


@pytest.mark.parametrize("kwargs", [{"max_attempts": 0}, {"max_retries": -1}, {"max_retries": 1.5}])
def test_invalid_attempt_count_rejected(kwargs):
    with pytest.raises(ValueError):
        SmartRouter(**kwargs)


def test_sync_wait_does_not_hold_other_domains_lock(monkeypatch):
    clock = [100.0]
    router = SmartRouter(min_delay=1.0, learn_sites=False)
    monkeypatch.setattr(router_module, "_last_request", {"busy.test": 100.0})
    monkeypatch.setattr(router_module.time, "monotonic", lambda: clock[0])

    def sleep(delay):
        assert router_module._rate_lock.acquire(blocking=False), "global lock held during sleep"
        router_module._rate_lock.release()
        router._rate_limit("https://free.test")
        clock[0] += delay

    monkeypatch.setattr(router_module.time, "sleep", sleep)
    router._rate_limit("https://busy.test")
    assert clock[0] == 101.0


def test_concurrent_async_waiters_keep_domain_spacing(monkeypatch):
    router = SmartRouter(min_delay=0.01, learn_sites=False)
    monkeypatch.setattr(router_module, "_last_request", {})

    async def scenario():
        starts = []

        async def acquire():
            await router._rate_limit_async(URL)
            starts.append(router_module.time.monotonic())

        await asyncio.gather(*(acquire() for _ in range(4)))
        assert all(b - a >= 0.009 for a, b in zip(starts, starts[1:]))

    asyncio.run(scenario())


def test_backoff_is_capped(monkeypatch):
    delays = []
    monkeypatch.setattr(router_module.time, "sleep", delays.append)
    tool = ScriptedTool(response(503))
    router_for(monkeypatch, tool, max_retries=3, retry_delay=1, max_retry_delay=2).crawl(URL)
    assert delays == [1, 2, 2]
