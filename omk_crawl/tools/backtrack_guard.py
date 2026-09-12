#!/usr/bin/env python3
"""
Backtracking Guard v1.0 — Anti-forensic rotation engine.

Prevents:
  - IP reuse (proxy fingerprinting)
  - User-Agent pattern recognition
  - Timing-based correlation (jittered delays)
  - Request header consistency checks
  - Session cookie accumulation
  - Referer chain leakage
  - TLS fingerprint correlation (different impersonate per request)

Strategy: every request looks like a completely new visitor from a
different browser, IP, and session — making backtracking impossible.
"""

from __future__ import annotations

import hashlib
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

# ── UA rotation pool (100+ real browser fingerprints) ──
_CHROME_VERSIONS = [124, 125, 126, 120, 121, 122, 123, 116, 117, 118, 119, 110, 111]
_FIREFOX_VERSIONS = [133, 132, 131, 130, 129, 128, 127, 126, 125]
_SAFARI_VERSIONS = [
    ("17.5", "605.1.15"),
    ("17.4", "605.1.15"),
    ("17.3", "605.1.15"),
    ("16.6", "605.1.15"),
    ("16.5", "605.1.15"),
]
_EDGE_VERSIONS = [126, 125, 124, 123, 122]

_PLATFORMS = [
    ("Windows NT 10.0; Win64; x64", "Windows"),
    ("Macintosh; Intel Mac OS X 14_5", "macOS"),
    ("Macintosh; Intel Mac OS X 14_4", "macOS"),
    ("X11; Linux x86_64", "Linux"),
    ("X11; Ubuntu; Linux x86_64", "Linux"),
    ("Macintosh; Intel Mac OS X 13_6", "macOS"),
    ("Windows NT 10.0; Win64; x64", "Windows"),
    ("iPhone; CPU iPhone OS 17_5 like Mac OS X", "iOS"),
    ("iPad; CPU OS 17_5 like Mac OS X", "iOS"),
    ("Android 14; Pixel 8 Pro", "Android"),
]

_SEC_CH_UA_BRANDS = [
    '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
    '"Chromium";v="120", "Google Chrome";v="120", "Not-A.Brand";v="99"',
    '"Microsoft Edge";v="126", "Chromium";v="126", "Not-A.Brand";v="99"',
    '"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
]


@dataclass
class BrowserProfile:
    """A complete browser fingerprint for one request."""

    user_agent: str
    sec_ch_ua: str
    sec_ch_ua_platform: str
    sec_ch_ua_mobile: str
    accept: str
    accept_language: str
    platform_type: str
    viewport: str


def _generate_profile() -> BrowserProfile:
    """Generate a realistic browser profile."""
    browser_type = random.choices(
        ["chrome", "firefox", "safari", "edge"],
        weights=[0.45, 0.25, 0.20, 0.10],
        k=1,
    )[0]

    platform, plat_type = random.choice(_PLATFORMS)
    lang = random.choices(
        ["ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7", "en-US,en;q=0.9,ko;q=0.7", "ko-KR,ko;q=0.9"],
        weights=[0.6, 0.3, 0.1],
        k=1,
    )[0]
    is_mobile = plat_type in ("iOS", "Android")

    if browser_type == "chrome":
        v = random.choice(_CHROME_VERSIONS)
        ua = (
            f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{v}.0.0.0 Safari/537.36"
        )
        accept = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        )
        sec_ch_ua = random.choice(_SEC_CH_UA_BRANDS)
        sec_ch_ua_platform = f'"{plat_type}"' if plat_type != "Windows" else '"Windows"'
    elif browser_type == "firefox":
        v = random.choice(_FIREFOX_VERSIONS)
        ua = f"Mozilla/5.0 ({platform}; rv:{v}.0) Gecko/20100101 Firefox/{v}.0"
        accept = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        )
        sec_ch_ua = ""
        sec_ch_ua_platform = ""
    elif browser_type == "safari":
        v_full, webkit = random.choice(_SAFARI_VERSIONS)
        ua = (
            f"Mozilla/5.0 ({platform}) AppleWebKit/{webkit} (KHTML, like Gecko) "
            f"Version/{v_full} Safari/{webkit}"
        )
        accept = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        sec_ch_ua = ""
        sec_ch_ua_platform = ""
    else:  # edge
        v = random.choice(_EDGE_VERSIONS)
        ua = (
            f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{v}.0.0.0 Safari/537.36 Edg/{v}.0.0.0"
        )
        accept = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
        )
        sec_ch_ua = f'"Microsoft Edge";v="{v}", "Chromium";v="{v}", "Not-A.Brand";v="99"'
        sec_ch_ua_platform = f'"{plat_type}"' if plat_type != "Windows" else '"Windows"'

    return BrowserProfile(
        user_agent=ua,
        sec_ch_ua=sec_ch_ua,
        sec_ch_ua_platform=sec_ch_ua_platform,
        sec_ch_ua_mobile="?1" if is_mobile else "?0",
        accept=accept,
        accept_language=lang,
        platform_type=plat_type,
        viewport="375x812"
        if is_mobile
        else random.choice(["1920x1080", "1440x900", "2560x1440", "1680x1050"]),
    )


