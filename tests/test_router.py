"""Tests for SmartRouter escalation logic using mock tools."""

from __future__ import annotations

from typing import Any

from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.router import SmartRouter
from omk_crawl.tools.base import BaseTool

# --- Mock tools ---


class MockOKTool(BaseTool):
    """Always succeeds."""

    name = "mock_ok"
    pip_package = ""

    def available(self) -> bool:
        return True

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        return CrawlResult(
            url=url,
            status=CrawlStatus.OK,
            status_code=200,
            html="<h1>Hello</h1>",
            markdown="# Hello",
            tool=self.name,
            elapsed_ms=10.0,
        )


class MockBlockedTool(BaseTool):
    """Always blocked (WAF)."""

    name = "mock_blocked"
    pip_package = ""

    def available(self) -> bool:
        return True

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        return CrawlResult(
            url=url,
            status=CrawlStatus.BLOCKED,
            status_code=403,
            html="<div>Access denied</div>",
            tool=self.name,
            elapsed_ms=15.0,
            metadata={"detection": "WAF challenge page"},
        )


class MockTLSTool(BaseTool):
    """TLS fingerprint blocked."""

    name = "mock_tls"
    pip_package = ""

    def available(self) -> bool:
        return True

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        return CrawlResult(
            url=url,
            status=CrawlStatus.TLS_BLOCKED,
            status_code=403,
            html="",
            tool=self.name,
            elapsed_ms=5.0,
            metadata={"detection": "TLS fingerprint block"},
        )


class MockErrorTool(BaseTool):
    """Hard error (not a block)."""

    name = "mock_error"
    pip_package = ""

    def available(self) -> bool:
        return True

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        return CrawlResult(
            url=url,
            status=CrawlStatus.ERROR,
            status_code=500,
            tool=self.name,
            elapsed_ms=20.0,
            error="ConnectionError: server refused",
        )


class MockPartialTool(BaseTool):
    """Returns partial content (blocked but has some HTML)."""

    name = "mock_partial"
    pip_package = ""

    def available(self) -> bool:
        return True

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        return CrawlResult(
            url=url,
            status=CrawlStatus.BLOCKED,
            status_code=403,
            html="<html><body>partial</body></html>",
            tool=self.name,
            elapsed_ms=12.0,
        )


# --- Helper to build router with mock tools ---


def _router_with(tools: list[BaseTool], **kwargs: Any) -> SmartRouter:
    """Create a SmartRouter that uses the given mock tool instances."""
    router = SmartRouter(**kwargs)
    # Monkey-patch _get_chain to return our mocks
    router._get_chain = lambda: tools  # type: ignore[method-assign]
    return router


# --- Tests ---


class TestEscalation:
    def test_first_tool_ok_returns_immediately(self):
        router = _router_with([MockOKTool(), MockBlockedTool()])
        r = router.crawl("https://example.com")
        assert r.ok
        assert r.tool == "mock_ok"
        assert len(router.history) == 1

    def test_escalates_past_blocked(self):
        router = _router_with([MockBlockedTool(), MockOKTool()])
        r = router.crawl("https://example.com")
        assert r.ok
        assert r.tool == "mock_ok"
        assert len(router.history) == 2

    def test_escalates_past_tls_blocked(self):
        router = _router_with([MockTLSTool(), MockOKTool()])
        r = router.crawl("https://example.com")
        assert r.ok
        assert r.tool == "mock_ok"
        assert len(router.history) == 2

    def test_all_blocked_returns_best_attempt(self):
        router = _router_with([MockBlockedTool(), MockPartialTool()])
        r = router.crawl("https://example.com")
        assert not r.ok
        assert r.metadata.get("escalation_exhausted") is True
        assert r.metadata.get("attempts") == 2
        # Best attempt should be the one with more HTML
        assert r.tool == "mock_partial"

    def test_hard_error_stops_escalation(self):
        router = _router_with([MockErrorTool(), MockOKTool()])
        r = router.crawl("https://example.com")
        # Should NOT reach MockOKTool because MockErrorTool is a hard error
        assert not r.ok
        assert len(router.history) == 1
        assert r.tool == "mock_error"

    def test_max_attempts_respected(self):
        tools = [MockBlockedTool(), MockBlockedTool(), MockBlockedTool(), MockOKTool()]
        router = _router_with(tools, max_attempts=2)
        r = router.crawl("https://example.com")
        assert not r.ok
        assert len(router.history) == 2

    def test_decisions_recorded(self):
        router = _router_with([MockBlockedTool(), MockOKTool()])
        router.crawl("https://example.com")
        assert len(router.decisions) == 2
        assert router.decisions[0].tool == "mock_blocked"
        assert router.decisions[1].tool == "mock_ok"
        assert router.decisions[1].reason == "success"

    def test_detection_metadata_stored(self):
        router = _router_with([MockOKTool()])
        r = router.crawl("https://example.com")
        assert "block_type" in r.metadata
        assert "detection" in r.metadata


