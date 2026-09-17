"""Session warm-up and clearance-cookie reuse — solve once, replay cheap.

The most reliable "unblockable" pattern is not a smarter client, it is a
*division of labor*:

  1. A real, fingerprint-coherent browser lands on the site like a human
     (natural entry point, think-time, scroll) and passes whatever challenge
     the WAF presents.
  2. The resulting clearance cookies (``cf_clearance``, ``datadome``,
     ``_abck``, ``aws-waf-token``…) are harvested into a :class:`WarmSession`.
  3. Cheap clients (curl_cffi with the *same* TLS profile and headers —
     see ``fingerprint.py``) replay that session for the actual workload.

This mirrors how the defenses work: Cloudflare/Akamai/DataDome issue a
clearance token bound to fingerprint signals, so the replay must keep TLS,
headers and cookies coherent (arXiv:2406.07647, arXiv:2606.30119) and the
session must be refreshed before its TTL expires.

Guardrail: warm-up passes anti-bot discrimination on content you are
authorized to access. It never bypasses authentication — a 401/login wall
stays a wall (see ``routing.is_auth_block``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from omk_crawl.behavior import BehaviorClock
from omk_crawl.fingerprint import PROFILES, FingerprintProfile, profile_for
from omk_crawl.result import CrawlResult, CrawlStatus

logger = logging.getLogger("omk_crawl")

__all__ = ["CLEARANCE_COOKIES", "SessionWarmup", "WarmSession", "warm_crawl"]

#: Cookie names that signal a passed anti-bot challenge, per vendor.
CLEARANCE_COOKIES: frozenset[str] = frozenset(
    {
        "cf_clearance",  # Cloudflare
        "datadome",  # DataDome
        "_abck",  # Akamai Bot Manager
        "bm_sz",  # Akamai
        "ak_bmsc",  # Akamai
        "pxcts",  # PerimeterX / HUMAN
        "_px3",  # PerimeterX / HUMAN
        "aws-waf-token",  # AWS WAF
        "reese84",  # Imperva
        "x-kpsdk-ct",  # Kasada
    }
)

_PROFILES_BY_NAME: dict[str, FingerprintProfile] = {p.name: p for p in PROFILES}
_DEFAULT_TTL = 1800.0  # cf_clearance-class tokens; conservative default


def _domain_of(url: str) -> str | None:
    try:
        host = urlparse(url if "://" in url else f"//{url}").hostname
    except ValueError:
        return None
    return host.lower() if host else None


def _root_of(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    scheme = parsed.scheme or "https"
    return f"{scheme}://{parsed.netloc}/"


@dataclass(frozen=True, slots=True)
class WarmSession:
    """A passed session: clearance cookies + the identity that earned them."""

    domain: str
    cookies: dict[str, str]
    profile: FingerprintProfile
    acquired_at: float
    tool: str
    ttl: float = _DEFAULT_TTL

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.acquired_at + self.ttl

    @property
    def has_clearance(self) -> bool:
        """True when at least one known clearance cookie is present."""
        return any(name in CLEARANCE_COOKIES for name in self.cookies)

    def curl_kwargs(self) -> dict[str, Any]:
        """Replay kwargs for curl_cffi: same TLS profile, same headers, cookies."""
        kwargs = self.profile.curl_kwargs()
        kwargs["cookies"] = dict(self.cookies)
        return kwargs

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "cookies": self.cookies,
            "profile": self.profile.name,
            "acquired_at": self.acquired_at,
            "tool": self.tool,
            "ttl": self.ttl,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WarmSession | None:
        """Parse a persisted session; None on any malformed field (fail-closed)."""
        try:
            domain = raw["domain"]
            cookies = raw["cookies"]
            profile = _PROFILES_BY_NAME[raw["profile"]]
            acquired_at = float(raw["acquired_at"])
            tool = str(raw["tool"])
            ttl = float(raw.get("ttl", _DEFAULT_TTL))
        except (KeyError, TypeError, ValueError):
            return None
        if not isinstance(domain, str) or not isinstance(cookies, dict):
            return None
        clean = {str(k): str(v) for k, v in cookies.items()}
        return cls(domain, clean, profile, acquired_at, tool, ttl)


class SessionWarmup:
    """Acquires, caches and replays warmed sessions per domain.

    Args:
        path: Persistence file (default ``~/.cache/omk-crawl/warm-sessions.json``;
            override with env ``OMK_CRAWL_WARM_SESSIONS``). None = memory only.
        ttl: Default session TTL in seconds.
        seed: Extra salt for the behavior clock / profile selection.
    """

    def __init__(
        self,
        path: Path | None = None,
        ttl: float = _DEFAULT_TTL,
        seed: str = "",
    ) -> None:
        configured = os.environ.get("OMK_CRAWL_WARM_SESSIONS")
        self._path = path or (
            Path(configured).expanduser()
            if configured
            else Path("~/.cache/omk-crawl/warm-sessions.json").expanduser()
        )
        self._ttl = ttl
        self._seed = seed
        self._sessions: dict[str, WarmSession] = self._load()

    # ── Cache ───────────────────────────────────────────────────────────

    def get(self, url: str) -> WarmSession | None:
        """Fresh cached session for this URL's domain, else None."""
        domain = _domain_of(url)
        if domain is None:
            return None
        session = self._sessions.get(domain)
        if session is None:
            return None
        if session.expired():
            self._sessions.pop(domain, None)
            self._save()
            return None
        return session

    def record(
        self,
        url: str,
        cookies: dict[str, str],
        profile: FingerprintProfile | None = None,
        tool: str = "manual",
        ttl: float | None = None,
    ) -> WarmSession:
        """Store a session (e.g. cookies exported from your own browser)."""
        domain = _domain_of(url)
        if domain is None:
            raise ValueError(f"cannot determine domain for {url!r}")
        session = WarmSession(
            domain=domain,
            cookies={str(k): str(v) for k, v in cookies.items()},
            profile=profile or profile_for(url, self._seed),
            acquired_at=time.time(),
            tool=tool,
            ttl=ttl if ttl is not None else self._ttl,
        )
        self._sessions[domain] = session
        self._save()
        return session

    def invalidate(self, url: str) -> None:
        domain = _domain_of(url)
        if domain is not None and self._sessions.pop(domain, None) is not None:
            self._save()

    # ── Acquisition ─────────────────────────────────────────────────────

    def acquire(
        self,
        url: str,
        *,
        timeout: int = 45,
        headless: bool = True,
        proxy: str | None = None,
    ) -> WarmSession:
        """Drive a real browser through the site entry and harvest cookies.

        Tries nodriver (CDP-native Chrome), then patchright (patched
        Playwright), then camoufox (anti-detect Firefox) — first available
        driver that returns cookies wins. Raises RuntimeError if every
        driver is unavailable or fails (fail-closed: no fake sessions).
        """
        root = _root_of(url)
        clock = BehaviorClock(f"warmup|{_domain_of(url) or ''}|{self._seed}")
        errors: list[str] = []
        for name, driver in (
            ("nodriver", self._acquire_nodriver),
            ("patchright", self._acquire_patchright),
            ("camoufox", self._acquire_camoufox),
        ):
            try:
                cookies = driver(root, timeout=timeout, headless=headless, proxy=proxy, clock=clock)
            except Exception as exc:  # driver missing or failed — try the next
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            if cookies:
                session = self.record(url, cookies, tool=name)
                logger.info(
                    "warmup: %s acquired %d cookies for %s (clearance=%s)",
                    name,
                    len(cookies),
                    session.domain,
                    session.has_clearance,
                )
                return session
            errors.append(f"{name}: no cookies harvested")
        raise RuntimeError(
            "warmup failed — no browser driver succeeded. "
            "Install one of: nodriver, patchright, camoufox. "
            f"Errors: {errors}"
        )

    def _acquire_nodriver(
        self,
        root: str,
        *,
        timeout: int,
        headless: bool,
        proxy: str | None,
        clock: BehaviorClock,
    ) -> dict[str, str]:
        import nodriver as nd  # noqa: F401 — ImportError means: try next driver

        async def _run() -> dict[str, str]:
            args = [f"--proxy-server={proxy}"] if proxy else None
            browser = await nd.start(headless=headless, browser_args=args)
            try:
                page = await browser.get(root)
                await page.wait(min(max(clock.think_time() + 1.0, 2.0), 6.0))
                raw = await browser.cookies.get_all()
                return {c.name: c.value for c in raw}
            finally:
                browser.stop()

        return asyncio.run(_run())

    def _acquire_patchright(
        self,
        root: str,
        *,
        timeout: int,
        headless: bool,
        proxy: str | None,
        clock: BehaviorClock,
    ) -> dict[str, str]:
        from patchright.sync_api import sync_playwright

        # Use the full six-layer plan rather than the profile alone. The context
        # kwargs and the init script then come from one identity, so the challenge
        # flow sees a client whose headers, JS surface and TLS all agree — the
        # property that v2.12 could only guarantee down to the header layer.
        # Imported lazily so warmup's own import surface stays unchanged.
        from omk_crawl.evasion import plan_for

        plan = plan_for(root, salt=self._seed)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            try:
                ctx_kwargs: dict[str, Any] = plan.browser_kwargs()
                if proxy:
                    ctx_kwargs["proxy"] = {"server": proxy}
                context = browser.new_context(**ctx_kwargs)
                # Must precede any page script: the patches have to be installed
                # before the challenge page can observe the raw browser.
                context.add_init_script(plan.init_script())
                page = context.new_page()
                page.goto(root, wait_until="domcontentloaded", timeout=timeout * 1000)
                page.wait_for_timeout(max(250, round(clock.think_time() * 1000)))
                return {
                    str(c.get("name")): str(c.get("value", ""))
                    for c in context.cookies()
                    if c.get("name")
                }
            finally:
                browser.close()

    def _acquire_camoufox(
        self,
        root: str,
        *,
        timeout: int,
        headless: bool,
        proxy: str | None,
        clock: BehaviorClock,
    ) -> dict[str, str]:
        from camoufox.sync_api import Camoufox  # pyright: ignore[reportMissingImports]

        config: dict[str, Any] = {"headless": headless, "humanize": True}
        if proxy:
            config["proxy"] = {"server": proxy}
            config["geoip"] = True  # pin exit geo to the proxy, keep layers coherent
        with Camoufox(**config) as browser:
            page = browser.new_page()
            page.goto(root, wait_until="domcontentloaded", timeout=timeout * 1000)
            page.wait_for_timeout(max(250, round(clock.think_time() * 1000)))
            return {
                str(c.get("name")): str(c.get("value", ""))
                for c in page.context.cookies()
                if c.get("name")
            }

    # ── Persistence (atomic, 0600) ──────────────────────────────────────

    def _load(self) -> dict[str, WarmSession]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable warm sessions %s: %s", self._path, exc)
            return {}
        if not isinstance(raw, dict) or not isinstance(raw.get("sessions"), dict):
            return {}
        sessions: dict[str, WarmSession] = {}
        for domain, entry in raw["sessions"].items():
            if not isinstance(domain, str) or not isinstance(entry, dict):
                continue
            parsed = WarmSession.from_dict(entry)
            if parsed is not None and not parsed.expired():
                sessions[domain] = parsed
        return sessions

    def _save(self) -> None:
        payload = {
            "version": 1,
            "sessions": {d: s.to_dict() for d, s in self._sessions.items()},
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, tmp_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.", dir=self._path.parent
            )
            temporary = Path(tmp_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    if hasattr(os, "fchmod"):
                        os.fchmod(stream.fileno(), 0o600)
                    json.dump(payload, stream, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self._path)
            except OSError:
                temporary.unlink(missing_ok=True)
                raise
        except OSError as exc:
            logger.warning("Could not persist warm sessions %s: %s", self._path, exc)


def warm_crawl(
    url: str,
    *,
    warmup: SessionWarmup | None = None,
    timeout: int = 30,
    fallback_to_router: bool = True,
    **kwargs: Any,
) -> CrawlResult:
    """One-liner: warm session → cheap coherent replay → router fallback.

    1. Reuse a fresh cached session or acquire one with a real browser.
    2. Fetch with curl_cffi replaying the session (TLS+headers+cookies agree).
    3. If the replay is blocked, invalidate the session and (optionally)
       delegate to ``SmartRouter``'s full escalation ladder.
    """
    manager = warmup or SessionWarmup()
    session = manager.get(url)
    if session is None:
        session = manager.acquire(
            url,
            timeout=timeout,
            headless=bool(kwargs.get("headless", True)),
            proxy=kwargs.get("proxy"),
        )

    started = time.perf_counter()
    try:
        from curl_cffi import requests as cffi

        request: dict[str, Any] = session.curl_kwargs()
        request["timeout"] = timeout
        request["allow_redirects"] = True
        if kwargs.get("proxy"):
            request["proxy"] = kwargs["proxy"]
        response = cffi.get(url, **request)
        elapsed = (time.perf_counter() - started) * 1000

        from omk_crawl.detect import detect_block, detection_to_status

        detection = detect_block(response.text, response.status_code)
        status = detection_to_status(detection)
        if status is CrawlStatus.OK:
            return CrawlResult(
                url=url,
                status=status,
                status_code=response.status_code,
                html=response.text,
                tool="warmup+curl_cffi",
                elapsed_ms=elapsed,
                metadata={
                    "strategy": "session_replay",
                    "warm_tool": session.tool,
                    "clearance": session.has_clearance,
                    "profile": session.profile.name,
                },
            )
    except Exception as exc:  # replay failed — fall through to the ladder
        logger.info("warm_crawl replay failed for %s: %s", url, exc)

    manager.invalidate(url)
    if not fallback_to_router:
        return CrawlResult(
            url=url,
            status=CrawlStatus.BLOCKED,
            tool="warmup",
            error="session replay blocked and fallback disabled",
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
    from omk_crawl.router import SmartRouter  # lazy: avoid import cycle

    result = SmartRouter(verbose=bool(kwargs.get("verbose", False))).crawl(url, **kwargs)
    result.metadata.setdefault("warmup", "replay_blocked_fell_back")
    return result
