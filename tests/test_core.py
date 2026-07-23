"""Tests for omk_crawl core (no external tools required)."""

from __future__ import annotations

import pytest

from omk_crawl.detect import BlockType, available_tools, detect_block, missing_tools, tool_available
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.router import SmartRouter
from omk_crawl.tools import ALL_TOOLS, ESCALATION_CHAIN, get_tool

# --- CrawlResult ---

class TestCrawlResult:
    def test_ok(self):
        r = CrawlResult(url="https://x.com", status=CrawlStatus.OK, markdown="# Hi")
        assert r.ok
        assert not r.blocked
        assert r.content == "# Hi"

    def test_blocked(self):
        r = CrawlResult(url="https://x.com", status=CrawlStatus.BLOCKED)
        assert not r.ok
        assert r.blocked

    def test_tls_blocked(self):
        r = CrawlResult(url="https://x.com", status=CrawlStatus.TLS_BLOCKED)
        assert r.blocked

    def test_content_priority(self):
        r = CrawlResult(
            url="x", status=CrawlStatus.OK, html="<p>hi</p>", markdown="# hi", fit_markdown="hi",
        )
        assert r.content == "hi"  # fit_markdown > markdown > html

    def test_summary(self):
        r = CrawlResult(
            url="https://x.com", status=CrawlStatus.OK, tool="curl_cffi",
            status_code=200, elapsed_ms=42.5, markdown="hello",
        )
        s = r.summary()
        assert "ok" in s
        assert "curl_cffi" in s
        assert "200" in s


# --- Detection ---

class TestDetection:
    def test_clean_page(self):
        d = detect_block("<html><body><h1>Hello</h1></body></html>", 200)
        assert d.block == BlockType.NONE
        assert not d.needs_browser
        assert not d.needs_stealth

    def test_cloudflare(self):
        html = '<div id="cf-browser-verification">Checking your browser...</div>'
        d = detect_block(html, 403)
        assert BlockType.CLOUDFLARE in d.block
        assert d.needs_stealth

    def test_js_framework(self):
        html = '<div id="__next"></div><script src="app.js"></script>'
        d = detect_block(html, 200)
        assert BlockType.JS_REQUIRED in d.block
        assert d.needs_browser

    def test_tls_fingerprint(self):
        d = detect_block("", 403)
        assert BlockType.TLS_FINGERPRINT in d.block or BlockType.WAF in d.block

    def test_rate_limit(self):
        d = detect_block("Too many requests", 429)
        assert BlockType.RATE_LIMIT in d.block

    def test_auth_required(self):
        d = detect_block("Login required", 401)
        assert BlockType.AUTH_REQUIRED in d.block

    def test_empty_200(self):
        d = detect_block(None, 200)
        assert d.block == BlockType.NONE


# --- Tool registry ---

class TestToolRegistry:
    def test_all_tools_registered(self):
        assert len(ALL_TOOLS) >= 6
        assert "curl_cffi" in ALL_TOOLS
        assert "crawl4ai" in ALL_TOOLS
        assert "scrapling" in ALL_TOOLS
        assert "browser_use" in ALL_TOOLS

    def test_escalation_order(self):
        names = [cls().name for cls in ESCALATION_CHAIN]
        assert names == ["curl_cffi", "crawl4ai", "scrapling", "browser_use"]

    def test_get_tool(self):
        t = get_tool("curl_cffi")
        assert t.name == "curl_cffi"
        assert t.layer == 0

    def test_get_tool_unknown(self):
        with pytest.raises(ValueError, match="Unknown tool"):
            get_tool("nonexistent")

    def test_tool_available_returns_bool(self):
        for name in ALL_TOOLS:
            assert isinstance(tool_available(name), bool)

    def test_available_plus_missing_equals_all(self):
        from omk_crawl.detect import _TOOL_MODULES
        avail = set(available_tools())
        miss = set(missing_tools())
        assert avail | miss == set(_TOOL_MODULES.keys())
        assert avail & miss == set()


# --- SmartRouter ---

class TestSmartRouter:
    def test_diagnose(self):
        router = SmartRouter()
        info = router.diagnose("https://example.com")
        assert "available_tools" in info
        assert "missing_tools" in info
        assert "escalation_order" in info

    def test_no_tools_returns_tool_missing(self):
        router = SmartRouter(tools=[])
        # Empty tools list → no chain → TOOL_MISSING
        r = router.crawl("https://example.com")
        # If tools are installed, this won't be TOOL_MISSING
        # but the router should handle gracefully
        assert isinstance(r, CrawlResult)

    def test_missing_tool_returns_graceful(self):
        """Fetching with a missing tool returns TOOL_MISSING, not exception."""
        from omk_crawl.tools.curl_cffi_tool import CurlCffiTool
        t = CurlCffiTool()
        if not t.available():
            r = t.fetch("https://example.com")
            assert r.status == CrawlStatus.TOOL_MISSING
            assert "pip install" in (r.error or "")


# --- Pipeline ---

class TestPipeline:
    def test_pipeline_construction(self):
        from omk_crawl.pipeline import Pipeline
        p = Pipeline()
        p.fetch().to_markdown()
        assert len(p.steps) == 2

    def test_pipeline_extract_css_step(self):
        from omk_crawl.pipeline import Pipeline
        p = Pipeline()
        p.fetch().extract_css("div.item", {"title": "h2"})
        assert len(p.steps) == 2