class TestEmptyTools:
    def test_tools_none_uses_auto_chain(self):
        """tools=None should use auto-discovery (not empty)."""
        router = SmartRouter(tools=None)
        # _get_chain should return auto-discovered tools (may be empty if nothing installed)
        chain = router._get_chain()
        assert isinstance(chain, list)

    def test_tools_empty_list_returns_empty_chain(self):
        """tools=[] should mean 'no tools', not 'all tools'."""
        router = SmartRouter(tools=[])
        chain = router._get_chain()
        assert chain == []

    def test_tools_empty_list_crawl_returns_tool_missing(self):
        """tools=[] should return TOOL_MISSING, not auto-chain."""
        router = SmartRouter(tools=[])
        r = router.crawl("https://example.com")
        assert r.status == CrawlStatus.TOOL_MISSING


class TestScore:
    def test_score_prefers_markdown(self):
        r1 = CrawlResult(url="x", status=CrawlStatus.BLOCKED, html="a" * 100)
        r2 = CrawlResult(url="x", status=CrawlStatus.BLOCKED, markdown="b" * 60)
        # markdown gets 2x weight: 60*2=120 > 100
        assert SmartRouter._score(r2) > SmartRouter._score(r1)

    def test_score_bonus_for_2xx(self):
        r1 = CrawlResult(url="x", status=CrawlStatus.BLOCKED, html="a" * 100, status_code=403)
        r2 = CrawlResult(url="x", status=CrawlStatus.BLOCKED, html="a" * 100, status_code=200)
        assert SmartRouter._score(r2) > SmartRouter._score(r1)


class TestEscalationReason:
    def test_success(self):
        r = CrawlResult(url="x", status=CrawlStatus.OK)
        assert SmartRouter._escalation_reason(r) == "success"

    def test_tls_blocked(self):
        r = CrawlResult(url="x", status=CrawlStatus.TLS_BLOCKED)
        assert "TLS" in SmartRouter._escalation_reason(r)

    def test_js_required(self):
        r = CrawlResult(url="x", status=CrawlStatus.JS_REQUIRED)
        assert "JS" in SmartRouter._escalation_reason(r)

    def test_blocked(self):
        r = CrawlResult(url="x", status=CrawlStatus.BLOCKED)
        assert "blocked" in SmartRouter._escalation_reason(r).lower()

    def test_error_with_message(self):
        r = CrawlResult(url="x", status=CrawlStatus.ERROR, error="timeout")
        assert SmartRouter._escalation_reason(r) == "timeout"


