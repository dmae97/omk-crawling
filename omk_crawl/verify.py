"""Offline anti-bot detector simulator — check the configuration before the site does.

Every evasion layer in this package is only worth what a detector would make of
it, and finding out against a live production site is both slow and rude. This
module is a *mock detector*: it scores a request surface against the checks real
vendors are documented to run, entirely offline and deterministically.

It is shipped as product code rather than test scaffolding because the operator
is the main consumer — "would my current setup look like a bot?" is a question
worth answering in a second, without a network.

What it checks, and why each one earns its place:

===================  ====  ====================================================
check                w     what it models
===================  ====  ====================================================
header_coherence     3     UA vs Client Hints vs platform vs TLS family
tls_family           3     GREASE / ALPN / TLS1.3 vs the claimed family
tcp_os               2     SYN signature vs the OS the UA claims
js_surface           3     navigator/screen/WebGL vs the claimed identity
cdp_surface          3     surviving automation tells
behavior_cadence     2     constant cadence is the clearest bot signature
challenge_policy     1     interactive challenges refused rather than faked
===================  ====  ====================================================

The score is a weighted pass ratio, not a probability of being blocked — no
offline model can honestly claim the latter (P1). It is a *relative* signal for
comparing configurations, which is exactly how :mod:`bench_evasion` uses it.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from omk_crawl.browser_props import audit_props
from omk_crawl.captcha import CaptchaKind, classify_captcha, resolve_challenge
from omk_crawl.cdp import audit_cdp
from omk_crawl.fingerprint import coherence_issues
from omk_crawl.tls import audit_tls

__all__ = [
    "CHECKS",
    "BehaviorSummary",
    "Check",
    "RequestSurface",
    "Verdict",
    "evasion_surface",
    "naive_stealth_surface",
    "score",
    "stock_surface",
]


@dataclass(frozen=True, slots=True)
class Check:
    """One detection axis.

    Attributes:
        key: Stable identifier, reported in verdicts.
        weight: Relative importance; three times a minor tell.
        description: What the check models.
        run: ``surface -> issues``; empty list means the check passed.
    """

    key: str
    weight: int
    description: str
    run: Callable[[RequestSurface], list[str]]


@dataclass(frozen=True, slots=True)
class BehaviorSummary:
    """Interaction timings to score for human plausibility.

    Attributes:
        think_times: Pauses between actions, in seconds.
        inter_request: Gaps between page requests, in seconds.
    """

    think_times: tuple[float, ...] = ()
    inter_request: tuple[float, ...] = ()

    @staticmethod
    def _cv(values: tuple[float, ...]) -> float | None:
        """Coefficient of variation; None when there is nothing to judge."""
        if len(values) < 3:
            return None
        mean = statistics.fmean(values)
        if mean <= 0:
            return None
        return statistics.pstdev(values) / mean

    def issues(self, min_cv: float = 0.18) -> list[str]:
        """Flag machine-like cadence: too little variation, or none at all."""
        issues: list[str] = []
        for label, values in (
            ("inter-request delays", self.inter_request),
            ("think times", self.think_times),
        ):
            cv = self._cv(values)
            if cv is None:
                continue
            if cv < min_cv:
                issues.append(
                    f"{label} vary by only {cv:.3f} (cv) — a metronome cadence is a bot tell"
                )
        if self.inter_request and min(self.inter_request) <= 0:
            issues.append("inter-request delays include a zero gap (unthrottled burst)")
        return issues


@dataclass(slots=True)
class RequestSurface:
    """Everything a detector could observe about one request.

    Every field is optional so a partial surface (or a mock one in a test) still
    produces a meaningful verdict; a missing field is simply not scored.
    """

    url: str = "https://example.com"
    headers: Mapping[str, str] = field(default_factory=dict)
    impersonate: str | None = None
    tls: Any = None
    tcp: Mapping[str, Any] | None = None
    profile_os: str | None = None
    js: Mapping[str, Any] | None = None
    cdp: Mapping[str, Any] | None = None
    behavior: BehaviorSummary | None = None
    challenge_html: str | None = None


# ── Checks ────────────────────────────────────────────────────────────────


# A check that could not run is not evidence of evasion, but scoring it as a
# pass would inflate the score. Checks prefix such messages with this marker so
# `score` can separate "passed" from "could not be judged" by construction,
# rather than by pattern-matching English.
_UNSCORED = "unscored: "


def _check_headers(surface: RequestSurface) -> list[str]:
    if not surface.headers:
        return [_UNSCORED + "no HTTP headers presented — nothing claims to be a browser"]
    return list(coherence_issues(dict(surface.headers), impersonate=surface.impersonate))


def _check_tls(surface: RequestSurface) -> list[str]:
    if surface.tls is None:
        return [_UNSCORED + "no TLS ClientHello model — handshake family cannot be verified"]
    family = "chrome"
    ua = str(surface.headers.get("User-Agent", "")) if surface.headers else ""
    if "Firefox/" in ua:
        family = "firefox"
    elif "Safari/" in ua and "Chrome/" not in ua:
        family = "safari"

    class _Stub:
        """Minimal profile stand-in: the audit only reads `family`."""

        def __init__(self, family: str) -> None:
            self.family = family

    return list(audit_tls(surface.tls, _Stub(family)))  # type: ignore[arg-type]


def _check_tcp(surface: RequestSurface) -> list[str]:
    if surface.tcp is None:
        return [_UNSCORED + "no TCP signature presented"]
    issues: list[str] = []
    observed = dict(surface.tcp)
    if surface.profile_os and "os_family" in observed:
        if str(observed["os_family"]).lower() != surface.profile_os.lower():
            issues.append(
                f"TCP stack is {observed['os_family']} but the UA claims {surface.profile_os}"
            )
    # The classic passive-fingerprint contradictions, checked without a spec.
    if surface.profile_os == "Windows" and observed.get("ttl") not in (128, 127, 126):
        issues.append(f"TTL {observed.get('ttl')} is not Windows-like (128)")
    if surface.profile_os in ("Linux", "macOS", "Android", "iOS") and observed.get("ttl") not in (
        64,
        63,
        62,
    ):
        issues.append(f"TTL {observed.get('ttl')} is not Unix-like (64)")
    options = observed.get("options")
    if options is not None:
        if 2 not in options:
            issues.append("SYN carries no MSS option — no real OS omits it")
        if 3 not in options:
            issues.append("SYN carries no window-scale option — implausible for a modern OS")
    return issues


def _check_js(surface: RequestSurface) -> list[str]:
    if surface.js is None:
        return [_UNSCORED + "no JS surface observed — the page would see raw browser defaults"]
    if "error" in surface.js:
        return [_UNSCORED + f"JS probe failed: {surface.js['error']}"]

    class _Stub:
        """Profile stand-in carrying only what audit_props reads."""

        def __init__(self, os_name: str) -> None:
            self.platform_os = os_name
            self.locale = "ko-KR"
            self.timezone_id = "Asia/Seoul"
            self.viewport = (1920, 1080)
            self.mobile = False
            self.name = "stub"

    return list(audit_props(surface.js, _Stub(surface.profile_os or "Windows")))  # type: ignore[arg-type]


def _check_cdp(surface: RequestSurface) -> list[str]:
    if surface.cdp is None:
        return [_UNSCORED + "CDP surface not probed — automation tells unverified"]
    leaks = audit_cdp(surface.cdp)
    return [f"{leak.key} ({leak.severity}): {leak.why}" for leak in leaks]


def _check_behavior(surface: RequestSurface) -> list[str]:
    if surface.behavior is None:
        return [_UNSCORED + "no interaction timing observed — cadence unscored"]
    return surface.behavior.issues()


def _check_challenge(surface: RequestSurface) -> list[str]:
    if surface.challenge_html is None:
        return []
    challenge = classify_captcha(surface.challenge_html)
    if challenge.kind is CaptchaKind.NONE:
        return []
    plan = resolve_challenge(challenge, surface.url)
    # This ordering matters: an unrefused interactive challenge is a policy
    # regression and is reported as such, not as an ordinary failure.
    if challenge.requires_human and plan.action != "refuse":
        return ["policy regression: an interactive challenge was not refused"]
    if plan.action == "refuse" and not challenge.requires_human:
        return [f"{challenge.kind.value}: no viable path configured ({plan.reason})"]
    return []


CHECKS: tuple[Check, ...] = (
    Check("header_coherence", 3, "UA vs Client Hints vs platform vs TLS family", _check_headers),
    Check("tls_family", 3, "GREASE/ALPN/TLS1.3 vs the claimed browser family", _check_tls),
    Check("tcp_os", 2, "SYN signature vs the OS the UA claims", _check_tcp),
    Check("js_surface", 3, "navigator/screen/WebGL vs the claimed identity", _check_js),
    Check("cdp_surface", 3, "surviving automation tells", _check_cdp),
    Check("behavior_cadence", 2, "constant cadence and unthrottled bursts", _check_behavior),
    Check(
        "challenge_policy",
        1,
        "interactive challenges refused rather than faked",
        _check_challenge,
    ),
)

_TOTAL_WEIGHT = sum(check.weight for check in CHECKS)


@dataclass(frozen=True, slots=True)
class Verdict:
    """Result of scoring one surface.

    Attributes:
        score: Weighted pass ratio in ``[0, 1]``; 1.0 means every scored check
            passed. A *relative* signal, not a probability of being blocked.
        detected: Keys of the checks that failed.
        passed: Keys of the checks that passed.
        unscored: Keys skipped because the surface lacked the input.
        details: Per-check issue text, for the checks that failed.
    """

    score: float
    detected: tuple[str, ...]
    passed: tuple[str, ...]
    unscored: tuple[str, ...]
    details: dict[str, list[str]]

    @property
    def clean(self) -> bool:
        return not self.detected

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "detected": list(self.detected),
            "passed": list(self.passed),
            "unscored": list(self.unscored),
            "details": self.details,
        }


# A check that could not run is not evidence of evasion, but scoring it as a
# pass would inflate the number. These sentinels let `score` tell "passed" from
# "could not be judged".


def score(surface: RequestSurface) -> Verdict:
    """Score a surface against every check. Never raises."""
    detected: list[str] = []
    passed: list[str] = []
    unscored: list[str] = []
    details: dict[str, list[str]] = {}
    earned = 0

    for check in CHECKS:
        try:
            issues = check.run(surface)
        except Exception as exc:  # a mock detector must never break the caller
            issues = [f"check raised {type(exc).__name__}: {exc}"]

        if issues and all(issue.startswith(_UNSCORED) for issue in issues):
            unscored.append(check.key)
            continue
        if issues:
            detected.append(check.key)
            details[check.key] = issues
        else:
            passed.append(check.key)
            earned += check.weight

    judged = _TOTAL_WEIGHT - sum(
        check.weight for check in CHECKS if check.key in unscored
    )
    return Verdict(
        score=(earned / judged) if judged else 0.0,
        detected=tuple(detected),
        passed=tuple(passed),
        unscored=tuple(unscored),
        details=details,
    )


# ── Reference surfaces ────────────────────────────────────────────────────
# Three configurations the benchmark compares. They are the honest spectrum:
# a plain HTTP client, the "glued-on stealth" mistake the research warns about,
# and the coherent plan this package produces.


def stock_surface(url: str = "https://example.com") -> RequestSurface:
    """A plain library client: no browser pretence, no stealth, no pacing."""
    from omk_crawl.tls import TlsClientHello

    return RequestSurface(
        url=url,
        headers={
            "User-Agent": "python-requests/2.32.3",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
        },
        tls=TlsClientHello(
            cipher_suites=(0xC02F, 0xC030, 0x009C),
            extensions=(0x0000, 0x000A, 0x000B),
            elliptic_curves=(0x0017, 0x0018),
            alpn=(),
            tls13=False,
        ),
        tcp={"ttl": 64, "os_family": "Linux", "options": (2, 4, 8, 1, 3)},
        profile_os="Linux",
        js=None,
        cdp={"webdriver": False},
        behavior=BehaviorSummary(
            think_times=(0.0, 0.0, 0.0, 0.0, 0.0),
            inter_request=(0.0, 0.0, 0.0, 0.0, 0.0),
        ),
    )


def naive_stealth_surface(url: str = "https://example.com") -> RequestSurface:
    """The documented worst practice: browser-shaped headers glued onto a
    non-browser stack, with superficial JS patches and a fixed sleep().

    Multi-layer web-agent fingerprinting (arXiv:2606.30119) found this pattern
    is *more* detectable than an honest client, because every disagreement is a
    signal. The benchmark exists partly to demonstrate that.
    """
    from omk_crawl.tls import TlsClientHello

    return RequestSurface(
        url=url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            # Client Hints omitted while using a Chromium UA, and the TLS target
            # is Firefox: two contradictions in one header set.
            "Accept-Language": "en-US,en;q=0.9",
        },
        impersonate="firefox133",
        tls=TlsClientHello(
            cipher_suites=(0x1301, 0x1303, 0xC02B),
            extensions=(0x0000, 0x000A, 0x000B, 0x0010),
            elliptic_curves=(0x001D, 0x0017),
            alpn=("h2", "http/1.1"),
            tls13=True,
        ),
        tcp={"ttl": 64, "os_family": "Linux", "options": (2, 4, 8, 1, 3)},
        profile_os="Windows",
        js={
            "platform": "Linux x86_64",
            "timezone": "UTC",
            "languages": ["en-US"],
            "screen": [1280, 720],
            "viewport": [1920, 1080],
            "webgl_renderer": (
                "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device), SwiftShader driver)"
            ),
        },
        cdp={"webdriver": True, "plugins_empty": True, "chrome_runtime_missing": True},
        behavior=BehaviorSummary(
            think_times=(1.0,) * 8,
            inter_request=(1.0,) * 8,
        ),
    )


def evasion_surface(url: str = "https://example.com", seed: int | None = None) -> RequestSurface:
    """The surface produced by this package's own :func:`plan_for`."""
    from omk_crawl.evasion import plan_for

    plan = plan_for(url, seed=seed)
    spec = plan.props
    return RequestSurface(
        url=url,
        headers={**plan.headers(), "User-Agent": plan.profile.user_agent},
        impersonate=plan.profile.impersonate,
        tls=plan.hello,
        tcp={
            "ttl": plan.tcp.ttl,
            "window": plan.tcp.window,
            "mss": plan.tcp.mss,
            "os_family": plan.tcp.os_family,
            "options": plan.tcp.options,
        },
        profile_os=plan.profile.platform_os,
        js={
            "platform": spec.platform,
            "timezone": spec.timezone,
            "languages": list(spec.languages),
            "screen": list(spec.screen),
            "avail": list(spec.avail),
            "viewport": list(plan.profile.viewport),
            "device_pixel_ratio": spec.device_pixel_ratio,
            "max_touch_points": spec.max_touch_points,
            "webgl_renderer": spec.webgl.unmasked_renderer,
            "plugins_length": 5,
            "pdf_viewer_enabled": True,
        },
        # What the composed init script leaves behind: nothing observable.
        cdp={tell.key: False for tell in _all_tell_keys()},
        behavior=BehaviorSummary(
            think_times=tuple(plan.clock.think_time() for _ in range(12)),
            inter_request=tuple(plan.clock.inter_request_delay(1.0) for _ in range(12)),
        ),
    )


def _all_tell_keys() -> tuple[Any, ...]:
    """Every probe key, so a clean surface is built from the registry itself."""
    from omk_crawl.cdp import CDP_TELLS

    return CDP_TELLS
