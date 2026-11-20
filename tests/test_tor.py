"""Offline tests for the Tor / .onion route and the deep+pool wiring."""

from __future__ import annotations

from typing import Any

import pytest

from omk_crawl.deep import DeepCrawlConfig, deep_crawl
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.targets import resolve_target
from omk_crawl.tools import get_tool
from omk_crawl.tools.curl_cffi_tool import CurlCffiTool
from omk_crawl.tools.tor_tool import DEFAULT_TOR_PROXY, ENV_TOR_PROXY, TorTool

ONION = "http://exampleonion1234567890abcdefghijklmnop.onion/"


class TestTargetRouting:
    def test_onion_host_routes_to_tor(self):
        target = resolve_target("http://abc.onion/p")
        assert target.tool == "tor"
        assert not target.is_web  # pinned transport like reddit: no reroute/site-memory

    def test_onion_beats_explicit_tool(self):
        # --tool curl_cffi cannot reach an onion service; tor wins.
        assert resolve_target("https://x.onion/", explicit=True).tool == "tor"

    def test_clearnet_untouched(self):
        target = resolve_target("https://x.com/")
        assert target.tool is None
        assert target.is_web

    def test_uppercase_onion(self):
        assert resolve_target("http://ABC.ONION/").tool == "tor"

    def test_registry_resolves(self):
        tool = get_tool("tor")
        assert isinstance(tool, TorTool)


def _capture_fetch(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake(self: CurlCffiTool, url: str, **kwargs: Any) -> CrawlResult:
        seen.update(kwargs)
        return CrawlResult(url=url, status=CrawlStatus.OK, html="ok", tool="tor")

    monkeypatch.setattr(CurlCffiTool, "fetch", fake)
    return seen


class TestTorTool:
    def test_injects_socks_bridge_and_longer_timeout(self, monkeypatch):
        seen = _capture_fetch(monkeypatch)
        monkeypatch.delenv(ENV_TOR_PROXY, raising=False)
        result = TorTool().fetch(ONION)
        assert result.ok
        assert seen["proxy"] == DEFAULT_TOR_PROXY
        assert seen["timeout"] == 60
        assert result.metadata["tor_proxy"] == DEFAULT_TOR_PROXY

    def test_kwarg_beats_env_beats_default(self, monkeypatch):
        seen = _capture_fetch(monkeypatch)
        monkeypatch.setenv(ENV_TOR_PROXY, "socks5h://127.0.0.1:9150")
        TorTool().fetch(ONION, tor_proxy="socks5h://10.0.0.1:9999")
        assert seen["proxy"] == "socks5h://10.0.0.1:9999"
        TorTool().fetch(ONION)
        assert seen["proxy"] == "socks5h://127.0.0.1:9150"

    def test_unreachable_bridge_gets_actionable_hint(self, monkeypatch):
        def fake(self: CurlCffiTool, url: str, **kwargs: Any) -> CrawlResult:
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                error="ProxyConnectError: connection refused",
                tool="tor",
            )

        monkeypatch.setattr(CurlCffiTool, "fetch", fake)
        monkeypatch.delenv(ENV_TOR_PROXY, raising=False)
        result = TorTool().fetch(ONION)
        assert not result.ok
        assert "tor daemon" in (result.error or "")
        assert "--tor-proxy" in (result.error or "")


class _PoolFetcher:
    """Duck-type stand-in for RotationCrawler (only .crawl is used)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def crawl(self, url: str, **kwargs: Any) -> CrawlResult:
        self.calls.append(url)
        return CrawlResult(url=url, status=CrawlStatus.OK, html="", tool="pool")


class TestDeepThroughPool:
    def test_deep_crawl_accepts_pool_fetcher(self):
        fetcher = _PoolFetcher()
        result = deep_crawl(
            "http://x.onion/",
            config=DeepCrawlConfig(max_pages=1, respect_robots=False),
            router=fetcher,
        )
        assert fetcher.calls == ["http://x.onion/"]
        assert result.stats.pages_ok == 1


class TestOnionPoolBypass:
    def test_rotation_crawler_routes_onion_to_tor(self, monkeypatch):
        from omk_crawl.rotate_crawl import RotationCrawler
        from omk_crawl.tools.tor_tool import TorTool

        calls: list[str] = []

        def fake_tor(self: TorTool, url: str, **kwargs: Any) -> CrawlResult:
            calls.append(url)
            return CrawlResult(url=url, status=CrawlStatus.OK, html="ok", tool="tor")

        monkeypatch.setattr(TorTool, "fetch", fake_tor)
        crawler = RotationCrawler(engine=_NoNodes(), tool=_OkTool2(), auto_refill=False)
        result = crawler.crawl("http://x.onion/hidden")
        assert result.ok
        assert calls == ["http://x.onion/hidden"]
        assert result.metadata["rotation"]["last_outcome"] == "tor"
        assert result.metadata["rotation"]["proxies"] == []

    def test_clearnet_still_uses_pool(self, monkeypatch):
        from omk_crawl.rotate_crawl import RotationCrawler
        from omk_crawl.tools.tor_tool import TorTool

        monkeypatch.setattr(
            TorTool, "fetch",
            lambda self, url, **kw: pytest.fail("tor must not serve clearnet"),
        )
        engine = _OneNode()
        crawler = RotationCrawler(engine=engine, tool=_OkTool2(), auto_refill=False)
        result = crawler.crawl("https://clearnet.example/")
        assert result.ok
        assert result.metadata["rotation"]["proxies"]


class _NoNodes:
    def next(self, **kw):
        return None

    def mark_success(self, node, latency_ms=0): pass
    def mark_rate_limited(self, node): pass
    def mark_dead(self, node): pass


class _OneNode:
    def __init__(self):
        from omk_crawl.tools.proxy_engine_v2 import ProxyNode
        self._node = ProxyNode(url="1.2.3.4:80")

    def next(self, **kw):
        node, self._node = self._node, None
        return node

    def mark_success(self, node, latency_ms=0): pass
    def mark_rate_limited(self, node): pass
    def mark_dead(self, node): pass


class _OkTool2:
    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        return CrawlResult(url=url, status=CrawlStatus.OK, html="ok", tool="fake")


class TestOnionRobots:
    def test_robots_fetched_over_tor(self, monkeypatch):
        from omk_crawl import detect
        from omk_crawl.tools.tor_tool import TorTool

        def fake_tor(self: TorTool, url: str, **kwargs: Any) -> CrawlResult:
            body = "User-agent: *\nDisallow: /private\n" if url.endswith("robots.txt") else ""
            return CrawlResult(url=url, status=CrawlStatus.OK, html=body, tool="tor")

        monkeypatch.setattr(TorTool, "fetch", fake_tor)
        detect._robots_for.cache_clear()
        try:
            assert detect.check_robots_txt("http://x.onion/pub/page") is True
            assert detect.check_robots_txt("http://x.onion/private/a") is False
        finally:
            detect._robots_for.cache_clear()

    def test_robots_onion_bridge_down_fails_open(self, monkeypatch):
        from omk_crawl import detect
        from omk_crawl.tools.tor_tool import TorTool

        monkeypatch.setattr(
            TorTool, "fetch",
            lambda self, url, **kw: CrawlResult(
                url=url, status=CrawlStatus.ERROR, error="tor daemon unreachable", tool="tor"
            ),
        )
        detect._robots_for.cache_clear()
        try:
            assert detect.check_robots_txt("http://x.onion/anything") is True
        finally:
            detect._robots_for.cache_clear()
