"""CAPTCHA classification and solver policy.

v2.12 scoped CAPTCHA-solver SaaS integration out and left only an extension
point. This module is that extension point made concrete — and, more usefully,
the *decision layer* that was missing: when a crawl lands on a challenge page,
something has to decide whether this is a flow a real browser can pass, a job
for a configured solver, or a job for nobody.

Three guardrails are structural, not advisory:

  **Human-judgement challenges are refused, never delegated.** An image grid, a
  slider, or a press-and-hold challenge exists precisely to require a person.
  :func:`resolve_challenge` returns ``refuse`` for them and no backend is
  consulted, so a misconfigured solver cannot quietly turn this module into a
  CAPTCHA-defeating tool.

  **No credentials in source.** :class:`HttpSolver` is inert until
  ``OMK_CAPTCHA_ENDPOINT`` and ``OMK_CAPTCHA_KEY`` are both set in the
  environment; without them it fails closed with ``NO_CREDENTIAL`` (P2). There
  is no default endpoint and no bundled key.

  **Clearance before purchase.** A challenge that a real browser can clear is
  routed to the warm-session flow (``clearance_flow``), which costs nothing,
  sends nothing to a third party, and reuses the session machinery already in
  :mod:`omk_crawl.warmup`. A solver is only reached when the operator has opted
  in *and* asked for it.

Authorized access only (P3): nothing here bypasses authentication. A login wall
is ``AUTH_REQUIRED`` and stays out of scope, exactly as it does in the router.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "CaptchaChallenge",
    "CaptchaKind",
    "CaptchaPlan",
    "HttpSolver",
    "MockSolver",
    "NullSolver",
    "SolveResult",
    "SolveStatus",
    "SolverBackend",
    "classify_captcha",
    "resolve_challenge",
]

_ENDPOINT_ENV = "OMK_CAPTCHA_ENDPOINT"
_KEY_ENV = "OMK_CAPTCHA_KEY"


class CaptchaKind(str, Enum):
    """A challenge family, keyed by who produces it.

    ``NONE`` and ``UNKNOWN`` are deliberately distinct. A page with no challenge
    at all must not be refused, while a page that *looks* like a challenge we
    cannot name must be — collapsing them into one value would either block
    ordinary pages or quietly automate something unrecognized.
    """

    NONE = "none"
    TURNSTILE = "turnstile"
    RECAPTCHA_V2 = "recaptcha_v2"
    RECAPTCHA_V3 = "recaptcha_v3"
    HCAPTCHA = "hcaptcha"
    DATADOME = "datadome"
    KASADA = "kasada"
    AWS_WAF = "aws_waf"
    IMAGE_GRID = "image_grid"
    SLIDER = "slider"
    PRESS_HOLD = "press_hold"
    UNKNOWN = "unknown"


class SolveStatus(str, Enum):
    """Outcome of an attempt to produce a token."""

    SOLVED = "solved"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    NO_CREDENTIAL = "no_credential"
    REFUSED = "refused"


# Neither a challenge nor something we can classify as one: nothing to do.
_NO_CHALLENGE: frozenset[CaptchaKind] = frozenset({CaptchaKind.NONE})

# Kinds a person must resolve. Kept as a single frozen set so the refusal rule
# has exactly one definition. UNKNOWN is here on purpose: a challenge we cannot
# name is a challenge we do not automate (fail closed, P2).
_REQUIRES_HUMAN: frozenset[CaptchaKind] = frozenset(
    {
        CaptchaKind.IMAGE_GRID,
        CaptchaKind.SLIDER,
        CaptchaKind.PRESS_HOLD,
        CaptchaKind.UNKNOWN,
    }
)

# Kinds a real browser session can clear on its own (harvested as a clearance
# cookie by warmup.SessionWarmup) without any human and without a third party.
_CLEARABLE: frozenset[CaptchaKind] = frozenset(
    {
        CaptchaKind.TURNSTILE,
        CaptchaKind.RECAPTCHA_V3,
        CaptchaKind.DATADOME,
        CaptchaKind.KASADA,
        CaptchaKind.AWS_WAF,
    }
)

# Marker -> kind. Ordered: the first match wins, so the specific interactive
# widgets are tested before the generic vendor scripts that embed them.
_MARKERS: tuple[tuple[CaptchaKind, tuple[str, ...]], ...] = (
    (
        CaptchaKind.IMAGE_GRID,
        ("rc-imageselect", "imageselect-table", "challenge-container",
         "select all images", "select all squares"),
    ),
    (
        CaptchaKind.PRESS_HOLD,
        ("px-captcha", "press and hold", "press_hold", "hold to confirm"),
    ),
    (
        CaptchaKind.SLIDER,
        ("geetest_slider", "nc_1_n1z", "yidun_intelli", "slidercaptcha",
         "drag the slider", "slide to verify"),
    ),
    (
        CaptchaKind.TURNSTILE,
        ("challenges.cloudflare.com/turnstile", "cf-turnstile", "turnstile.render"),
    ),
    (
        CaptchaKind.RECAPTCHA_V2,
        ("g-recaptcha", "www.google.com/recaptcha/api.js", "recaptcha/api/js"),
    ),
    (
        CaptchaKind.RECAPTCHA_V3,
        ("grecaptcha.execute", "recaptcha/api.js?render=", "grecaptcha.ready"),
    ),
    (
        CaptchaKind.HCAPTCHA,
        ("hcaptcha.com/1/api.js", "h-captcha", "hcaptcha.render"),
    ),
    (
        CaptchaKind.DATADOME,
        ("geo.captcha-delivery.com", "datadome", "dd_captcha"),
    ),
    (CaptchaKind.KASADA, ("x-kpsdk", "/ips.js")),
    (
        CaptchaKind.AWS_WAF,
        ("awswafcookiedomainlist", "aws-waf-token", "challenge.js",
         "awswaf", "captcha.awswaf.com"),
    ),
)

# Generic "you are being challenged" hints. A page matching one of these is a
# real challenge we could not name — UNKNOWN, and therefore refused — rather than
# an ordinary page. Without this split, every blog post would look like an
# unclassifiable challenge and the policy layer would refuse the open web.
_CHALLENGE_HINTS: tuple[str, ...] = (
    "captcha",
    "challenge-platform",
    "cf-chl",
    "just a moment",
    "verify you are human",
    "checking your browser",
    "enable javascript and cookies to continue",
    "are you a robot",
    "unusual traffic",
    "bot detection",
    "complete the security check",
)

_SITEKEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'data-sitekey=["\']([A-Za-z0-9_\-]{8,})["\']'),
    re.compile(r'sitekey["\']?\s*[:=]\s*["\']([A-Za-z0-9_\-]{8,})["\']'),
    re.compile(r'render=([A-Za-z0-9_\-]{8,})'),
)

_ACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'action["\']?\s*[:=]\s*["\']([A-Za-z0-9_\-]{1,64})["\']'),
    re.compile(r'data-action=["\']([A-Za-z0-9_\-]{1,64})["\']'),
)


def _first_match(html: str, patterns: tuple[re.Pattern[str], ...]) -> str | None:
    """First group of the first pattern that matches, or None."""
    for pattern in patterns:
        match = pattern.search(html)
        if match:
            return match.group(1)
    return None


@dataclass(frozen=True, slots=True)
class CaptchaChallenge:
    """A classified challenge.

    Attributes:
        kind: Recognized family (``UNKNOWN`` when nothing matched).
        sitekey: Widget site key when the page exposes one.
        action: reCAPTCHA v3 action, when present.
        requires_human: Whether the challenge is designed to need a person.
            Structurally derived from ``kind`` — never guessed per page.
        resoluble_by_clearance: Whether a real browser session can pass it on
            its own and yield a reusable clearance cookie.
        confidence: ``"high"`` when a marker matched, ``"low"`` for ``UNKNOWN``.
        evidence: The markers that fired, for operator diagnosis.
    """

    kind: CaptchaKind
    sitekey: str | None = None
    action: str | None = None
    requires_human: bool = False
    resoluble_by_clearance: bool = False
    confidence: str = "low"
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "sitekey": self.sitekey,
            "action": self.action,
            "requires_human": self.requires_human,
            "resoluble_by_clearance": self.resoluble_by_clearance,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
        }


def classify_captcha(html: str | None) -> CaptchaChallenge:
    """Classify a challenge page.

    Never raises. Three outcomes, not two:

      * a **named kind** when a vendor marker matched;
      * **UNKNOWN** when the page carries generic challenge language we cannot
        attribute — treated as a challenge and refused rather than guessed at;
      * **NONE** when nothing suggests a challenge, so the caller can proceed.

    ``None`` and empty input yield ``NONE``: absence of a body is not evidence of
    a challenge.
    """
    if not html:
        return CaptchaChallenge(kind=CaptchaKind.NONE, confidence="high")

    lowered = html.lower()
    for kind, markers in _MARKERS:
        hits = tuple(marker for marker in markers if marker in lowered)
        if not hits:
            continue
        # An image grid can appear *inside* a reCAPTCHA/hCaptcha frame; the
        # interactive widget is the thing a person has to solve, so when one is
        # present it wins regardless of which vendor script embedded it.
        return CaptchaChallenge(
            kind=kind,
            sitekey=_first_match(html, _SITEKEY_PATTERNS),
            action=_first_match(html, _ACTION_PATTERNS),
            requires_human=kind in _REQUIRES_HUMAN,
            resoluble_by_clearance=kind in _CLEARABLE,
            confidence="high",
            evidence=hits,
        )

    hints = tuple(hint for hint in _CHALLENGE_HINTS if hint in lowered)
    if hints:
        return CaptchaChallenge(
            kind=CaptchaKind.UNKNOWN, requires_human=True, confidence="low", evidence=hints
        )

    return CaptchaChallenge(kind=CaptchaKind.NONE, confidence="high")


@dataclass(frozen=True, slots=True)
class SolveResult:
    """What a solver backend produced. Failure is data, not an exception."""

    status: SolveStatus
    backend: str
    token: str | None = None
    cookies: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    cost_estimate: float | None = None

    @property
    def ok(self) -> bool:
        return self.status is SolveStatus.SOLVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "backend": self.backend,
            "has_token": bool(self.token),
            "cookies": sorted(self.cookies),
            "error": self.error,
            "cost_estimate": self.cost_estimate,
        }


class SolverBackend:
    """Contract for a token source.

    Implementations must not raise from :meth:`solve`; a missing credential or
    an unreachable endpoint is reported through :class:`SolveResult` (P5: the
    same rule the tool adapters follow).
    """

    name: str = "solver"
    #: Kinds this backend is willing to attempt. Human-judgement kinds must not
    #: appear here — the policy layer refuses them before a backend is asked.
    supports_kinds: frozenset[CaptchaKind] = frozenset(_CLEARABLE)

    def available(self) -> bool:
        """Whether the backend is configured well enough to be attempted."""
        return True

    def supports(self, kind: CaptchaKind) -> bool:
        return kind in self.supports_kinds

    def solve(self, challenge: CaptchaChallenge, url: str, **kwargs: Any) -> SolveResult:
        """Produce a token. Must not raise."""
        raise NotImplementedError

    def _unsupported(self, challenge: CaptchaChallenge) -> SolveResult:
        return SolveResult(
            status=SolveStatus.UNSUPPORTED,
            backend=self.name,
            error=f"{self.name} does not handle {challenge.kind.value}",
        )

    def _no_credential(self, detail: str) -> SolveResult:
        return SolveResult(status=SolveStatus.NO_CREDENTIAL, backend=self.name, error=detail)


class NullSolver(SolverBackend):
    """The default: deliberately inert, and loudly so.

    A crawl that reaches this backend is told *why* nothing happened rather than
    silently continuing as if the challenge did not exist.
    """

    name = "null"

    def available(self) -> bool:
        return False

    def solve(self, challenge: CaptchaChallenge, url: str, **kwargs: Any) -> SolveResult:
        return self._no_credential(
            "no solver configured; set "
            f"{_ENDPOINT_ENV}/{_KEY_ENV} and pass prefer_solver=True to opt in"
        )


class MockSolver(SolverBackend):
    """Deterministic offline backend for tests and benchmarks.

    The token is derived from the challenge so runs are reproducible, and it is
    obviously synthetic (``mock-`` prefixed) so it can never be mistaken for a
    real one in a result payload.
    """

    name = "mock"

    def __init__(self, token_prefix: str = "mock", fail_kinds: Iterable[CaptchaKind] = ()) -> None:
        self._prefix = token_prefix
        self._fail = frozenset(fail_kinds)

    def solve(self, challenge: CaptchaChallenge, url: str, **kwargs: Any) -> SolveResult:
        if not self.supports(challenge.kind):
            return self._unsupported(challenge)
        if challenge.kind in self._fail:
            return SolveResult(
                status=SolveStatus.FAILED, backend=self.name, error="mock failure requested"
            )
        seed = f"{challenge.kind.value}|{challenge.sitekey}|{url}"
        # sha256, not hash(): str hashing is salted per process, which would make
        # the token differ between runs and break the determinism both the tests
        # and the benchmark rely on.
        token = f"{self._prefix}-{hashlib.sha256(seed.encode()).hexdigest()[:8]}"
        return SolveResult(
            status=SolveStatus.SOLVED,
            backend=self.name,
            token=token,
            cookies={f"{challenge.kind.value}_clearance": token},
            cost_estimate=0.0,
        )


class HttpSolver(SolverBackend):
    """Operator-supplied solver endpoint, gated on environment credentials.

    Inert unless both ``OMK_CAPTCHA_ENDPOINT`` and ``OMK_CAPTCHA_KEY`` are set
    (P2). Uses ``urllib`` from the standard library so the core stays zero-dep
    (P4). Any transport or protocol failure becomes a FAILED result — never an
    exception, and never a fallback to a bundled service.
    """

    name = "http"

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        # Read from the environment at call time, not import time, so a test can
        # set them without reloading the module — and so nothing is cached in a
        # long-lived process after an operator revokes a key.
        self._endpoint = endpoint
        self._api_key = api_key
        self.timeout = timeout

    def _config(self) -> tuple[str | None, str | None]:
        endpoint = self._endpoint or os.environ.get(_ENDPOINT_ENV)
        api_key = self._api_key or os.environ.get(_KEY_ENV)
        return endpoint, api_key

    def available(self) -> bool:
        endpoint, api_key = self._config()
        return bool(endpoint and api_key)

    def solve(self, challenge: CaptchaChallenge, url: str, **kwargs: Any) -> SolveResult:
        if not self.supports(challenge.kind):
            return self._unsupported(challenge)
        endpoint, api_key = self._config()
        if not endpoint or not api_key:
            return self._no_credential(
                f"{_ENDPOINT_ENV} and {_KEY_ENV} must both be set to use the http solver"
            )

        payload = json.dumps(
            {
                "kind": challenge.kind.value,
                "sitekey": challenge.sitekey,
                "action": challenge.action,
                "url": url,
            }
        ).encode()
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8", "replace") or "{}")
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            return SolveResult(
                status=SolveStatus.FAILED,
                backend=self.name,
                error=f"{type(exc).__name__}: {exc}",
            )

        if not isinstance(body, dict):
            return SolveResult(
                status=SolveStatus.FAILED, backend=self.name, error="solver returned non-object"
            )
        token = body.get("token")
        if not token:
            return SolveResult(
                status=SolveStatus.FAILED,
                backend=self.name,
                error=str(body.get("error") or "solver returned no token"),
            )
        cookies = body.get("cookies")
        return SolveResult(
            status=SolveStatus.SOLVED,
            backend=self.name,
            token=str(token),
            cookies=(
                {str(k): str(v) for k, v in cookies.items()}
                if isinstance(cookies, dict)
                else {}
            ),
            cost_estimate=body.get("cost") if isinstance(body.get("cost"), (int, float)) else None,
        )


DEFAULT_BACKENDS: tuple[SolverBackend, ...] = (NullSolver(), HttpSolver())


@dataclass(frozen=True, slots=True)
class CaptchaPlan:
    """What to do about a challenge.

    Attributes:
        action: ``"proceed"`` (no challenge), ``"clearance_flow"`` (a real
            browser session clears it), ``"solver"`` (a configured backend is
            asked), or ``"refuse"``.
        reason: Human-readable justification — always populated, because a
            refusal that does not say why is indistinguishable from a bug.
        challenge: The classification this decision was made from.
        backend: The chosen backend's name, when ``action == "solver"``.
    """

    action: str
    reason: str
    challenge: CaptchaChallenge
    backend: str | None = None

    @property
    def refused(self) -> bool:
        return self.action == "refuse"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "backend": self.backend,
            "challenge": self.challenge.to_dict(),
        }


def resolve_challenge(
    challenge: CaptchaChallenge,
    url: str,
    backends: Iterable[SolverBackend] | None = None,
    prefer_solver: bool = False,
) -> CaptchaPlan:
    """Decide how to handle ``challenge``.

    Order of decision, and why:

      1. **No challenge at all proceeds.** ``NONE`` is not a decision point.
      2. **Human-judgement kinds refuse immediately.** No backend is consulted,
         so no configuration mistake can route an image grid to a solver.
      3. **Clearance-capable kinds default to the browser flow.** It costs
         nothing, involves no third party, and reuses the warm-session machinery
         that already exists. A solver is used only when the operator passes
         ``prefer_solver=True`` *and* a backend is actually available.
      4. **Anything else refuses**, with the reason recorded.
    """
    if challenge.kind in _NO_CHALLENGE:
        return CaptchaPlan(
            action="proceed",
            reason="no challenge detected on the page",
            challenge=challenge,
        )

    if challenge.requires_human:
        return CaptchaPlan(
            action="refuse",
            reason=(
                f"{challenge.kind.value} requires human judgement; this module does not "
                "automate interactive challenges"
            ),
            challenge=challenge,
        )

    candidates = backends if backends is not None else DEFAULT_BACKENDS
    available = [b for b in candidates if b.available()]
    solver = next((b for b in available if b.supports(challenge.kind)), None)

    if prefer_solver and solver is not None:
        return CaptchaPlan(
            action="solver",
            reason=f"operator opted into the {solver.name} backend for {challenge.kind.value}",
            challenge=challenge,
            backend=solver.name,
        )

    if challenge.resoluble_by_clearance:
        reason = f"{challenge.kind.value} is clearable by a real browser session"
        if prefer_solver and solver is None:
            reason += "; no configured backend supports it, falling back to clearance"
        return CaptchaPlan(action="clearance_flow", reason=reason, challenge=challenge)

    if solver is not None:
        return CaptchaPlan(
            action="solver",
            reason=f"{challenge.kind.value} is not clearable in-browser; using {solver.name}",
            challenge=challenge,
            backend=solver.name,
        )

    return CaptchaPlan(
        action="refuse",
        reason=f"{challenge.kind.value} is neither clearable nor supported by a configured backend",
        challenge=challenge,
    )
