"""Tests for the v2.12 breakthrough layer (offline, deterministic).

Covers: fingerprint coherence (fingerprint.py), seeded behavior (behavior.py),
session warm-up & clearance reuse (warmup.py), new WAF detection, extended
routing tables, and the three new stealth-browser adapters.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from omk_crawl.behavior import BehaviorClock
from omk_crawl.detect import BlockType, detect_block
from omk_crawl.fingerprint import (
    PROFILES,
    FingerprintProfile,
    coherence_issues,
    match_impersonate,
    profile_for,
)
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.routing import DEFAULT_ORDER, ROUTE_TABLE, preferred_order
from omk_crawl.tools import ALL_TOOLS, ESCALATION_CHAIN, get_tool
from omk_crawl.warmup import CLEARANCE_COOKIES, SessionWarmup, WarmSession, warm_crawl

# ── Fingerprint coherence ────────────────────────────────────────────────


class TestFingerprintProfiles:
    def test_builtin_profiles_are_self_coherent(self):
        """Every built-in profile's own header set passes the audit."""
        for profile in PROFILES:
            assert profile.coherence_issues() == [], profile.name

    def test_chrome_headers_carry_client_hints(self):
        profile = next(p for p in PROFILES if p.family == "chrome")
        headers = profile.headers()
        assert headers["Sec-Ch-Ua"]
        assert headers["Sec-Ch-Ua-Platform"]
        assert headers["Sec-Fetch-Mode"] == "navigate"

    def test_firefox_and_safari_send_no_client_hints(self):
        for profile in PROFILES:
            if profile.family in ("firefox", "safari"):
                assert "Sec-Ch-Ua" not in profile.headers(), profile.name

    def test_coherence_audit_catches_firefox_with_client_hints(self):
        issues = coherence_issues(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) "
                "Gecko/20100101 Firefox/133.0",
                "Sec-Ch-Ua": '"Chromium";v="124"',
            }
        )
        assert any("Firefox" in issue for issue in issues)

    def test_coherence_audit_catches_platform_mismatch(self):
        issues = coherence_issues(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
                "Sec-Ch-Ua-Platform": '"macOS"',
            }
        )
        assert any("platform" in issue.lower() for issue in issues)

    def test_coherence_audit_catches_version_mismatch(self):
        issues = coherence_issues(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
            }
        )
        assert any("120" in issue and "124" in issue for issue in issues)

    def test_coherence_audit_catches_impersonate_mismatch(self):
        issues = coherence_issues({"User-Agent": PROFILES[0].user_agent}, impersonate="firefox133")
        assert any("impersonate" in issue for issue in issues)

    def test_profile_for_is_deterministic_per_site(self):
        a = profile_for("https://example.com/page")
        b = profile_for("https://example.com/other")
        assert a is b  # same site → same identity (over-time consistency)

    def test_profile_for_varies_with_salt(self):
        salts = {profile_for("https://example.com", salt=str(i)).name for i in range(20)}
        assert len(salts) > 1

    def test_match_impersonate_family_and_version(self):
        p = match_impersonate("chrome124")
        assert p.family == "chrome"
        assert p.ua_major_version == 124
        assert match_impersonate("firefox133").family == "firefox"
        assert match_impersonate("safari17_0").family == "safari"

    def test_match_impersonate_unknown_family_falls_back(self):
        assert match_impersonate("edge101").name == PROFILES[0].name

    def test_curl_kwargs_requires_impersonation_target(self):
        bare = FingerprintProfile(name="browser-only", impersonate="", user_agent="x")
        with pytest.raises(ValueError, match="no TLS impersonation"):
            bare.curl_kwargs()

    def test_browser_context_kwargs(self):
        ctx = PROFILES[0].browser_context_kwargs()
        assert ctx["user_agent"] == PROFILES[0].user_agent
        assert ctx["viewport"]["width"] == PROFILES[0].viewport[0]


# ── Behavior ─────────────────────────────────────────────────────────────


class TestBehaviorClock:
    def test_deterministic_given_same_seed(self):
        a = BehaviorClock("example.com|s1")
        b = BehaviorClock("example.com|s1")
        assert [a.think_time() for _ in range(5)] == [b.think_time() for _ in range(5)]

    def test_different_seeds_diverge(self):
        a = BehaviorClock("seed-a")
        b = BehaviorClock("seed-b")
        assert [a.think_time() for _ in range(5)] != [b.think_time() for _ in range(5)]

    def test_think_time_within_bounds(self):
        clock = BehaviorClock(42)
        for _ in range(200):
            assert 0.4 <= clock.think_time() <= 4.0

    def test_dwell_time_grows_with_content(self):
        clock = BehaviorClock(42)
        short = sum(clock.dwell_time(100) for _ in range(20))
        clock2 = BehaviorClock(42)
        long = sum(clock2.dwell_time(9000) for _ in range(20))
        assert long > short

    def test_inter_request_delay_bounds(self):
        clock = BehaviorClock(7)
        for _ in range(200):
            assert 0.5 <= clock.inter_request_delay(1.0) <= 8.0

    def test_mouse_path_endpoints_and_shape(self):
        clock = BehaviorClock("mouse")
        path = clock.mouse_path((0, 0), (400, 300))
        assert list(path[0]) == [0, 0]
        assert list(path[-1]) == [400, 300]
        assert 8 <= len(path) <= 48
        # Not a perfectly straight line: some interior point deviates.
        xs = [p[0] for p in path]
        assert len(set(xs)) > len(path) // 2

    def test_scroll_plan_covers_page(self):
        clock = BehaviorClock("scroll")
        stops = clock.scroll_plan(page_height=8000, viewport_height=1000)
        assert stops[-1] == 7000
        assert all(0 < s <= 7000 for s in stops)
        assert stops == sorted(stops)

    def test_scroll_plan_short_page_is_empty(self):
        assert BehaviorClock("x").scroll_plan(900, 1000) == []


