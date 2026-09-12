"""Request-scoped engine budgets, capability routing, and failure isolation."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest

from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.router import SmartRouter
from omk_crawl.tools.base import COMMON_KWARGS, BaseTool

URL = "https://engine.example.test/public"


class EngineTool(BaseTool):
    capabilities = frozenset(COMMON_KWARGS)

    def __init__(self, name: str, *responses: CrawlResult | Exception):
        self.name = name
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.elapsed = 0.0
        self.clock: list[float] | None = None

    def available(self) -> bool:
        return True

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        index = min(len(self.calls), len(self.responses) - 1)
        self.calls.append(kwargs)
        if self.clock is not None:
            self.clock[0] += self.elapsed
        response = self.responses[index]
        if isinstance(response, Exception):
            raise response
        return replace(response, url=url, tool=self.name, metadata=dict(response.metadata))


def ok() -> CrawlResult:
    return CrawlResult(url=URL, status=CrawlStatus.OK, status_code=200, markdown="# Real content")


def blocked() -> CrawlResult:
    return CrawlResult(url=URL, status=CrawlStatus.BLOCKED, status_code=403, html="Access denied")


def router_for(monkeypatch, *tools: EngineTool, **options: Any) -> SmartRouter:
    router = SmartRouter(respect_robots=False, min_delay=0, learn_sites=False, **options)
    monkeypatch.setattr(router, "_get_chain", lambda: list(tools))
    return router


def run(router: SmartRouter, mode: str, **kwargs: Any) -> CrawlResult:
    return (
        asyncio.run(router.crawl_async(URL, **kwargs))
        if mode == "async"
        else router.crawl(URL, **kwargs)
    )


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_complete_chain_is_available_by_default(monkeypatch, mode):
    tools = [EngineTool(f"blocked_{i}", blocked()) for i in range(6)]
    final = EngineTool("last", ok())
    result = run(router_for(monkeypatch, *tools, final), mode)
    assert result.ok
    assert result.tool == "last"
    assert result.metadata["attempts"] == 7


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_unsupported_session_is_skipped_without_losing_credentials(monkeypatch, mode):
    unsupported = EngineTool("unsupported", ok())
    unsupported.capabilities = frozenset({"timeout"})
    supported = EngineTool("supported", ok())
    result = run(
        router_for(monkeypatch, unsupported, supported), mode, cookies={"session": "s3cr3t"}
    )
    assert result.ok and result.tool == "supported"
    assert unsupported.calls == []
    assert supported.calls[0]["cookies"] == {"session": "s3cr3t"}
    execution = result.metadata["execution"]
    assert execution["skipped"][0]["unsupported"] == ["cookies"]
    assert "s3cr3t" not in json.dumps(execution)


def test_explicit_unsupported_tool_fails_before_fetch(monkeypatch):
    tool = EngineTool("unsupported", ok())
    tool.capabilities = frozenset()
    router = router_for(monkeypatch, tool)
    router.tools = ["unsupported"]
    result = router.crawl(URL, proxy="http://private-proxy.invalid")
    assert not result.ok
    assert tool.calls == []
    assert result.metadata["stop_reason"] == "no_eligible_tools"


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_paid_tool_needs_explicit_opt_in(monkeypatch, mode):
    paid = EngineTool("paid", ok())
    paid.needs_llm = True
    plain = EngineTool("plain", ok())
    router = router_for(monkeypatch, paid, plain)
    assert run(router, mode).tool == "plain"
    assert paid.calls == []
    router.allow_llm = True
    assert run(router, mode).tool == "paid"


def test_browser_can_be_disabled(monkeypatch):
    browser = EngineTool("browser", ok())
    browser.needs_browser = True
    plain = EngineTool("plain", ok())
    result = router_for(monkeypatch, browser, plain, allow_browser=False).crawl(URL)
    assert result.tool == "plain"
    assert browser.calls == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("error", [RuntimeError("private credential"), ImportError("optional SDK")])
def test_adapter_exception_does_not_abort_engine(monkeypatch, mode, error):
    broken = EngineTool("broken", error)
    working = EngineTool("working", ok())
    result = run(router_for(monkeypatch, broken, working, max_retries=0), mode)
    assert result.ok and result.tool == "working"
    trace = result.metadata["execution"]["trace"]
    assert [event["tool"] for event in trace] == ["broken", "working"]
    assert "private credential" not in json.dumps(trace)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_fetch_limit_spans_tools_and_retries(monkeypatch, mode):
    timeout = CrawlResult(url=URL, status=CrawlStatus.ERROR, error="connection reset")
    first = EngineTool("first", timeout, blocked())
    second = EngineTool("second", ok())
    router = router_for(monkeypatch, first, second, max_fetches=2, retry_delay=0)
    result = run(router, mode)
    assert not result.ok
    assert len(first.calls) == 2 and second.calls == []
    assert result.metadata["stop_reason"] == "fetch_limit"
    assert result.metadata["execution"]["fetches"] == 2


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_deadline_is_shared_and_late_success_is_not_claimed(monkeypatch, mode):
    clock = [100.0]
    monkeypatch.setattr("omk_crawl.stability.time.monotonic", lambda: clock[0])
    first, second = EngineTool("first", blocked()), EngineTool("second", ok())
    for tool in (first, second):
        tool.clock = clock
        tool.elapsed = 6
    result = run(router_for(monkeypatch, first, second, total_timeout=10), mode, timeout=30)
    assert [first.calls[0]["timeout"], second.calls[0]["timeout"]] == [10, 4]
    assert not result.ok
    assert result.metadata["stop_reason"] == "deadline_exceeded"
    assert result.metadata["execution"]["timeout_enforcement"] == "cooperative"


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_insufficient_retry_budget_does_not_sleep_or_refetch(monkeypatch, mode):
    def forbidden(_delay):
        pytest.fail("retry wait exceeds remaining deadline")

    async def forbidden_async(_delay):
        forbidden(_delay)

    monkeypatch.setattr("omk_crawl.router.time.sleep", forbidden)
    monkeypatch.setattr(asyncio, "sleep", forbidden_async)
    tool = EngineTool("limited", CrawlResult(url=URL, status=CrawlStatus.ERROR, status_code=503))
    result = run(router_for(monkeypatch, tool, total_timeout=1, retry_delay=5), mode)
    assert result.metadata["stop_reason"] == "deadline_exceeded"
    assert len(tool.calls) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_execution_trace_is_per_crawl(monkeypatch, mode):
    tool = EngineTool("working", ok())
    router = router_for(monkeypatch, tool)
    first, second = run(router, mode), run(router, mode)
    assert len(router.history) == 2
    assert first.metadata["execution"]["fetches"] == 1
    assert second.metadata["execution"]["fetches"] == 1
    assert len(second.metadata["execution"]["trace"]) == 1


@pytest.mark.parametrize(
    "options", [{"max_fetches": 0}, {"total_timeout": 0}, {"total_timeout": -1}]
)
def test_invalid_execution_limits_are_rejected(options):
    with pytest.raises(ValueError):
        SmartRouter(**options)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_success_on_last_allowed_fetch_stays_successful(monkeypatch, mode):
    result = run(router_for(monkeypatch, EngineTool("working", ok()), max_fetches=1), mode)
    assert result.ok
    assert result.metadata["execution"]["fetches"] == 1


def test_no_browser_policy_overrides_optional_browser_fallback(monkeypatch):
    tool = EngineTool("http_with_optional_browser", ok())
    router = router_for(monkeypatch, tool, allow_browser=False)
    assert router.crawl(URL, stealth=True).ok
    assert not tool.calls[0]["stealth"]


def test_retry_after_on_access_rejection_is_not_bypassed(monkeypatch):
    refusal = blocked()
    refusal.headers = {"Retry-After": "10"}
    first, second = EngineTool("refused", refusal), EngineTool("fallback", ok())
    result = router_for(monkeypatch, first, second).crawl(URL)
    assert not result.ok
    assert second.calls == []
    assert result.metadata["stop_reason"] == "retry_exhausted"


def test_returned_adapter_exception_can_use_next_backend(monkeypatch):
    first, second = EngineTool("wrapped"), EngineTool("working", ok())
    first.responses = (first._error(URL, RuntimeError("SDK failed")),)
    result = router_for(monkeypatch, first, second, max_retries=0).crawl(URL)
    assert result.ok and result.tool == "working"


@pytest.mark.parametrize("raised", [False, True])
@pytest.mark.parametrize(
    "code,reason", [(401, "auth_required"), (429, "rate_limited"), (500, "hard_error")]
)
def test_http_exception_status_is_preserved_before_fallback(monkeypatch, raised, code, reason):
    from types import SimpleNamespace

    class HttpError(Exception):
        response = SimpleNamespace(status_code=code, headers={})

    first, second = EngineTool("http_error"), EngineTool("working", ok())
    error = HttpError("HTTP request failed")
    first.responses = (error if raised else first._error(URL, error),)
    result = router_for(monkeypatch, first, second, max_retries=0).crawl(URL)
    assert not result.ok
    assert result.status_code == code
    assert result.metadata["stop_reason"] == reason
    assert second.calls == []
