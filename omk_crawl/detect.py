"""Detection heuristics — what's blocking us, what do we need?"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from enum import Flag, auto


class BlockType(Flag):
    """Detected blocking mechanism."""

    NONE = 0
    TLS_FINGERPRINT = auto()  # JA3/HTTP2 fingerprint rejection
    JS_REQUIRED = auto()  # empty body / noscript / JS framework
    CLOUDFLARE = auto()  # CF challenge / Turnstile
    WAF = auto()  # generic WAF (403 + challenge page)
    RATE_LIMIT = auto()  # 429
    AUTH_REQUIRED = auto()  # 401 / login redirect


@dataclass
class Detection:
    """Result of analyzing a response."""

    block: BlockType = BlockType.NONE
    status_code: int | None = None
    needs_browser: bool = False
    needs_stealth: bool = False
    needs_llm_agent: bool = False
    confidence: float = 0.0
    detail: str = ""


# --- Tool availability ---

_TOOL_MODULES: dict[str, str] = {
    "curl_cffi": "curl_cffi",
    "crawl4ai": "crawl4ai",
    "scrapling": "scrapling",
    "scrapy": "scrapy",
    "crawlee": "crawlee",
    "browser_use": "browser_use",
    "autoscraper": "autoscraper",
    "markitdown": "markitdown",
}


def tool_available(name: str) -> bool:
    """Check if a tool's Python module is importable."""
    mod = _TOOL_MODULES.get(name, name)
    return importlib.util.find_spec(mod) is not None


def available_tools() -> list[str]:
    """List installed tools."""
    return [name for name in _TOOL_MODULES if tool_available(name)]


def missing_tools() -> list[str]:
    """List tools that need `pip install`."""
    return [name for name in _TOOL_MODULES if not tool_available(name)]


# --- Response analysis ---

_CF_MARKERS = ("cf-browser-verification", "cf_chl_opt", "turnstile", "challenge-platform")
_JS_MARKERS = ("<noscript>", "id=\"__next\"", "id=\"root\"", "id=\"app\"", "ng-app", "data-reactroot")
_WAF_MARKERS = ("access denied", "blocked", "captcha", "are you a robot", "unusual traffic")


def detect_block(html: str | None, status_code: int | None) -> Detection:
    """Analyze response HTML + status to detect blocking type."""
    d = Detection(status_code=status_code)

    if status_code == 429:
        d.block = BlockType.RATE_LIMIT
        d.confidence = 0.9
        d.detail = "Rate limited (429)"
        return d

    if status_code == 401:
        d.block = BlockType.AUTH_REQUIRED
        d.confidence = 0.9
        d.detail = "Authentication required (401)"
        return d

    if not html:
        if status_code == 403:
            d.block = BlockType.TLS_FINGERPRINT | BlockType.WAF
            d.confidence = 0.7
            d.detail = "403 with empty body — likely TLS fingerprint or WAF"
        return d

    lower = html.lower()

    # Cloudflare
    if any(m in lower for m in _CF_MARKERS):
        d.block |= BlockType.CLOUDFLARE
        d.needs_stealth = True
        d.confidence = 0.9
        d.detail = "Cloudflare challenge detected"

    # JS-required
    if any(m in lower for m in _JS_MARKERS) and len(html) < 5000:
        d.block |= BlockType.JS_REQUIRED
        d.needs_browser = True
        d.confidence = max(d.confidence, 0.8)
        d.detail = "JS framework shell detected (needs rendering)"

    # Generic WAF
    if status_code == 403 and any(m in lower for m in _WAF_MARKERS):
        d.block |= BlockType.WAF
        d.confidence = max(d.confidence, 0.7)
        d.detail = "WAF challenge page"

    # TLS fingerprint: 403 + very short/empty response
    if status_code == 403 and len(html) < 500 and not d.block:
        d.block = BlockType.TLS_FINGERPRINT
        d.confidence = 0.6
        d.detail = "Short 403 — possible TLS fingerprint block"

    if not d.block:
        d.block = BlockType.NONE
        d.confidence = 1.0
        d.detail = "No blocking detected"

    return d
