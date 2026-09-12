"""Tests for adaptive per-site tool ordering."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.router import SmartRouter
from omk_crawl.routing import SiteMemory
from omk_crawl.tools.base import BaseTool


class _BlockedTool(BaseTool):
    name = "blocked"
    pip_package = ""

    def available(self) -> bool:
        return True

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        return CrawlResult(
            url=url,
            status=CrawlStatus.BLOCKED,
            status_code=403,
            tool=self.name,
            elapsed_ms=10.0,
        )


class _WorkingTool(BaseTool):
    name = "working"
    pip_package = ""

    def available(self) -> bool:
        return True

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        return CrawlResult(
            url=url,
            status=CrawlStatus.OK,
            status_code=200,
            html="<h1>ok</h1>",
            markdown="# ok",
            tool=self.name,
            elapsed_ms=20.0,
        )


class _FakeMemory:
    def __init__(self) -> None:
        self.outcomes: list[tuple[str, str, bool]] = []

    def order(self, url: str, tools: list[str]) -> list[str]:
        assert url == "https://example.com/page"
        assert tools == ["blocked", "working"]
        return ["working", "blocked"]

    def record(self, url: str, tool: str, success: bool, latency_ms: float) -> None:
        self.outcomes.append((url, tool, success))


class _NeverMemory:
    def order(self, url: str, tools: list[str]) -> list[str]:
        raise AssertionError("explicit tool order must not consult site memory")

    def record(self, url: str, tool: str, success: bool, latency_ms: float) -> None:
        raise AssertionError("explicit tool runs must not update site memory")


def test_memory_persists_and_reorders_known_outcomes(tmp_path) -> None:
    # Given
    path = tmp_path / "site-memory.json"
    memory = SiteMemory(path)
    memory.record("https://example.com/a", "blocked", False, 10.0)
    memory.record("https://example.com/b", "working", True, 20.0)

    # When
    order = SiteMemory(path).order(
        "https://example.com/next",
        ["blocked", "unknown", "working"],
    )

    # Then
    assert order == ["working", "unknown", "blocked"]


def test_router_learns_across_instances_by_default(monkeypatch, tmp_path) -> None:
    # Given
    import omk_crawl.router as router_module

    monkeypatch.setenv("OMK_CRAWL_SITE_MEMORY", str(tmp_path / "memory.json"))
    monkeypatch.setattr(router_module, "ESCALATION_CHAIN", [_BlockedTool, _WorkingTool])
    first = SmartRouter(respect_robots=False, min_delay=0)
    first.crawl("https://example.com/page")

    # When
    second = SmartRouter(respect_robots=False, min_delay=0)
    result = second.crawl("https://example.com/other")

    # Then
    assert result.tool == "working"
    assert [attempt.tool for attempt in second.history] == ["working"]


def test_router_uses_site_memory_before_first_attempt(monkeypatch) -> None:
    # Given
    import omk_crawl.router as router_module

    monkeypatch.setattr(router_module, "ESCALATION_CHAIN", [_BlockedTool, _WorkingTool])
    memory = _FakeMemory()

    # When
    router = SmartRouter(
        site_memory=memory,
        respect_robots=False,
        min_delay=0,
    )
    result = router.crawl("https://example.com/page")

    # Then
    assert result.tool == "working"
    assert [attempt.tool for attempt in router.history] == ["working"]
    expected_outcomes = [("https://example.com/page", "working", True)]
    assert memory.outcomes == expected_outcomes


def test_async_router_learns_across_instances(monkeypatch, tmp_path) -> None:
    import omk_crawl.router as router_module

    monkeypatch.setenv("OMK_CRAWL_SITE_MEMORY", str(tmp_path / "memory.json"))
    monkeypatch.setattr(router_module, "ESCALATION_CHAIN", [_BlockedTool, _WorkingTool])
    first = SmartRouter(respect_robots=False, min_delay=0)
    asyncio.run(first.crawl_async("https://example.com/page"))

    second = SmartRouter(respect_robots=False, min_delay=0)
    result = asyncio.run(second.crawl_async("https://example.com/other"))

    assert result.tool == "working"
    assert [attempt.tool for attempt in second.history] == ["working"]


def test_new_options_preserve_positional_history_and_decisions() -> None:
    parameters = list(inspect.signature(SmartRouter).parameters)

    assert parameters[:10] == [
        "tools",
        "max_attempts",
        "max_retries",
        "retry_delay",
        "min_delay",
        "respect_robots",
        "verbose",
        "tool_kwargs",
        "history",
        "decisions",
    ]


def test_explicit_tool_order_bypasses_site_memory(monkeypatch) -> None:
    import omk_crawl.router as router_module

    tools = {"blocked": _BlockedTool(), "working": _WorkingTool()}
    monkeypatch.setattr(router_module, "get_tool", tools.__getitem__)
    router = SmartRouter(
        tools=["working", "blocked"],
        site_memory=_NeverMemory(),
        respect_robots=False,
        min_delay=0,
    )

    result = router.crawl("https://example.com/page")

    assert result.tool == "working"
    assert [attempt.tool for attempt in router.history] == ["working"]


def test_corrupt_site_memory_preserves_default_order(tmp_path) -> None:
    path = tmp_path / "site-memory.json"
    path.write_text("{not-json", encoding="utf-8")

    order = SiteMemory(path).order(
        "https://example.com/page",
        ["first", "second"],
    )

    assert order == ["first", "second"]


def test_site_memory_file_is_private(tmp_path) -> None:
    path = tmp_path / "site-memory.json"

    SiteMemory(path).record("https://example.com", "working", True, 10.0)

    assert path.stat().st_mode & 0o777 == 0o600


def test_stale_instances_merge_domains(tmp_path) -> None:
    path = tmp_path / "site-memory.json"
    first = SiteMemory(path)
    second = SiteMemory(path)

    first.record("https://alpha.example", "alpha-tool", True, 10.0)
    second.record("https://beta.example", "beta-tool", True, 10.0)

    reloaded = SiteMemory(path)
    assert reloaded.order("https://alpha.example", ["other", "alpha-tool"]) == [
        "alpha-tool",
        "other",
    ]
    assert reloaded.order("https://beta.example", ["other", "beta-tool"]) == [
        "beta-tool",
        "other",
    ]


def test_malformed_url_and_large_integer_json_fail_open(tmp_path) -> None:
    path = tmp_path / "site-memory.json"
    huge_integer = "9" * 5000
    path.write_text(
        '{"domains":{"example.com":{"tool":{"successes":'
        f"{huge_integer}"
        ',"failures":0,"total_latency_ms":0}}}}',
        encoding="utf-8",
    )
    memory = SiteMemory(path)

    assert memory.order("http://[invalid", ["first", "second"]) == [
        "first",
        "second",
    ]
