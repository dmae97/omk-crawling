"""One target resolver for CLI, sync API, async API, and dry-run plans."""

from __future__ import annotations

import asyncio
import json

import pytest

from omk_crawl import crawl, crawl_async
from omk_crawl.cli import _main, build_parser
from omk_crawl.router import SmartRouter
from omk_crawl.tools.insane_search_tool import InsaneSearchTool


def har_file(tmp_path):
    path = tmp_path / "capture.har"
    path.write_text(json.dumps({"log": {"entries": []}}))
    return path


@pytest.mark.parametrize("mode", ["sync", "async", "router"])
def test_native_har_dispatch_never_enters_web_chain(monkeypatch, tmp_path, mode):
    def forbidden(*_args, **_kwargs):
        pytest.fail("local capture was sent to the web chain")

    monkeypatch.setattr(InsaneSearchTool, "fetch", forbidden)
    path = str(har_file(tmp_path))
    if mode == "sync":
        result = crawl(path)
    elif mode == "async":
        result = asyncio.run(crawl_async(path))
    else:
        result = SmartRouter().crawl(path)
    assert result.ok and result.tool == "har"
    assert result.extracted == []
    assert result.metadata["execution"]["target_tool"] == "har"


@pytest.mark.parametrize(
    "target,tool",
    [
        ("android://SERIAL/packages", "scrcpy"),
        ("appstore://12345", "appstore"),
        ("ios://Freeform", "appstore"),
        ("baemin://36.8,127.1", "baemin"),
        ("reddit://r/programming", "reddit"),
        ("https://old.reddit.com/r/programming", "reddit"),
        ("missing.har", "har"),
        ("missing.apk", "apk"),
        ("missing.ipa", "ipa"),
        ("missing.pdf", "markitdown"),
    ],
)
def test_diagnose_uses_native_target_route(target, tool):
    plan = SmartRouter().diagnose(target)
    assert plan["target_tool"] == tool
    assert plan["escalation_order"] == [tool]


@pytest.mark.parametrize(
    "url",
    [
        "https://reddit.com.evil.test/article",
        "https://example.test/?next=reddit.com",
        "https://reddit.com@example.test/article",
    ],
)
def test_reddit_routing_uses_hostname_not_substrings(url):
    assert SmartRouter().diagnose(url)["target_tool"] is None


def test_cli_and_api_share_dispatch_without_live_device(monkeypatch, tmp_path, capsys):
    path = har_file(tmp_path)
    assert _main([str(path), "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["metadata"]["execution"]["target_tool"] == "har"


def test_engine_limits_are_cli_options():
    args = build_parser().parse_args(
        [
            "https://example.test",
            "--total-timeout",
            "15",
            "--max-fetches",
            "3",
            "--max-attempts",
            "8",
            "--no-browser",
        ]
    )
    assert args.total_timeout == 15
    assert args.max_fetches == 3
    assert args.max_attempts == 8
    assert args.no_browser


def test_diagnose_never_fetches_target(monkeypatch, tmp_path):
    from omk_crawl.tools.har_tool import HarTool

    monkeypatch.setattr(HarTool, "fetch", lambda *_args, **_kwargs: pytest.fail("dry run fetched"))
    plan = SmartRouter().diagnose(str(har_file(tmp_path)))
    assert plan["execution_limits"]["total_timeout"] == 120


def test_unknown_scheme_is_rejected_before_network(monkeypatch):
    monkeypatch.setattr(InsaneSearchTool, "fetch", lambda *_args, **_kwargs: pytest.fail("fetched"))
    result = crawl("javascript:alert(1)")
    assert not result.ok
    assert result.metadata["stop_reason"] == "invalid_target"


def test_explicit_web_adapter_keeps_robots_gate_on_service_hosts(monkeypatch):
    from .test_engine import EngineTool, ok

    tool = EngineTool("forced_http", ok())
    router = SmartRouter(tools=["curl_cffi"], learn_sites=False)
    monkeypatch.setattr(router, "_get_chain", lambda: [tool])
    monkeypatch.setattr(router, "_check_robots", lambda _url: False)
    result = router.crawl("https://www.reddit.com/r/programming")
    assert not result.ok
    assert tool.calls == []
    assert result.metadata["stop_reason"] == "robots_denied"


def test_local_target_does_not_use_network_rate_limiter(monkeypatch, tmp_path):
    router = SmartRouter()
    monkeypatch.setattr(router, "_rate_wait", lambda _url: pytest.fail("local input throttled"))
    assert router.crawl(str(har_file(tmp_path))).ok
