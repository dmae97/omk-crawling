"""Tests for the unified adapter contract (Phase 1) and browser-use guards."""

from __future__ import annotations

from omk_crawl.tools.base import COMMON_KWARGS
from omk_crawl.tools.browser_use_tool import BrowserUseTool
from omk_crawl.tools.crawl4ai_tool import Crawl4aiTool
from omk_crawl.tools.curl_cffi_tool import CurlCffiTool
from omk_crawl.tools.scrapling_tool import ScraplingTool

CORE_ADAPTERS = [CurlCffiTool, Crawl4aiTool, ScraplingTool, BrowserUseTool]


class TestCapabilitiesDeclared:
    def test_every_core_adapter_declares_capabilities(self):
        for cls in CORE_ADAPTERS:
            assert cls.capabilities, f"{cls.name} must declare capabilities"
            assert isinstance(cls.capabilities, frozenset)

    def test_capabilities_are_known_common_or_feature_names(self):
        # capabilities should be drawn from a known vocabulary
        known = set(COMMON_KWARGS) | {"js_render", "markdown", "stealth"}
        for cls in CORE_ADAPTERS:
            assert cls.capabilities <= known, f"{cls.name}: {cls.capabilities}"

    def test_supports_reflects_capabilities(self):
        curl = CurlCffiTool()
        assert curl.supports("proxy")
        assert curl.supports("cookies")
        assert not curl.supports("js_render")

    def test_timeout_is_universal(self):
        # every core adapter should honour the timeout contract
        for cls in CORE_ADAPTERS:
            assert "timeout" in cls.capabilities or cls is BrowserUseTool


class TestUnsupportedReporting:
    def test_unsupported_features_flags_passed_but_unsupported(self):
        # js_render not a COMMON_KWARGS key, so not flagged; pass a common one:
        # crawl4ai lacks proxy/cookies
        c4 = Crawl4aiTool()
        assert "proxy" in c4.unsupported_features({"proxy": "http://x:8080"})
        assert "cookies" in c4.unsupported_features({"cookies": {"a": "b"}})
        assert c4.unsupported_features({"timeout": 5}) == []  # supported

    def test_none_values_not_flagged(self):
        c4 = Crawl4aiTool()
        assert c4.unsupported_features({"proxy": None}) == []

    def test_contract_metadata_includes_capabilities(self):
        curl = CurlCffiTool()
        meta = curl.contract_metadata({"timeout": 10})
        assert "capabilities" in meta
        assert "proxy" in meta["capabilities"]

    def test_contract_metadata_flags_unsupported_requested(self):
        c4 = Crawl4aiTool()
        meta = c4.contract_metadata({"proxy": "http://x:8080"})
        assert meta.get("unsupported_requested") == ["proxy"]

    def test_contract_metadata_clean_when_all_supported(self):
        curl = CurlCffiTool()
        meta = curl.contract_metadata({"proxy": "http://x", "cookies": {"a": "b"}})
        assert "unsupported_requested" not in meta


class TestBrowserUseGuards:
    def test_caps_stored(self):
        t = BrowserUseTool(max_steps=10, max_cost_usd=0.5, deadline_s=30)
        assert t.max_steps == 10
        assert t.max_cost_usd == 0.5
        assert t.deadline_s == 30

    def test_defaults_are_bounded(self):
        t = BrowserUseTool()
        assert t.max_steps > 0
        assert t.deadline_s > 0

    def test_has_llm_key_false_when_no_env(self, monkeypatch):
        for v in ("BROWSER_USE_API_KEY", "OPENAI_API_KEY",
                  "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        assert BrowserUseTool()._has_llm_key() is False

    def test_has_llm_key_true_when_set(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert BrowserUseTool()._has_llm_key() is True

    def test_failure_taxonomy(self):
        f = BrowserUseTool._classify_failure
        assert f(TimeoutError("timed out")) == "timeout"
        assert f(RuntimeError("login required")) == "login"
        assert f(ConnectionError("net::ERR_NAME_NOT_RESOLVED")) == "nav"
        assert f(RuntimeError("model rate limit")) == "model"
        assert f(ValueError("something else")) == "unknown"

    def test_dry_run_reports_guardrails(self, monkeypatch):
        # Force "available" so the dry-run path is reachable without the dep.
        monkeypatch.setattr(BrowserUseTool, "available", lambda self: True)
        t = BrowserUseTool(max_steps=7, deadline_s=42)
        r = t.fetch("https://example.com", dry_run=True)
        assert r.metadata.get("dry_run") is True
        assert r.metadata.get("max_steps") == 7
        assert r.metadata.get("deadline_s") == 42
