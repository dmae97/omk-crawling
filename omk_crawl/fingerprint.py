"""Cross-layer fingerprint coherence — the arXiv insight that wins.

Modern bot detection does not key on any single attribute; it scores the
*consistency* between layers — TLS/JA4 vs HTTP headers vs JS APIs vs
timezone/locale vs screen geometry:

  - FP-Inconsistent (arXiv:2406.07647): evasive bots fail on cross-attribute
    and over-time consistency; detectors exploit exactly those two axes.
  - Multi-layer web-agent fingerprinting (arXiv:2606.30119): superficial
    stealth patches make agents *more* detectable when layers disagree.
  - TLS bad-bot detection (arXiv:2602.09606): the handshake alone already
    separates stock HTTP clients from real browsers.

This module is the single source of truth for coherent identities. One
FingerprintProfile pins TLS impersonation + User-Agent + Client Hints +
Accept-Language + locale/timezone + viewport so every layer tells the same
story, and ``profile_for`` keeps that story *stable per site over time*
(the second axis FP-Inconsistent measures).

Guardrail: coherence is for passing anti-bot discrimination against clients
you are authorized to use — never for bypassing authentication.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

__all__ = [
    "FingerprintProfile",
    "PROFILES",
    "coherence_issues",
    "match_impersonate",
    "profile_for",
]

_CHROME_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,*/*;q=0.8"
)
_FIREFOX_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/png,image/svg+xml,*/*;q=0.8"
)
_SAFARI_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

_UA_PLATFORM_RE = (
    ("Windows NT", "Windows"),
    ("Android", "Android"),
    ("iPhone", "iOS"),
    ("Mac OS X", "macOS"),
    ("X11; Linux", "Linux"),
    ("CrOS", "Chrome OS"),
)

# Precompiled at module load: constant patterns only (no user-controlled
# input ever reaches the regex engine).
_UA_VERSION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"Chrome/(\d+)",
        r"Firefox/(\d+)",
        r"Version/(\d+)(?:\.\d+)* Safari/",
        r"Edg/(\d+)",
    )
)


_PROFILE_MARKER = "omk-fp-v1"


def _to_int(value: str | None) -> int | None:
    """Parse an int defensively; None on any malformed input."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class FingerprintProfile:
    """One coherent identity across TLS, HTTP and browser layers.

    Attributes:
        name: Short identifier ("chrome-win").
        impersonate: curl_cffi TLS target ("chrome124"). "" for browser-only.
        user_agent: Full UA string matching the TLS target family + version.
        sec_ch_ua: ``Sec-CH-UA`` value; "" when the family never sends Client
            Hints (Firefox/Safari) — sending them anyway is a bot tell.
        sec_ch_ua_platform: ``Sec-CH-UA-Platform`` value (with quotes omitted
            here, added on render); "" when Client Hints are absent.
        mobile: Whether this is a mobile identity (drives ``?0``/``?1``).
        accept_language / locale / timezone_id: One locale story.
        viewport: (width, height) a real device of this class would have.
    """

    name: str
    impersonate: str
    user_agent: str
    sec_ch_ua: str = ""
    sec_ch_ua_platform: str = ""
    mobile: bool = False
    accept_language: str = "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
    locale: str = "ko-KR"
    timezone_id: str = "Asia/Seoul"
    viewport: tuple[int, int] = (1920, 1080)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def family(self) -> str:
        """Browser family derived from the UA (TLS target must agree)."""
        ua = self.user_agent
        if "Firefox/" in ua:
            return "firefox"
        if re.search(r"Version/\d+(\.\d+)* Safari/", ua):
            return "safari"
        if "Edg/" in ua:
            return "edge"
        return "chrome"

    @property
    def platform_os(self) -> str:
        """OS claimed by the UA string."""
        for marker, name in _UA_PLATFORM_RE:
            if marker in self.user_agent:
                return name
        return self.sec_ch_ua_platform or "Windows"

    @property
    def ua_major_version(self) -> int | None:
        """Browser major version claimed by the UA, if parseable."""
        for pattern in _UA_VERSION_PATTERNS:
            m = pattern.search(self.user_agent)
            if m:
                return _to_int(m.group(1))
        return None

    def headers(self, referer: str | None = None) -> dict[str, str]:
        """Render a coherent HTTP header set for this identity.

        Client Hints are emitted only for families that actually send them;
        Sec-Fetch-* navigation headers go to Chrome-family and Firefox.
        """
        family = self.family
        h: dict[str, str] = {"User-Agent": self.user_agent}
        if family in ("chrome", "edge"):
            h["Accept"] = _CHROME_ACCEPT
            h["Accept-Encoding"] = "gzip, deflate, br, zstd"
        elif family == "firefox":
            h["Accept"] = _FIREFOX_ACCEPT
            h["Accept-Encoding"] = "gzip, deflate, br, zstd"
        else:
            h["Accept"] = _SAFARI_ACCEPT
            h["Accept-Encoding"] = "gzip, deflate, br"
        h["Accept-Language"] = self.accept_language
        if family in ("chrome", "edge"):
            h["Sec-Ch-Ua"] = self.sec_ch_ua
            h["Sec-Ch-Ua-Mobile"] = "?1" if self.mobile else "?0"
            if self.sec_ch_ua_platform:
                h["Sec-Ch-Ua-Platform"] = f'"{self.sec_ch_ua_platform}"'
        if family in ("chrome", "edge", "firefox"):
            h["Sec-Fetch-Dest"] = "document"
            h["Sec-Fetch-Mode"] = "navigate"
            h["Sec-Fetch-Site"] = "none" if referer is None else "cross-site"
            h["Sec-Fetch-User"] = "?1"
            h["Upgrade-Insecure-Requests"] = "1"
        if referer:
            h["Referer"] = referer
        return h

    def curl_kwargs(self, referer: str | None = None) -> dict[str, Any]:
        """kwargs for ``curl_cffi.requests.get`` — TLS and headers agree."""
        if not self.impersonate:
            raise ValueError(f"profile {self.name} has no TLS impersonation target")
        return {"impersonate": self.impersonate, "headers": self.headers(referer)}

    def browser_context_kwargs(self) -> dict[str, Any]:
        """Playwright/patchright ``new_context`` kwargs telling the same story."""
        return {
            "user_agent": self.user_agent,
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "viewport": {"width": self.viewport[0], "height": self.viewport[1]},
        }

    def coherence_issues(self, headers: dict[str, str] | None = None) -> list[str]:
        """Validate a header mapping (default: this profile's) layer-by-layer."""
        return coherence_issues(headers or self.headers(), impersonate=self.impersonate)