# ── Warm sessions ────────────────────────────────────────────────────────


def _make_session(tmp_path, ttl=1800.0, cookies=None) -> WarmSession:
    return WarmSession(
        domain="example.com",
        cookies=cookies if cookies is not None else {"cf_clearance": "tok", "x": "y"},
        profile=PROFILES[0],
        acquired_at=time.time(),
        tool="test",
        ttl=ttl,
    )


class TestWarmSession:
    def test_has_clearance(self, tmp_path):
        assert _make_session(tmp_path).has_clearance
        assert not _make_session(tmp_path, cookies={"session": "abc"}).has_clearance

    def test_expired(self, tmp_path):
        session = _make_session(tmp_path, ttl=-1.0)
        assert session.expired()
        assert not _make_session(tmp_path).expired()

    def test_curl_kwargs_replays_coherently(self, tmp_path):
        session = _make_session(tmp_path)
        kwargs = session.curl_kwargs()
        assert kwargs["impersonate"] == session.profile.impersonate
        assert kwargs["cookies"]["cf_clearance"] == "tok"
        # Replay headers must pass the same coherence audit.
        assert coherence_issues(kwargs["headers"], impersonate=kwargs["impersonate"]) == []

    def test_serialization_round_trip(self, tmp_path):
        session = _make_session(tmp_path)
        restored = WarmSession.from_dict(session.to_dict())
        assert restored == session

    def test_from_dict_rejects_malformed(self):
        assert WarmSession.from_dict({"domain": "x"}) is None
        assert (
            WarmSession.from_dict(
                {"domain": "x", "cookies": {}, "profile": "nope", "acquired_at": 0.0, "tool": "t"}
            )
            is None
        )

    def test_clearance_registry_covers_major_vendors(self):
        for name in ("cf_clearance", "datadome", "_abck", "aws-waf-token", "x-kpsdk-ct"):
            assert name in CLEARANCE_COOKIES


class TestSessionWarmup:
    def test_record_get_invalidate(self, tmp_path):
        manager = SessionWarmup(path=tmp_path / "warm.json")
        manager.record("https://example.com/", {"cf_clearance": "tok"}, tool="test")
        session = manager.get("https://example.com/some/page")
        assert session is not None
        assert session.has_clearance
        manager.invalidate("https://example.com/")
        assert manager.get("https://example.com/") is None

    def test_expired_sessions_are_dropped(self, tmp_path):
        manager = SessionWarmup(path=tmp_path / "warm.json", ttl=-1.0)
        manager.record("https://example.com/", {"cf_clearance": "tok"}, ttl=-1.0)
        assert manager.get("https://example.com/") is None

    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "warm.json"
        SessionWarmup(path=path).record(
            "https://example.com/", {"cf_clearance": "tok"}, tool="test"
        )
        reloaded = SessionWarmup(path=path)
        session = reloaded.get("https://example.com/")
        assert session is not None
        assert session.tool == "test"

    def test_acquire_fail_closed_when_no_driver(self, tmp_path, monkeypatch):
        manager = SessionWarmup(path=tmp_path / "warm.json")

        def _unavailable(*args, **kwargs):
            raise ImportError("no browser")

        monkeypatch.setattr(manager, "_acquire_nodriver", _unavailable)
        monkeypatch.setattr(manager, "_acquire_patchright", _unavailable)
        monkeypatch.setattr(manager, "_acquire_camoufox", _unavailable)
        with pytest.raises(RuntimeError, match="warmup failed"):
            manager.acquire("https://example.com/")