class TestDetectionRouting:
    def test_detection_to_status_404(self):
        from omk_crawl.detect import Detection, detection_to_status

        d = Detection(status_code=404)
        assert detection_to_status(d) == CrawlStatus.ERROR

    def test_detection_to_status_500(self):
        from omk_crawl.detect import Detection, detection_to_status

        d = Detection(status_code=500)
        assert detection_to_status(d) == CrawlStatus.ERROR

    def test_detection_to_status_503_plain(self):
        """503 with no blocking markers is a server error."""
        from omk_crawl.detect import Detection, detection_to_status

        d = Detection(status_code=503)
        assert detection_to_status(d) == CrawlStatus.ERROR

    def test_detection_to_status_503_cloudflare(self):
        """CF 503 challenge must be BLOCKED, not ERROR — escalation must continue."""
        from omk_crawl.detect import BlockType, Detection, detection_to_status

        d = Detection(
            status_code=503,
            block=BlockType.CLOUDFLARE,
            needs_stealth=True,
            confidence=0.9,
            detail="Cloudflare challenge detected",
        )
        assert detection_to_status(d) == CrawlStatus.BLOCKED

    def test_detection_to_status_403_waf(self):
        """403 with WAF markers must be BLOCKED, not ERROR."""
        from omk_crawl.detect import BlockType, Detection, detection_to_status

        d = Detection(status_code=403, block=BlockType.WAF)
        assert detection_to_status(d) == CrawlStatus.BLOCKED

    def test_detection_to_status_200_clean(self):
        from omk_crawl.detect import Detection, detection_to_status

        d = Detection(status_code=200)
        assert detection_to_status(d) == CrawlStatus.OK

    def test_detection_to_status_403_tls(self):
        from omk_crawl.detect import BlockType, Detection, detection_to_status

        d = Detection(status_code=403, block=BlockType.TLS_FINGERPRINT)
        assert detection_to_status(d) == CrawlStatus.TLS_BLOCKED

    def test_detection_to_status_429(self):
        from omk_crawl.detect import BlockType, Detection, detection_to_status

        d = Detection(status_code=429, block=BlockType.RATE_LIMIT)
        assert detection_to_status(d) == CrawlStatus.BLOCKED

    def test_detection_to_status_401(self):
        from omk_crawl.detect import BlockType, Detection, detection_to_status

        d = Detection(status_code=401, block=BlockType.AUTH_REQUIRED)
        assert detection_to_status(d) == CrawlStatus.BLOCKED

    def test_detect_block_cf_503_integration(self):
        """End-to-end: detect_block on CF 503 HTML → detection_to_status → BLOCKED."""
        from omk_crawl.detect import detect_block, detection_to_status

        html = '<div id="cf-browser-verification">Checking your browser...</div>'
        det = detect_block(html, 503)
        assert detection_to_status(det) == CrawlStatus.BLOCKED


class TestEnsureMarkdown:
    def test_strips_script_and_style(self):
        r = CrawlResult(
            url="x",
            status=CrawlStatus.OK,
            html=(
                "<style>body{color:red}</style>"
                "<script>var x=1;alert('x')</script>"
                "<p>Hello &amp; bye</p>"
            ),
        )
        SmartRouter._ensure_markdown(r)
        assert r.markdown is not None
        assert "color:red" not in r.markdown
        assert "alert" not in r.markdown
        assert "Hello & bye" in r.markdown

    def test_does_not_overwrite_existing_markdown(self):
        r = CrawlResult(url="x", status=CrawlStatus.OK, html="<p>hi</p>", markdown="# hi")
        SmartRouter._ensure_markdown(r)
        assert r.markdown == "# hi"

    def test_empty_html_leaves_markdown_none(self):
        r = CrawlResult(url="x", status=CrawlStatus.OK, html="")
        SmartRouter._ensure_markdown(r)
        assert r.markdown is None

    def test_only_script_leaves_markdown_none(self):
        """If HTML is only script/style, markdown stays None (not garbage)."""
        r = CrawlResult(
            url="x", status=CrawlStatus.OK,
            html="<script>var x=1;</script><style>.a{}</style>",
        )
        SmartRouter._ensure_markdown(r)
        assert r.markdown is None


class TestBogusTool:
    def test_unknown_tool_returns_tool_missing(self):
        """--tool bogus should not crash with ValueError."""
        router = SmartRouter(tools=["bogus_tool"])
        r = router.crawl("https://example.com")
        assert r.status == CrawlStatus.TOOL_MISSING
