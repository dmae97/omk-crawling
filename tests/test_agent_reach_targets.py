"""Agent Reach registry and target-classification tests (no live calls)."""

from __future__ import annotations

import asyncio

import pytest

import omk_crawl.detect as detect
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.router import SmartRouter
from omk_crawl.targets import Target, resolve_target
from omk_crawl.tools import ALL_TOOLS, ESCALATION_CHAIN, TARGET_TOOLS, get_tool
from omk_crawl.tools.agent_reach_tool import AgentReachTool, route_for_target

_HOST_CHANNELS = {
    "github.com": "github",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "bilibili.com": "bilibili",
    "b23.tv": "bilibili",
    "v2ex.com": "v2ex",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "xiaohongshu.com": "xiaohongshu",
    "xhslink.com": "xiaohongshu",
    "linkedin.com": "linkedin",
    "zhipin.com": "boss",
    "xueqiu.com": "xueqiu",
    "xiaoyuzhoufm.com": "xiaoyuzhou",
}


def test_registered() -> None:
    assert ALL_TOOLS["agent_reach"] is AgentReachTool
    assert isinstance(get_tool("agent_reach"), AgentReachTool)
    assert "agent_reach" in TARGET_TOOLS


def test_agent_reach_is_not_in_escalation_chain() -> None:
    assert AgentReachTool not in ESCALATION_CHAIN


@pytest.mark.parametrize(
    ("url", "channel"),
    [
        ("reach://github/search?q=x", "github"),
        ("https://github.com/o/r/issues/1", "github"),
        ("https://youtu.be/abc", "youtube"),
        ("https://www.bilibili.com/video/BV1", "bilibili"),
        ("https://v2ex.com/t/1", "v2ex"),
        ("https://x.com/u/status/1", "twitter"),
    ],
)
def test_routes(url: str, channel: str) -> None:
    target = resolve_target(url)
    assert target.tool == "agent_reach"
    assert target.is_web is False
    assert route_for_target(url)[0] == channel


def test_reach_scheme_returns_native_target() -> None:
    value = "reach://github/search?q=x"
    assert resolve_target(value) == Target(value, "agent_reach", False)


@pytest.mark.parametrize(("domain", "channel"), _HOST_CHANNELS.items())
@pytest.mark.parametrize("subdomain", [False, True])
def test_official_hosts_and_subdomains(domain: str, channel: str, subdomain: bool) -> None:
    host = f"www.{domain}" if subdomain else domain
    url = f"https://{host}/example"
    assert resolve_target(url) == Target(url, "agent_reach", False)
    assert route_for_target(url)[0] == channel


@pytest.mark.parametrize("domain", _HOST_CHANNELS)
def test_plain_http_official_hosts_stay_on_web_path(domain: str) -> None:
    url = f"http://{domain}/example"
    # Do not call route_for_target(): plain HTTP must not select Agent Reach.
    assert resolve_target(url) == Target(url, None, True)


def test_reddit_stays_on_existing_adapter() -> None:
    assert resolve_target("https://www.reddit.com/r/python/").tool == "reddit"
    assert resolve_target("https://old.reddit.com/r/python/").tool == "reddit"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com.evil.test/o/r",
        "https://github.com@evil.test/o/r",
        "https://evil.test/?next=github.com",
    ],
)
def test_lookalike_hosts_remain_web_targets(url: str) -> None:
    assert resolve_target(url) == Target(url, None, True)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com:not-a-port/o/r",
        "https://github.com:0/o/r",
        "https://github.com:65536/o/r",
    ],
)
def test_invalid_port_is_rejected_before_agent_reach_selection(url: str) -> None:
    with pytest.raises(ValueError):
        resolve_target(url)


@pytest.mark.parametrize(
    "target",
    [
        "reach://github/search?q=bad\x00value",
        "reach://github/search?q=bad%00value",
        "https://github.com/org/repo\x7f",
    ],
)
def test_control_characters_are_rejected_before_target_selection(target: str) -> None:
    with pytest.raises(ValueError):
        resolve_target(target)


def test_agent_reach_is_listed_as_host_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detect.os, "name", "posix")
    monkeypatch.setattr(detect.shutil, "which", lambda _name: "/usr/bin/agent-reach")
    assert "agent_reach" in detect.available_tools()

    monkeypatch.setattr(detect.shutil, "which", lambda _name: None)
    assert "agent_reach" in detect.missing_tools()


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    ("reason", "status"),
    [
        ("auth_required", CrawlStatus.BLOCKED),
        ("rate_limited", CrawlStatus.BLOCKED),
        ("deadline_exceeded", CrawlStatus.ERROR),
        ("tool_missing", CrawlStatus.TOOL_MISSING),
    ],
)
def test_public_router_preserves_stop_reason_with_one_invocation(
    monkeypatch: pytest.MonkeyPatch, mode: str, reason: str, status: CrawlStatus
) -> None:
    calls: list[str] = []

    def fetch(self: AgentReachTool, url: str, **_kwargs) -> CrawlResult:
        calls.append(url)
        return CrawlResult(
            url=url,
            status=status,
            tool=self.name,
            error=reason,
            metadata={"stop_reason": reason, "untrusted_content": True},
        )

    monkeypatch.setattr(AgentReachTool, "fetch", fetch)
    router = SmartRouter(
        tools=["agent_reach"],
        respect_robots=False,
        min_delay=0,
        learn_sites=False,
        max_retries=3,
        retry_delay=0,
    )
    target = "reach://github/search?q=x"

    result = asyncio.run(router.crawl_async(target)) if mode == "async" else router.crawl(target)

    assert result.metadata["stop_reason"] == reason
    assert result.metadata["retry_count"] == 0
    assert result.metadata["attempts"] == 1
    assert result.metadata["execution"]["fetches"] == 1
    assert calls == [target]
