"""Offline tests for the free-proxy pool rotation chain.

Covers: proxy_engine_v2 (parse/freshness/selection/burn/cache),
rotate_crawl (RotationCrawler feedback loop, pool_cycles), proxy_pool_tool
(scheme actions), and the target/registry wiring.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import omk_crawl.tools.proxy_engine_v2 as pev
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.rotate_crawl import (
    RotationCrawler,
    _classify,
    pool_cycles,
)
from omk_crawl.targets import resolve_target
from omk_crawl.tools.proxy_engine_v2 import ProxyEngineV2, ProxyNode

# ── fakes ──


class FakeNode:
    def __init__(self, url: str = "1.2.3.4:8080", tls: str = "chrome124"):
        self.url = url
        self.proxy_url = f"http://{url}"
        self.tls_profile = tls
        self.marks: list[str] = []

    def mark_success(self, latency_ms: float = 0) -> None:
        self.marks.append("success")

    def mark_failure(self, is_rate_limit: bool = False) -> None:
        self.marks.append("rate_limited" if is_rate_limit else "failure")


class FakeEngine:
    """Duck-typed FreshnessEngine: scripted nodes + recorded feedback."""

    _engine: Any | None = None  # optional inner engine (auto-refill seam)

    def __init__(self, nodes: list[FakeNode] | None = None):
        self.nodes = list(nodes or [])
        self.dead: list[FakeNode] = []
        self.successes: list[FakeNode] = []
        self.rate_limited: list[FakeNode] = []
        self.stats: dict[str, Any] = {"available": 10}
        self.harvest_calls: list[int] = []
        self.validate_calls: list[int] = []

    def next(self, exclude: set[str] | None = None, fresh_only: bool = False) -> FakeNode | None:
        for node in self.nodes:
            if exclude and node.url in exclude:
                continue
            return node
        return None

    def mark_success(self, node: FakeNode) -> None:
        self.successes.append(node)

    def mark_rate_limited(self, node: FakeNode) -> None:
        self.rate_limited.append(node)

    def mark_dead(self, node: FakeNode) -> None:
        self.dead.append(node)


class FakeTool:
    """Scripted BaseTool stand-in returning queued CrawlResults."""

    def __init__(self, results: list[CrawlResult]):
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        self.calls.append({"url": url, **kwargs})
        if self.results:
            return self.results.pop(0)
        return CrawlResult(url=url, status=CrawlStatus.ERROR, error="script exhausted")


def ok(url: str = "https://t.example/") -> CrawlResult:
    return CrawlResult(url=url, status=CrawlStatus.OK, status_code=200, markdown="m")


def blocked(code: int = 403) -> CrawlResult:
    return CrawlResult(url="https://t.example/", status=CrawlStatus.BLOCKED, status_code=code)


# ── classification ──


class TestClassify:
    def test_mapping(self):
        auth = CrawlResult(url="u", status=CrawlStatus.BLOCKED, status_code=407)
        limited = CrawlResult(url="u", status=CrawlStatus.BLOCKED, status_code=429)
        assert _classify(ok()) == "ok"
        assert _classify(auth) == "proxy_auth"
        assert _classify(limited) == "rate_limited"
        assert _classify(blocked(403)) == "blocked"
        assert _classify(CrawlResult(url="u", status=CrawlStatus.ERROR, error="x")) == "error"


# ── RotationCrawler ──


class TestRotationCrawler:
    def test_success_first_attempt(self):
        node = FakeNode()
        engine = FakeEngine([node])
        tool = FakeTool([ok()])
        crawler = RotationCrawler(engine=engine, tool=tool, auto_refill=False)
        result = crawler.crawl("https://t.example/")
        assert result.ok
        assert result.metadata["rotation"]["attempts"] == 1
        assert tool.calls[0]["proxy"] == node.proxy_url
        assert tool.calls[0]["impersonate"] == "chrome124"
        assert engine.successes == [node]
        assert crawler.stats.ok == 1

    def test_rotation_overrides_caller_proxy(self):
        node = FakeNode()
        tool = FakeTool([ok()])
        crawler = RotationCrawler(engine=FakeEngine([node]), tool=tool, auto_refill=False)
        result = crawler.crawl("https://t.example/", proxy="http://caller-proxy")
        assert result.ok
        assert tool.calls[0]["proxy"] == node.proxy_url

    def test_rotates_on_blocked_then_succeeds(self):
        n1, n2 = FakeNode("1.1.1.1:80"), FakeNode("2.2.2.2:80")
        # FakeEngine.next returns first non-excluded node
        engine = FakeEngine([n1, n2])
        tool = FakeTool([blocked(403), ok()])
        crawler = RotationCrawler(engine=engine, tool=tool, auto_refill=False)
        result = crawler.crawl("https://t.example/")
        assert result.ok
        assert result.metadata["rotation"]["attempts"] == 2
        assert tool.calls[1]["proxy"] == n2.proxy_url  # exclude forced the second node
        assert n1.marks == ["failure"]
        assert crawler.stats.distinct_proxies == 2

    def test_rate_limit_feedback(self):
        node = FakeNode()
        engine = FakeEngine([node, node])
        tool = FakeTool([CrawlResult(url="u", status=CrawlStatus.BLOCKED, status_code=429), ok()])
        crawler = RotationCrawler(engine=engine, tool=tool, auto_refill=False)
        crawler.crawl("https://t.example/")
        assert engine.rate_limited == [node]

    def test_proxy_auth_burns(self):
        node = FakeNode()
        engine = FakeEngine([node, node])
        tool = FakeTool([CrawlResult(url="u", status=CrawlStatus.BLOCKED, status_code=407), ok()])
        crawler = RotationCrawler(engine=engine, tool=tool, auto_refill=False)
        crawler.crawl("https://t.example/")
        assert engine.dead == [node]
        assert crawler.stats.burned == 1

    def test_pool_exhausted_returns_error(self):
        crawler = RotationCrawler(engine=FakeEngine([]), tool=FakeTool([]), auto_refill=False)
        result = crawler.crawl("https://t.example/")
        assert not result.ok
        assert result.status is CrawlStatus.ERROR
        assert "pool exhausted" in (result.error or "")

    def test_auto_refill_recovers_pool(self):
        # pool empty → sync refill (fake inner engine yields 1) → node available
        node = FakeNode()
        inner = FakeEngine([])

        async def fake_harvest(target_count: int = 200) -> int:
            inner.harvest_calls.append(target_count)
            return 5

        async def fake_validate(batch_size: int = 50) -> int:
            inner.validate_calls.append(batch_size)
            return 3

        inner.harvest = fake_harvest  # type: ignore[method-assign]
        inner.validate_batch = fake_validate  # type: ignore[method-assign]
        engine = FakeEngine([])
        engine._engine = inner  # duck-typed wrapper
        responses: list[FakeNode | None] = [None, node]
        engine.next = lambda exclude=None, fresh_only=False: (  # type: ignore[method-assign]
            responses.pop(0) if responses else node
        )
        tool = FakeTool([ok()])
        crawler = RotationCrawler(engine=engine, tool=tool, auto_refill=True)
        result = crawler.crawl("https://t.example/")
        assert result.ok
        assert inner.harvest_calls == [100]

    def test_attempts_validation(self):
        with pytest.raises(ValueError, match="attempts_per_url"):
            RotationCrawler(attempts_per_url=0)

    def test_crawl_many_concurrent(self):
        nodes = [FakeNode(f"1.1.1.{i}:80") for i in range(4)]
        tool = FakeTool([ok() for _ in range(4)])
        crawler = RotationCrawler(engine=FakeEngine(list(nodes)), tool=tool, auto_refill=False)
        urls = [f"https://t{i}.example/" for i in range(4)]
        results = asyncio.run(crawler.crawl_many(urls, concurrency=2))
        assert all(r.ok for r in results)
        assert len(tool.calls) == 4


# ── pool_cycles (무한의 굴레, bounded) ──


class TestPoolCycles:
    def _engine(self) -> FakeEngine:
        async def harvest(target_count: int = 200) -> int:
            return 7

        async def validate(batch_size: int = 50) -> int:
            return 5

        engine = FakeEngine([])
        engine.harvest = harvest  # type: ignore[method-assign]
        engine.validate_batch = validate  # type: ignore[method-assign]
        return engine

    def test_bounded_cycles(self):
        reports = asyncio.run(pool_cycles(self._engine(), cycles=3, interval=0))
        assert [r.cycle for r in reports] == [1, 2, 3]
        assert all(r.added == 7 and r.validated == 5 for r in reports)

    def test_missing_validation_key_degrades(self):
        async def harvest(target_count: int = 200) -> int:
            return 1

        async def validate(batch_size: int = 50) -> int:
            raise RuntimeError("no key")

        engine = self._engine()
        engine.validate_batch = validate  # type: ignore[method-assign]
        reports = asyncio.run(pool_cycles(engine, cycles=1, interval=0))
        assert reports[0].validated == 0 and reports[0].added == 7

    def test_validation(self):
        with pytest.raises(ValueError):
            asyncio.run(pool_cycles(self._engine(), cycles=-1))


# ── ProxyEngineV2 ──


@pytest.fixture()
def fresh_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Redirect both cache files and the IPv6 probe for engine tests."""
    monkeypatch.setattr(pev, "POOL_CACHE", tmp_path / "pool.json")
    monkeypatch.setattr(pev, "BURNED_CACHE", tmp_path / "burned.json")
    monkeypatch.setattr(ProxyEngineV2, "_check_ipv6", staticmethod(lambda: False))