# ── Built-in coherent profiles ─────────────────────────────────────────────
# Each entry was cross-checked: TLS target ↔ UA family+major ↔ Client Hints
# brand+major ↔ platform ↔ viewport class. ko-KR/Asia-Seoul is the operator
# locale; keep it identical across layers (and across requests — see
# profile_for) instead of randomizing it into an inconsistent story.

PROFILES: tuple[FingerprintProfile, ...] = (
    FingerprintProfile(
        name="chrome-win",
        impersonate="chrome124",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        sec_ch_ua_platform="Windows",
        viewport=(1920, 1080),
    ),
    FingerprintProfile(
        name="chrome-mac",
        impersonate="chrome124",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        sec_ch_ua_platform="macOS",
        viewport=(1680, 1050),
    ),
    FingerprintProfile(
        name="chrome-linux",
        impersonate="chrome124",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        sec_ch_ua_platform="Linux",
        viewport=(1536, 864),
    ),
    FingerprintProfile(
        name="firefox-win",
        impersonate="firefox133",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"
        ),
        viewport=(1920, 1080),
    ),
    FingerprintProfile(
        name="safari-mac",
        impersonate="safari17_0",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.5 Safari/605.1.15"
        ),
        viewport=(1440, 900),
    ),
)

_PROFILES_BY_NAME: dict[str, FingerprintProfile] = {p.name: p for p in PROFILES}


def profile_for(url: str, salt: str = "") -> FingerprintProfile:
    """Deterministically pick one profile per site (over-time consistency).

    FP-Inconsistent's second axis is *temporal*: a client whose fingerprint
    changes between requests to the same site is flagged. Keying the choice
    on the domain (plus an optional deployment-wide salt) keeps every request
    to that site on the same identity, reproducibly and offline.
    """
    host = urlparse(url if "://" in url else f"//{url}").hostname or url
    digest = hashlib.sha256(f"{_PROFILE_MARKER}|{host.lower()}|{salt}".encode()).digest()
    return PROFILES[int.from_bytes(digest[:4]) % len(PROFILES)]


