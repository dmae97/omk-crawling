"""Target classification shared by the public API, CLI, and execution planner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

_SCHEMES = {
    "android": "scrcpy",
    "adb": "scrcpy",
    "device": "scrcpy",
    "appstore": "appstore",
    "ios": "appstore",
    "reddit": "reddit",
    "reach": "agent_reach",
    "baemin": "baemin",
    "apk": "apk",
    "ipa": "ipa",
    "proxy": "proxy_pool",
}

# Official Agent Reach base-domain → channel mapping. Exact HTTPS hosts and their
# subdomains use agent_reach; route_for_target() performs the channel dispatch.
# Reddit is intentionally absent so its specialized adapter keeps precedence.
_AGENT_REACH_HOST_CHANNELS = {
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

_FILES = {
    ".har": "har",
    ".apk": "apk",
    ".xapk": "apk",
    ".apks": "apk",
    ".ipa": "ipa",
    ".pdf": "markitdown",
    ".html": "markitdown",
    ".htm": "markitdown",
    ".docx": "markitdown",
    ".pptx": "markitdown",
    ".xlsx": "markitdown",
    ".csv": "markitdown",
    ".txt": "markitdown",
    ".md": "markitdown",
}


@dataclass(frozen=True)
class Target:
    value: str
    tool: str | None
    is_web: bool


def _has_control_chars(value: str) -> bool:
    return any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)


def resolve_target(value: str, *, explicit: bool = False) -> Target:
    """Classify without fetching, opening a device, or reading file contents."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Target must be a non-empty string")
    if _has_control_chars(value):
        raise ValueError("Target contains control characters")
    parsed = urlsplit(value)
    if parsed.scheme == "reach" and _has_control_chars(unquote(value)):
        raise ValueError("Target contains decoded control characters")
    if parsed.scheme in {"http", "https"}:
        if not parsed.hostname:
            raise ValueError("Invalid HTTP(S) target")
        # Validate the port before choosing a network adapter.
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Invalid HTTP(S) port")
        host = parsed.hostname.lower()
        tool = None
        if not explicit:
            # Specialized adapters take precedence over generic host routing.
            if host == "reddit.com" or host.endswith(".reddit.com"):
                tool = "reddit"
            elif parsed.scheme == "https" and any(
                host == domain or host.endswith(f".{domain}")
                for domain in _AGENT_REACH_HOST_CHANNELS
            ):
                tool = "agent_reach"
        return Target(value, tool, tool is None)
    if parsed.scheme in _SCHEMES:
        return Target(value, _SCHEMES[parsed.scheme], False)
    if value.lower() in {"appstore", "ios", "reddit", "baemin"}:
        return Target(value, _SCHEMES[value.lower()], False)
    if not parsed.scheme:
        path = Path(value)
        tool = _FILES.get(path.suffix.lower())
        if tool or path.is_file():
            return Target(value, tool or "markitdown", False)
    if explicit:
        # Explicit adapters retain tool-specific inputs such as crawl4ai raw: HTML.
        return Target(value, None, False)
    raise ValueError("Expected an HTTP(S) URL, supported native URI, or local file")