class TestProxyEngineV2:
    def test_parse_proxies_formats(self, fresh_cache):
        engine = ProxyEngineV2()
        text = '1.2.3.4:8080\n# comment\n5.6.7.8:99999\nnotaproxy\n{"json": true}\n9.9.9.9:80\n'
        nodes = engine._parse_proxies(text, "https://raw.githubusercontent.com/x/http.txt")
        urls = [n.url for n in nodes]
        assert urls == ["1.2.3.4:8080", "9.9.9.9:80"]
        assert all(n.protocol == "http" for n in nodes)

    def test_parse_proxies_socks_protocol_from_url(self, fresh_cache):
        engine = ProxyEngineV2()
        nodes = engine._parse_proxies("1.2.3.4:1080\n", "https://x/socks5.txt")
        assert nodes[0].protocol == "socks5"

    def test_freshness_fields_persist(self, fresh_cache):
        node = ProxyNode(url="1.2.3.4:80", harvested_at=123.0, source="https://src")
        data = node.to_dict()
        assert data["harvested_at"] == 123.0 and data["source"] == "https://src"
        restored = ProxyNode.from_dict(data)
        assert restored.harvested_at == 123.0 and restored.source == "https://src"

    def test_stale_scoring(self, fresh_cache):
        import time

        fresh = ProxyNode(url="1.1.1.1:80", harvested_at=time.time())
        stale = ProxyNode(url="2.2.2.2:80", harvested_at=0)  # unknown age → stale
        assert not fresh.is_stale
        assert stale.is_stale
        assert fresh.score > stale.score

    def test_next_fresh_only_prefers_newest(self, fresh_cache):
        import time

        engine = ProxyEngineV2()
        stale = ProxyNode(url="2.2.2.2:80", harvested_at=0, uptime_pct=100)
        fresh = ProxyNode(url="1.1.1.1:80", harvested_at=time.time(), uptime_pct=100)
        engine._pool = [stale, fresh]
        pick = engine.next(fresh_only=True)
        assert pick is not None and pick.url == fresh.url
        # without fresh_only both are eligible
        picks: set[str] = set()
        for _ in range(20):
            node = engine.next()
            assert node is not None
            picks.add(node.url)
        assert picks <= {"1.1.1.1:80", "2.2.2.2:80"}

    def test_next_excludes(self, fresh_cache):
        engine = ProxyEngineV2()
        a, b = ProxyNode(url="1.1.1.1:80"), ProxyNode(url="2.2.2.2:80")
        engine._pool = [a, b]
        for _ in range(10):
            node = engine.next(exclude={"1.1.1.1:80"})
            assert node is not None and node.url == "2.2.2.2:80"

    def test_burn_removes_and_persists(self, fresh_cache, tmp_path):
        engine = ProxyEngineV2()
        node = ProxyNode(url="3.3.3.3:80")
        engine._pool = [node]
        engine.burn(node)
        assert engine._pool == []
        assert engine._hash("3.3.3.3:80") in engine._burned
        assert (tmp_path / "burned.json").exists()

    def test_ingest_refreshes_freshness_without_duplication(self, fresh_cache):
        import time

        engine = ProxyEngineV2()
        first = ProxyNode(url="4.4.4.4:80", harvested_at=1.0)
        engine._ingest([first])
        assert len(engine._pool) == 1
        time.sleep(0.01)
        again = ProxyNode(url="4.4.4.4:80", harvested_at=1.0)
        added = engine._ingest([again])
        assert added == 0  # dedup
        assert len(engine._pool) == 1
        assert engine._pool[0].harvested_at > 1.0  # freshness bumped

    def test_ingest_skips_burned(self, fresh_cache):
        engine = ProxyEngineV2()
        node = ProxyNode(url="5.5.5.5:80")
        engine.burn(node)
        assert engine._ingest([ProxyNode(url="5.5.5.5:80")]) == 0

    def test_cache_roundtrip(self, fresh_cache, tmp_path):
        engine = ProxyEngineV2()
        engine._pool = [ProxyNode(url="6.6.6.6:80", harvested_at=42.0, source="s")]
        engine._save_cache()
        engine2 = ProxyEngineV2()
        assert len(engine2._pool) == 1
        assert engine2._pool[0].url == "6.6.6.6:80"
        assert engine2._pool[0].harvested_at == 42.0