def match_impersonate(impersonate: str) -> FingerprintProfile:
    """Coherent profile for an existing curl_cffi impersonation target.

    Prefers the same family and major version; falls back to the same family,
    then to the default Chrome-on-Windows identity.
    """
    imp = impersonate.lower()
    family = (
        "firefox"
        if "firefox" in imp
        else "safari"
        if "safari" in imp
        else "edge"
        if "edge" in imp
        else "chrome"
    )
    version_match = re.search(r"(\d+)", imp)
    version = _to_int(version_match.group(1)) if version_match else None
    candidates = [p for p in PROFILES if p.family == family]
    if version is not None:
        for p in candidates:
            if p.ua_major_version == version:
                return p
    if candidates:
        return candidates[0]
    return PROFILES[0]


def coherence_issues(headers: dict[str, str], impersonate: str | None = None) -> list[str]:
    """Cross-layer consistency audit of an HTTP header set.

    Returns a list of human-readable violations; empty means every layer
    tells the same story. Each rule mirrors a check documented by bot
    vendors (DataDome JS-tag consistency tests) and fingerprinting research.
    """
    issues: list[str] = []
    norm = {k.lower(): v for k, v in headers.items()}
    ua = norm.get("user-agent", "")
    ch = norm.get("sec-ch-ua", "")
    ch_mobile = norm.get("sec-ch-ua-mobile", "")
    ch_platform = norm.get("sec-ch-ua-platform", "").strip('"')

    is_firefox = "Firefox/" in ua
    is_safari = bool(re.search(r"Version/\d+(\.\d+)* Safari/", ua)) and "Chrome/" not in ua
    is_chrome = "Chrome/" in ua and not is_safari and not is_firefox

    # Rule 1 — Client Hints exist only on Chromium. FF/Safari sending them
    # means a header list was glued onto a non-Chromium TLS stack.
    if (is_firefox or is_safari) and ch:
        issues.append(f"UA claims {'Firefox' if is_firefox else 'Safari'} but Sec-Ch-Ua present")

    # Rule 2 — UA OS token vs Sec-CH-UA-Platform must agree.
    ua_os = next((name for marker, name in _UA_PLATFORM_RE if marker in ua), "")
    if ch_platform and ua_os and ch_platform.lower() != ua_os.lower():
        issues.append(f"UA platform {ua_os} != Sec-Ch-Ua-Platform {ch_platform}")

    # Rule 3 — Chrome major version must match between UA and Client Hints.
    if is_chrome and ch:
        ua_major = re.search(r"Chrome/(\d+)", ua)
        ch_major = re.search(r'"Google Chrome";v="(\d+)"', ch)
        if ua_major and ch_major and ua_major.group(1) != ch_major.group(1):
            issues.append(f"UA Chrome/{ua_major.group(1)} != Sec-Ch-Ua Chrome v{ch_major.group(1)}")
        if "Chromium" not in ch:
            issues.append("Chrome UA but Sec-Ch-Ua missing Chromium brand")

    # Rule 4 — Mobile token vs Sec-CH-UA-Mobile.
    ua_mobile = any(t in ua for t in ("Android", "iPhone", "Mobile"))
    if ch_mobile == "?1" and not ua_mobile:
        issues.append("Sec-Ch-Ua-Mobile ?1 but UA is desktop")
    if ch_mobile == "?0" and ua_mobile:
        issues.append("Sec-Ch-Ua-Mobile ?0 but UA is mobile")

    # Rule 5 — TLS impersonation target vs UA family (+ major when known).
    if impersonate:
        imp = impersonate.lower()
        imp_family = "firefox" if "firefox" in imp else "safari" if "safari" in imp else "chrome"
        ua_family = "firefox" if is_firefox else "safari" if is_safari else "chrome"
        if imp_family != ua_family:
            issues.append(f"impersonate {impersonate} ({imp_family}) != UA family {ua_family}")
        imp_major = re.search(r"(\d+)", imp)
        if imp_family == "chrome" and is_chrome and imp_major:
            ua_major = re.search(r"Chrome/(\d+)", ua)
            if ua_major and ua_major.group(1) != imp_major.group(1):
                issues.append(
                    f"impersonate {impersonate} major {imp_major.group(1)} "
                    f"!= UA Chrome/{ua_major.group(1)}"
                )

    return issues