class TestWarmCrawl:
    def test_replay_success(self, tmp_path, monkeypatch):
        manager = SessionWarmup(path=tmp_path / "warm.json")
        manager.record("https://example.com/", {"cf_clearance": "tok"}, tool="test")

        class _Response:
            status_code = 200
            text = "<html><body><h1>real content here, not a challenge</h1></body></html>"

        from curl_cffi import requests as cffi

        seen = {}

        def fake_get(url, **kwargs):
            seen.update(kwargs)
            return _Response()

        monkeypatch.setattr(cffi, "get", fake_get)
        result = warm_crawl("https://example.com/article", warmup=manager)
        assert result.ok
        assert result.metadata["strategy"] == "session_replay"
        assert seen["cookies"]["cf_clearance"] == "tok"
        assert seen["impersonate"]

    def test_replay_failure_falls_back_to_router(self, tmp_path, monkeypatch):
        manager = SessionWarmup(path=tmp_path / "warm.json")
        manager.record("https://example.com/", {"cf_clearance": "tok"}, tool="test")

        from curl_cffi import requests as cffi

        def failing_get(url, **kwargs):
            raise ConnectionError("network down")

        monkeypatch.setattr(cffi, "get", failing_get)

        sentinel = CrawlResult(url="https://example.com/", status=CrawlStatus.BLOCKED)

        def fake_crawl(self, url, **kwargs):
            return sentinel

        monkeypatch.setattr("omk_crawl.router.SmartRouter.crawl", fake_crawl)
        result = warm_crawl("https://example.com/", warmup=manager)
        assert result is sentinel
        assert result.metadata["warmup"] == "replay_blocked_fell_back"
        # Session was invalidated after the failed replay.
        assert manager.get("https://example.com/") is None


# ── Detection: new WAF vendors ───────────────────────────────────────────


class TestNewWafDetection:
    def test_kasada(self):
        d = detect_block('<script src="/ips.js?x-kpsdk-v=j"></script>', 429 - 26)
        assert BlockType.KASADA in d.block
        assert d.needs_stealth

    def test_perimeterx(self):
        d = detect_block('<div id="px-captcha">human check</div>', 403)
        assert BlockType.PERIMETERX in d.block

    def test_aws_waf(self):
        d = detect_block("<script>window.awsWafCookieDomainList; aws-waf-token</script>", 405)
        assert BlockType.AWS_WAF in d.block

    def test_clean_page_unaffected(self):
        d = detect_block("<html><body>normal site</body></html>", 200)
        assert BlockType.KASADA not in d.block
        assert BlockType.PERIMETERX not in d.block
        assert BlockType.AWS_WAF not in d.block


# ── Routing tables ───────────────────────────────────────────────────────


class TestRoutingTables:
    def test_default_order_tools_registered(self):
        assert set(DEFAULT_ORDER) <= set(ALL_TOOLS)

    def test_route_table_tools_registered(self):
        for block, tools in ROUTE_TABLE.items():
            assert set(tools) <= set(ALL_TOOLS), block

    def test_kasada_frontloads_cdp_native(self):
        order = preferred_order(BlockType.KASADA, 0.9, list(DEFAULT_ORDER), DEFAULT_ORDER)
        assert order[0] == "nodriver"

    def test_cloudflare_frontloads_breaker(self):
        order = preferred_order(BlockType.CLOUDFLARE, 0.9, list(DEFAULT_ORDER), DEFAULT_ORDER)
        assert order[0] == "insane_search"

    def test_combined_block_resolves_by_priority(self):
        combined = BlockType.KASADA | BlockType.PERIMETERX
        order = preferred_order(combined, 0.9, list(DEFAULT_ORDER), DEFAULT_ORDER)
        assert order[0] == "nodriver"  # KASADA priority over PERIMETERX, both CDP-first

    def test_auth_never_escalates(self):
        assert (
            preferred_order(BlockType.AUTH_REQUIRED, 1.0, list(DEFAULT_ORDER), DEFAULT_ORDER) == []
        )


# ── New adapters ─────────────────────────────────────────────────────────


class TestBreakthroughAdapters:
    def test_registered_and_in_chain(self):
        for name in ("camoufox", "nodriver", "patchright"):
            assert name in ALL_TOOLS
        names = [cls().name for cls in ESCALATION_CHAIN]
        assert names == [
            "insane_search",
            "curl_cffi",
            "crawl4ai",
            "scrapling",
            "camoufox",
            "patchright",
            "nodriver",
            "browser_use",
        ]

    def test_browser_layer_contract(self):
        for name in ("camoufox", "nodriver", "patchright"):
            tool = get_tool(name)
            assert tool.layer == 2
            assert tool.needs_browser
            assert {"timeout", "proxy"} <= tool.capabilities

    def test_missing_tool_fails_closed(self, monkeypatch):
        from omk_crawl.tools.camoufox_tool import CamoufoxTool

        monkeypatch.setattr(CamoufoxTool, "available", lambda self: False)
        result = CamoufoxTool().fetch("https://example.com")
        assert result.status is CrawlStatus.TOOL_MISSING
        assert "pip install" in (result.error or "")

    def test_nodriver_sync_fetch_rejects_running_loop(self):
        from omk_crawl.tools.nodriver_tool import NodriverTool

        async def _inside_loop():
            return NodriverTool().fetch("https://example.com")

        result = asyncio.run(_inside_loop())
        assert result.status is CrawlStatus.ERROR
        assert "async-native" in (result.error or "")
