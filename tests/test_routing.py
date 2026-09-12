"""Tests for detection-aware routing (routing.py) and router reroute wiring."""

from __future__ import annotations

import asyncio
from typing import Any

from omk_crawl.detect import BlockType
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.router import SmartRouter
from omk_crawl.routing import (
    CONFIDENCE_THRESHOLD,
    is_auth_block,
    preferred_order,
    reorder_tools,
)
from omk_crawl.tools.base import BaseTool

# --- preferred_order: the routing table -------------------------------------


class TestPreferredOrder:
    def test_tls_prefers_curl_then_browsers(self):
        order = preferred_order(
            BlockType.TLS_FINGERPRINT,
            0.9,
            ["curl_cffi", "crawl4ai", "scrapling", "browser_use"],
        )
        # curl_cffi stays first (try a new impersonation), browsers follow
        assert order[0] == "curl_cffi"
        assert set(order) == {"curl_cffi", "crawl4ai", "scrapling", "browser_use"}

    def test_js_prefers_renderer(self):
        order = preferred_order(
            BlockType.JS_REQUIRED,
            0.8,
            ["curl_cffi", "crawl4ai", "scrapling", "browser_use"],
        )
        assert order[0] == "crawl4ai"  # cheapest JS renderer first

    def test_cloudflare_prefers_stealth(self):
        order = preferred_order(
            BlockType.CLOUDFLARE,
            0.9,
            ["curl_cffi", "crawl4ai", "scrapling", "browser_use"],
        )
        assert order[0] == "scrapling"  # stealth browser first

    def test_waf_prefers_stealth(self):
        order = preferred_order(
            BlockType.WAF,
            0.7,
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


class _RecordingMemory:
    def __init__(self) -> None:
        self.outcomes: dict[str, bool] = {}

    def order(self, url: str, tools: list[str]) -> list[str]:
        return tools

    def record(self, url: str, tool: str, success: bool, latency_ms: float) -> None:
        self.outcomes[tool] = success


def _waf(name: str) -> _ScriptedTool:
    return _ScriptedTool(
        name,
        CrawlResult(
            url="",
            status=CrawlStatus.BLOCKED,
            status_code=403,
            html="<div>Access denied — captcha required</div>",
            elapsed_ms=5.0,
        ),
    )


def _ok(name: str) -> _ScriptedTool:
    return _ScriptedTool(
        name,
        CrawlResult(
            url="",
            status=CrawlStatus.OK,
            status_code=200,
            html="<h1>data</h1>",
            markdown="# data",
            elapsed_ms=5.0,
        ),
    )


class TestRouterReroute:
    def _router(self, tools):
        r = SmartRouter(learn_sites=False)
        r._get_chain = lambda: tools  # type: ignore[method-assign]
        return r

    def test_explicit_tool_order_is_not_rerouted(self, monkeypatch):
        import omk_crawl.router as router_module

        tools = {
            "curl_cffi": _waf("curl_cffi"),
            "crawl4ai": _ok("crawl4ai"),
            "scrapling": _ok("scrapling"),
        }
        monkeypatch.setattr(router_module, "get_tool", tools.__getitem__)
        router = SmartRouter(
            tools=["curl_cffi", "crawl4ai", "scrapling"],
            learn_sites=False,
            respect_robots=False,
            min_delay=0,
        )

        result = router.crawl("https://example.com")

        assert result.tool == "crawl4ai"
        assert [attempt.tool for attempt in router.history] == ["curl_cffi", "crawl4ai"]

    def test_successful_http_challenge_escalates(self):
        challenge = _ScriptedTool(
            "browser_use",
            CrawlResult(
                url="",
                status=CrawlStatus.OK,
                status_code=200,
                markdown="Checking your browser. Please verify you are human.",
            ),
        )
        router = self._router([challenge, _ok("crawl4ai")])

        result = router.crawl("https://example.com/profile")

        assert result.tool == "crawl4ai"
        assert router.history[0].status is CrawlStatus.BLOCKED
        assert router.history[0].metadata["block_type"] == "WAF"

    def test_successful_access_denied_challenge_escalates(self):
        challenge = _ScriptedTool(
            "browser_use",
            CrawlResult(
                url="",
                status=CrawlStatus.OK,
                status_code=200,
                html="<title>Access Denied</title><p>Please complete the CAPTCHA to continue.</p>",
            ),
        )
        router = self._router([challenge, _ok("crawl4ai")])

        result = router.crawl("https://example.com/profile")

        assert result.tool == "crawl4ai"
        assert router.history[0].status is CrawlStatus.BLOCKED

    def test_large_script_shell_escalates_and_records_failure(self):
        shell = _ScriptedTool(
            "curl_cffi",
            CrawlResult(
                url="",
                status=CrawlStatus.OK,
                status_code=200,
                html='<div id="root"></div><script>' + "window.x=1;" * 1000 + "</script>",
            ),
        )
        memory = _RecordingMemory()
        router = self._router([shell, _ok("crawl4ai")])
        router.learn_sites = True
        router.site_memory = memory

        result = router.crawl("https://example.com/profile")

        assert result.tool == "crawl4ai"
        assert router.history[0].status is CrawlStatus.JS_REQUIRED
        assert memory.outcomes == {"curl_cffi": False, "crawl4ai": True}

    def test_empty_success_escalates(self):
        empty = _ScriptedTool(
            "curl_cffi",
            CrawlResult(url="", status=CrawlStatus.OK, status_code=200),
        )
        router = self._router([empty, _ok("crawl4ai")])

        result = router.crawl("https://example.com/profile")

        assert result.tool == "crawl4ai"
        assert router.history[0].status is CrawlStatus.JS_REQUIRED

    def test_async_empty_success_escalates(self):
        empty = _ScriptedTool(
            "curl_cffi",
            CrawlResult(url="", status=CrawlStatus.OK, status_code=200),
        )
        router = self._router([empty, _ok("crawl4ai")])

        result = asyncio.run(router.crawl_async("https://example.com/profile"))

        assert result.tool == "crawl4ai"
        assert router.history[0].status is CrawlStatus.JS_REQUIRED

    def test_script_only_page_without_known_root_escalates(self):
        shell = _ScriptedTool(
            "curl_cffi",
            CrawlResult(
                url="",
                status=CrawlStatus.OK,
                status_code=200,
                html="<html><body><script>" + "window.data={};" * 100 + "</script></body></html>",
            ),
        )
        router = self._router([shell, _ok("crawl4ai")])

        result = router.crawl("https://example.com/profile")

        assert result.tool == "crawl4ai"
        assert router.history[0].status is CrawlStatus.JS_REQUIRED

    def test_login_navigation_does_not_hide_public_content(self):
        public = _ScriptedTool(
            "curl_cffi",
            CrawlResult(
                url="",
                status=CrawlStatus.OK,
                status_code=200,
                markdown=(
                    "Log in · Sign up\n\n"
                    "# Public API reference\n"
                    "Use this guide to build clients, inspect resources, and handle errors."
                ),
            ),
        )
        router = self._router([public, _ok("crawl4ai")])

        result = router.crawl("https://example.com/docs")

        assert result.ok
        assert result.tool == "curl_cffi"
        assert len(router.history) == 1

    def test_password_modal_does_not_hide_public_content(self):
        public = _ScriptedTool(
            "curl_cffi",
            CrawlResult(
                url="",
                status=CrawlStatus.OK,
                status_code=200,
                html=(
                    "<main><article>Public documentation explains resources, clients, schemas, "
                    "examples, limits, errors, retries, pagination, filters, sorting, caching, "
                    "security, deployment, monitoring, and support.</article></main>"
                    '<form hidden><input type="password">Log in</form>'
                ),
            ),
        )
        router = self._router([public, _ok("crawl4ai")])

        result = router.crawl("https://example.com/docs")

        assert result.ok
        assert result.tool == "curl_cffi"

    def test_detector_keywords_do_not_override_usable_content(self):
        for html in (
            "<article>Cloudflare integration guide with public configuration examples, "
            "deployment steps, cache rules, headers, monitoring, troubleshooting, and support "
            "information for production clients.</article>",
            '<div id="root"><article>Rendered public profile with posts, biography, links, '
            "followers, projects, activity, contact information, recent updates, documentation, "
            "examples, and community resources.</article></div>",
        ):
            router = self._router(
                [
                    _ScriptedTool(
                        "curl_cffi",
                        CrawlResult(url="", status=CrawlStatus.OK, status_code=200, html=html),
                    ),
                    _ok("crawl4ai"),
                ]
            )

            result = router.crawl("https://example.com/docs")

            assert result.ok
            assert result.tool == "curl_cffi"

    def test_password_login_wall_stops_without_bypass(self):
        login = _ScriptedTool(
            "crawl4ai",
            CrawlResult(
                url="",
                status=CrawlStatus.OK,
                status_code=200,
                html='<form action="/login"><input type="password">Log in</form>',
            ),
        )
        router = self._router([login, _ok("scrapling")])

        result = router.crawl("https://example.com/profile")

        assert not result.ok
        assert len(router.history) == 1
        assert result.metadata.get("auth_stop")

    def test_single_marker_auth_wall_stops_without_bypass(self):
        login = _ScriptedTool(
            "browser_use",
            CrawlResult(
                url="",
                status=CrawlStatus.OK,
                status_code=200,
                markdown="You must sign in to view this page.",
            ),
        )
        router = self._router([login, _ok("scrapling")])

        result = router.crawl("https://example.com/profile")

        assert not result.ok
        assert len(router.history) == 1
        assert result.metadata.get("auth_stop")

    def test_markdown_login_wall_stops_without_bypass(self):
        login = _ScriptedTool(
            "browser_use",
            CrawlResult(
                url="",
                status=CrawlStatus.OK,
                status_code=200,
                markdown="Log in to continue. Sign up or create an account.",
            ),
        )
        router = self._router([login, _ok("scrapling")])

        result = router.crawl("https://example.com/profile")

        assert not result.ok
        assert len(router.history) == 1
        assert result.metadata.get("auth_stop")

    def test_waf_reroutes_to_stealth_before_renderer(self):
        # Chain order would try crawl4ai next, but WAF detection should
        # reroute to scrapling (stealth) first.
        router = self._router([_waf("curl_cffi"), _ok("crawl4ai"), _ok("scrapling")])
        result = router.crawl("https://example.com")
        assert result.ok
        assert result.tool == "scrapling"  # rerouted past crawl4ai
        assert len(router.history) == 2  # curl_cffi -> scrapling
        assert router.history[0].metadata.get("rerouted_to", [None])[0] == "scrapling"

    def test_no_reroute_when_detection_uncertain(self):
        # A plain 404 (no blocking markers, confidence 0) keeps chain order.
        not_found = _ScriptedTool(
            "curl_cffi",
            CrawlResult(
                url="",
                status=CrawlStatus.ERROR,
                status_code=404,
                html="<p>nope</p>",
            ),
        )
        router = self._router([not_found, _ok("crawl4ai")])
        result = router.crawl("https://example.com")
        # 404 is a hard client error → escalation stops, no reroute
        assert not result.ok
        assert len(router.history) == 1

    def test_auth_block_stops_without_bypass(self):
        auth = _ScriptedTool(
            "curl_cffi",
            CrawlResult(
                url="",
                status=CrawlStatus.BLOCKED,
                status_code=401,
                html="login required",
                elapsed_ms=5.0,
            ),
        )
        router = self._router([auth, _ok("scrapling")])
        result = router.crawl("https://example.com")
        assert not result.ok  # did NOT escalate to bypass
        assert len(router.history) == 1
        assert router.history[0].metadata.get("auth_stop")