# ── proxy:// tool ──


class TestProxyPoolTool:
    def _tool(self) -> Any:
        from omk_crawl.tools.proxy_pool_tool import ProxyPoolTool

        return ProxyPoolTool()

    def test_stats_offline(self, fresh_cache):
        result = self._tool().fetch("proxy://stats")
        assert result.ok
        assert '"available"' in (result.markdown or "")

    def test_unknown_action(self, fresh_cache):
        result = self._tool().fetch("proxy://bogus")
        assert not result.ok
        assert "unknown proxy:// action" in (result.error or "")

    def test_bad_params_fail_closed(self, fresh_cache):
        result = self._tool().fetch("proxy://loop?cycles=abc")
        assert not result.ok
        assert "not an integer" in (result.error or "")

    def test_refresh_with_fake_engine(self, fresh_cache, monkeypatch):
        class FakeV2:
            def __init__(self, *a: Any, **k: Any) -> None: ...

            async def harvest(self, target_count: int = 200) -> int:
                return 12

            async def validate_batch(self, batch_size: int = 50) -> int:
                return 9

            @property
            def stats(self) -> dict[str, Any]:
                return {"available": 12}

        monkeypatch.setattr(pev, "ProxyEngineV2", FakeV2)
        result = self._tool().fetch("proxy://refresh?harvest=50")
        assert result.ok
        assert "+12 proxies" in (result.markdown or "")
        assert result.metadata["report"]["validated"] == 9

    def test_loop_bounded_with_fake_engine(self, fresh_cache, monkeypatch):
        class FakeV2:
            def __init__(self, *a: Any, **k: Any) -> None: ...

            async def harvest(self, target_count: int = 200) -> int:
                return 3

            async def validate_batch(self, batch_size: int = 50) -> int:
                return 2

            @property
            def stats(self) -> dict[str, Any]:
                return {"available": 3}

        monkeypatch.setattr(pev, "ProxyEngineV2", FakeV2)
        result = self._tool().fetch("proxy://loop?cycles=2&interval=0")
        assert result.ok
        assert "cycle 1" in (result.markdown or "") and "cycle 2" in (result.markdown or "")


# ── wiring ──


class TestWiring:
    def test_scheme_routes_to_tool(self):
        target = resolve_target("proxy://stats")
        assert target.tool == "proxy_pool"
        assert target.is_web is False

    def test_registry_entries(self):
        from omk_crawl.tools import ALL_TOOLS

        assert ALL_TOOLS["proxy_pool"].name == "proxy_pool"
        assert ALL_TOOLS["proxy"].name == "proxy_pool"

    def test_not_in_web_escalation_chain(self):
        from omk_crawl.tools import ESCALATION_CHAIN

        assert all(t.name != "proxy_pool" for t in ESCALATION_CHAIN)
