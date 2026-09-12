"""Target classification shared by the public API, CLI, and execution planner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_SCHEMES = {
    "android": "scrcpy",
    "adb": "scrcpy",
    "device": "scrcpy",
    "appstore": "appstore",
    "ios": "appstore",
    "reddit": "reddit",
    "baemin": "baemin",
    "apk": "apk",
    "ipa": "ipa",
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


def resolve_target(value: str, *, explicit: bool = False) -> Target:
    """Classify without fetching, opening a device, or reading file contents."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Target must be a non-empty string")
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"}:
        if not parsed.hostname or any(ord(char) < 32 for char in value):
            raise ValueError("Invalid HTTP(S) target")
        # Validate the port before choosing a network adapter.
        _ = parsed.port
        host = parsed.hostname.lower()
        tool = None
        if not explicit and (host == "reddit.com" or host.endswith(".reddit.com")):
            tool = "reddit"
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