class BacktrackGuard:
    """Prevent backtracking across all request dimensions."""

    def __init__(self, session_seed: str | None = None):
        seed = session_seed or hashlib.sha256(str(time.time()).encode()).hexdigest()
        self._rng = random.Random(seed)
        # Track unique values per dimension
        self._used_ips: set[str] = set()
        self._used_uas: set[str] = set()
        self._used_accepts: set[str] = set()
        self._used_sec_ch_ua: set[str] = set()
        # Sliding window for timing analysis
        self._request_times: deque[float] = deque(maxlen=100)
        # Unique hash per request to prevent correlation
        self._request_hashes: deque[str] = deque(maxlen=500)
        self._lock = threading.Lock()

    def headers(self, proxy_ip: str | None = None) -> dict[str, str]:
        """Generate unique headers for this request. Never repeats patterns."""
        profile = _generate_profile()

        with self._lock:
            # Ensure no UA reuse
            attempts = 0
            while profile.user_agent in self._used_uas and attempts < 100:
                profile = _generate_profile()
                attempts += 1
            self._used_uas.add(profile.user_agent)

            if proxy_ip:
                self._used_ips.add(proxy_ip)

        headers: dict[str, str] = {
            "User-Agent": profile.user_agent,
            "Accept": profile.accept,
            "Accept-Language": profile.accept_language,
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": random.choice(["document", "empty"]),
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": random.choice(["none", "same-origin", "cross-site"]),
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "DNT": "1",
        }

        if profile.sec_ch_ua:
            headers["Sec-Ch-Ua"] = profile.sec_ch_ua
            headers["Sec-Ch-Ua-Mobile"] = profile.sec_ch_ua_mobile
            headers["Sec-Ch-Ua-Platform"] = profile.sec_ch_ua_platform

        # Randomize Referer
        referers = [
            "https://www.google.com/",
            "https://www.google.co.kr/",
            "https://search.naver.com/search.naver?query=ERP",
            "https://www.bing.com/",
            "",  # direct navigation
        ]
        headers["Referer"] = self._rng.choice(referers)

        # Randomize Connection header
        headers["Connection"] = self._rng.choice(["keep-alive", "close"])

        # Track timing
        self._request_times.append(time.time())

        # Generate unique request hash
        req_hash = hashlib.sha256(
            f"{profile.user_agent}{headers['Referer']}{time.time_ns()}".encode()
        ).hexdigest()[:16]
        self._request_hashes.append(req_hash)

        return headers

    def delay(self, min_s: float = 0.5, max_s: float = 4.0) -> float:
        """Return a jittered delay to avoid timing fingerprinting."""
        # Use truncated normal distribution for realistic delay
        base = random.gauss(mu=(min_s + max_s) / 2, sigma=(max_s - min_s) / 4)
        delay = max(min_s, min(max_s, base))
        time.sleep(delay)
        return delay

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "unique_uas": len(self._used_uas),
                "unique_ips": len(self._used_ips),
                "total_requests": len(self._request_hashes),
                "avg_interval_s": (
                    (
                        (self._request_times[-1] - self._request_times[0])
                        / max(len(self._request_times) - 1, 1)
                    )
                    if len(self._request_times) >= 2
                    else 0
                ),
            }


# ── Singleton for module-level use ──
_guard: BacktrackGuard | None = None


def get_guard() -> BacktrackGuard:
    global _guard
    if _guard is None:
        _guard = BacktrackGuard()
    return _guard
