"""Tests for detection-aware routing (routing.py) and router reroute wiring."""

from __future__ import annotations

from typing import Any

from omk_crawl.detect import BlockType
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.routing import (
    CONFIDENCE_THRESHOLD,
    is_auth_block,
    preferred_order,
    reorder_tools,
)
from omk_crawl.router import SmartRouter
from omk_crawl.tools.base import BaseTool


# --- preferred_order: the routing table -------------------------------------


class TestPreferredOrder:
    def test_tls_prefers_curl_then_browsers(self):
        order = preferred_order(
            BlockType.TLS_FINGERPRINT, 0.9,
            ["curl_cffi", "crawl4ai", "scrapling", "browser_use"],
        )
        # curl_cffi stays first (try a new impersonation), browsers follow
        assert order[0] == "curl_cffi"
        assert set(order) == {"curl_cffi", "crawl4ai", "scrapling", "browser_use"}

    def test_js_prefers_renderer(self):
        order = preferred_order(
            BlockType.JS_REQUIRED, 0.8,
            ["curl_cffi", "crawl4ai", "scrapling", "browser_use"],
        )
        assert order[0] == "crawl4ai"  # cheapest JS renderer first

    def test_cloudflare_prefers_stealth(self):
        order = preferred_order(
            BlockType.CLOUDFLARE, 0.9,
            ["curl_cffi", "crawl4ai", "scrapling", "browser_use"],
        )
        assert order[0] == "scrapling"  # stealth browser first

    def test_waf_prefers_stealth(self):
        order = preferred_order(
            BlockType.WAF, 0.7,
            ["curl_cffi", "crawl4ai", "scrapling"],
        )
        assert order[0] == "scrapling"

    def test_auth_returns_empty_no_bypass(self):
        assert preferred_order(BlockType.AUTH_REQUIRED, 0.9, ["curl_cffi", "scrapling"]) == []

    def test_combined_auth_wins_over_waf(self):
        # AUTH | WAF must still refuse to escalate
        assert preferred_order(BlockType.AUTH_REQUIRED | BlockType.WAF, 0.9, ["scrapling"]) == []

    def test_low_confidence_keeps_order(self):
        avail = ["curl_cffi", "crawl4ai", "scrapling"]
        order = preferred_order(BlockType.CLOUDFLARE, CONFIDENCE_THRESHOLD - 0.1, avail)
        assert order == avail  # unchanged

    def test_no_block_keeps_order(self):
        avail = ["curl_cffi", "scrapling"]
        assert preferred_order(BlockType.NONE, 1.0, avail) == avail

    def test_is_permutation_nothing_dropped(self):
        avail = ["curl_cffi", "crawl4ai", "scrapling", "browser_use"]
        order = preferred_order(BlockType.WAF, 0.9, avail)
        assert sorted(order) == sorted(avail)

    def test_unknown_tools_preserved(self):
        # mock/unknown names keep their relative order behind preferred ones
        order = preferred_order(BlockType.WAF, 0.9, ["mock_a", "scrapling", "mock_b"])
        assert order[0] == "scrapling"
        assert order[1:] == ["mock_a", "mock_b"]

    def test_is_auth_block(self):
        assert is_auth_block(BlockType.AUTH_REQUIRED)
        assert is_auth_block(BlockType.AUTH_REQUIRED | BlockType.WAF)
        assert not is_auth_block(BlockType.WAF)


# --- reorder_tools: stable for duplicate names ------------------------------


class _Named:
    def __init__(self, name: str, idx: int) -> None:
        self.name = name
        self.idx = idx


class TestReorderTools:
    def test_follows_name_order(self):
        a, b, c = _Named("curl_cffi", 0), _Named("crawl4ai", 1), _Named("scrapling", 2)
        out = reorder_tools([a, b, c], ["scrapling", "crawl4ai", "curl_cffi"])
        assert [t.name for t in out] == ["scrapling", "crawl4ai", "curl_cffi"]

    def test_stable_for_duplicate_names(self):
        b0, b1, ok = _Named("mock_blocked", 0), _Named("mock_blocked", 1), _Named("mock_ok", 2)
        out = reorder_tools([b0, b1, ok], ["mock_blocked", "mock_blocked", "mock_ok"])
        # duplicates keep their relative order
        assert [t.idx for t in out] == [0, 1, 2]

    def test_missing_name_appended(self):
        a, b = _Named("curl_cffi", 0), _Named("extra", 1)
        out = reorder_tools([a, b], ["curl_cffi"])  # "extra" not in order
        assert [t.name for t in out] == ["curl_cffi", "extra"]


# --- Router integration: detection actually reroutes ------------------------


class _ScriptedTool(BaseTool):
    """Tool with a controllable name + canned result (for reroute tests)."""

    pip_package = ""

    def __init__(self, name: str, result: CrawlResult) -> None:
        self.name = name
        self._result = result

    def available(self) -> bool:
        return True

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        r = self._result
        r.url = url
        r.tool = self.name
        return r


def _waf(name: str) -> _ScriptedTool:
    return _ScriptedTool(name, CrawlResult(
        url="", status=CrawlStatus.BLOCKED, status_code=403,
        html="<div>Access denied — captcha required</div>", elapsed_ms=5.0,
    ))


def _ok(name: str) -> _ScriptedTool:
    return _ScriptedTool(name, CrawlResult(
        url="", status=CrawlStatus.OK, status_code=200,
        html="<h1>data</h1>", markdown="# data", elapsed_ms=5.0,
    ))


class TestRouterReroute:
    def _router(self, tools):
        r = SmartRouter()
        r._get_chain = lambda: tools  # type: ignore[method-assign]
        return r

    def test_waf_reroutes_to_stealth_before_renderer(self):
        # Chain order would try crawl4ai next, but WAF detection should
        # reroute to scrapling (stealth) first.
        router = self._router([_waf("curl_cffi"), _ok("crawl4ai"), _ok("scrapling")])
        result = router.crawl("https://example.com")
        assert result.ok
        assert result.tool == "scrapling"          # rerouted past crawl4ai
        assert len(router.history) == 2            # curl_cffi -> scrapling
        assert router.history[0].metadata.get("rerouted_to", [None])[0] == "scrapling"

    def test_no_reroute_when_detection_uncertain(self):
        # A plain 404 (no blocking markers, confidence 0) keeps chain order.
        not_found = _ScriptedTool("curl_cffi", CrawlResult(
            url="", status=CrawlStatus.ERROR, status_code=404, html="<p>nope</p>",
        ))
        router = self._router([not_found, _ok("crawl4ai")])
        result = router.crawl("https://example.com")
        # 404 is a hard client error → escalation stops, no reroute
        assert not result.ok
        assert len(router.history) == 1

    def test_auth_block_stops_without_bypass(self):
        auth = _ScriptedTool("curl_cffi", CrawlResult(
            url="", status=CrawlStatus.BLOCKED, status_code=401,
            html="login required", elapsed_ms=5.0,
        ))
        router = self._router([auth, _ok("scrapling")])
        result = router.crawl("https://example.com")
        assert not result.ok                       # did NOT escalate to bypass
        assert len(router.history) == 1
        assert router.history[0].metadata.get("auth_stop") is True
