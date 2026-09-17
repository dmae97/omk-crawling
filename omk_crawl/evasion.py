"""EvasionPlan — one identity, six layers, decided once.

Layers in isolation are easy. What detectors actually score is whether the
layers *agree* (FP-Inconsistent arXiv:2406.07647; multi-layer web-agent
fingerprinting arXiv:2606.30119). This module is the single place where that
agreement is produced and checked, so no caller has to remember the rules:

    TLS hello (tls.py)      ─┐
    TCP stack (tcp.py)      ─┤
    HTTP headers (v2.12)    ─┼──  all derived from one FingerprintProfile,
    JS surface (browser_props)─┤   so they cannot drift apart
    CDP patches (cdp.py)    ─┤
    behavior (behavior.py)  ┘

Two properties are worth calling out:

  **Determinism is per site, not per call.** ``plan_for`` seeds everything from
  the URL (plus an optional deployment salt), so two runs against the same site
  present the *same* identity — the temporal axis detectors measure. Different
  sites get different identities, which is what keeps a fleet from looking like
  one machine.

  **Coherence is verified, not assumed.** :meth:`EvasionPlan.coherence_report`
  re-audits each layer against the profile and returns per-layer verdicts. A
  plan that cannot prove its own consistency is reported as inconsistent rather
  than shipped silently.

Guardrail (P3): this plans client discrimination for content you are authorized
to reach. It does not touch authentication — ``AUTH_REQUIRED`` is refused by the
router before a plan is ever built.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from omk_crawl.behavior import BehaviorClock
from omk_crawl.browser_props import BrowserPropertySpec, property_spec
from omk_crawl.captcha import CaptchaPlan, classify_captcha, resolve_challenge
from omk_crawl.cdp import PatchPlan, full_script, patch_plan
from omk_crawl.fingerprint import FingerprintProfile, profile_for
from omk_crawl.tcp import TcpStackProfile, audit_tcp, stack_for
from omk_crawl.tls import TlsClientHello, audit_tls, hello_for

__all__ = [
    "CoherenceReport",
    "EvasionPlan",
    "LayerStatus",
    "plan_for",
]


def _seed_for(url: str, salt: str) -> int:
    """64-bit seed derived from the **host**, so one site keeps one identity.

    Keyed on the host rather than the full URL, matching
    :func:`~omk_crawl.fingerprint.profile_for`. A client that presents different
    canvas noise on ``/a`` and ``/b`` has drifted *within a single visit* — the
    intra-session inconsistency FP-Inconsistent measures — so the identity has
    to be a property of the site, not of the page.
    """
    host = urlparse(url if "://" in url else f"//{url}").hostname or url
    material = f"omk-evasion-v1|{salt}|{host.lower()}".encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


@dataclass(frozen=True, slots=True)
class LayerStatus:
    """Coherence verdict for one layer."""

    layer: str
    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {"layer": self.layer, "ok": self.ok, "issues": list(self.issues)}


@dataclass(frozen=True, slots=True)
class CoherenceReport:
    """Aggregated cross-layer audit of a plan.

    Attributes:
        layers: One :class:`LayerStatus` per layer, in bottom-up order.
        notes: Facts that are neither pass nor fail — chiefly the tells no
            init script can remove, so the operator still sees them.
    """

    layers: tuple[LayerStatus, ...]
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return all(layer.ok for layer in self.layers)

    @property
    def failures(self) -> tuple[LayerStatus, ...]:
        return tuple(layer for layer in self.layers if not layer.ok)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "layers": [layer.to_dict() for layer in self.layers],
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class EvasionPlan:
    """The complete, self-consistent client identity for one URL.

    Build with :func:`plan_for` rather than by hand — the constructor does no
    cross-checking, so assembling layers from different profiles is possible and
    wrong.
    """

    url: str
    seed: int
    profile: FingerprintProfile
    tcp: TcpStackProfile
    hello: TlsClientHello
    cdp: PatchPlan
    props: BrowserPropertySpec
    clock: BehaviorClock

    # ── Consumption ─────────────────────────────────────────────────────

    def browser_kwargs(self) -> dict[str, Any]:
        """Playwright/patchright ``new_context`` kwargs for this identity."""
        return self.profile.browser_context_kwargs()

    def init_script(self) -> str:
        """One init script covering both the CDP and property surfaces.

        Composed through :func:`omk_crawl.cdp.full_script` so a single IIFE and a
        single ``Function.prototype.toString`` replacement are installed — two
        scripts would install it twice, which is observable.
        """
        return full_script(self.cdp, self.props)

    def captcha_policy(self, html: str | None, **kwargs: Any) -> CaptchaPlan:
        """Classify a challenge page and decide what to do about it."""
        return resolve_challenge(classify_captcha(html), self.url, **kwargs)

    def headers(self, referer: str | None = None) -> dict[str, str]:
        """The HTTP header set that matches this plan's TLS and JS layers."""
        return self.profile.headers(referer)

    def curl_kwargs(self, referer: str | None = None) -> dict[str, Any]:
        """``curl_cffi`` kwargs — real impersonated TLS plus coherent headers."""
        return self.profile.curl_kwargs(referer)

    # ── Self-check ──────────────────────────────────────────────────────

    def coherence_report(self) -> CoherenceReport:
        """Re-audit every layer against the profile.

        The checks run on the plan's own derived values, so they catch the
        failure mode that matters here: a layer that was built from a *different*
        identity, or a derived value that violates an invariant (a screen
        smaller than its own viewport, a Windows UA over a Linux stack).
        """
        layers = [
            LayerStatus("headers", tuple(self.profile.coherence_issues())),
            LayerStatus("tls", tuple(audit_tls(self.hello, self.profile))),
            # No observed values to compare, so this is purely the cross-layer
            # rule: the stack we derived must claim the OS the UA claims.
            LayerStatus("tcp", tuple(audit_tcp({}, self.tcp, self.profile))),
            LayerStatus("js", tuple(self._audit_props())),
        ]

        notes: list[str] = []
        for tell, reason in self.cdp.driver_fixed.items():
            notes.append(f"{tell}: not fixable from JS — {reason}")
        for tell, reason in self.cdp.unpatchable.items():
            notes.append(f"{tell}: {reason}")

        return CoherenceReport(layers=tuple(layers), notes=tuple(notes))

    def _audit_props(self) -> list[str]:
        """Audit the derived JS surface against the profile.

        Imports the audit lazily through the module to keep the dataclass
        import-light, and builds an observation map from the spec itself — which
        is exactly how a construction invariant gets caught.
        """
        from omk_crawl.browser_props import audit_props

        observed = {
            "platform": self.props.platform,
            "languages": list(self.props.languages),
            "timezone": self.props.timezone,
            "screen": list(self.props.screen),
            "avail": list(self.props.avail),
            "viewport": list(self.profile.viewport),
            "device_pixel_ratio": self.props.device_pixel_ratio,
            "max_touch_points": self.props.max_touch_points,
            "webgl_renderer": self.props.webgl.unmasked_renderer,
        }
        return audit_props(observed, self.profile)

    # ─ Metadata ────────────────────────────────────────────────────────

    def as_metadata(self) -> dict[str, Any]:
        """Compact summary for ``CrawlResult.metadata`` — no JS, JSON-safe."""
        return {
            "profile": self.profile.name,
            "seed": self.seed,
            "ja3": self.hello.ja3(),
            "tcp_stack": self.tcp.name,
            "cdp_covered": len(self.cdp.covered_tells()),
            "cdp_strategy": self.cdp.strategy,
            "coherent": self.coherence_report().ok,
        }

    def describe(self) -> str:
        """Human-readable multi-line summary for the CLI."""
        report = self.coherence_report()
        lines = [
            f"url       {self.url}",
            f"profile   {self.profile.name}  ({self.profile.family}, {self.profile.platform_os})",
            f"seed      {self.seed}",
            f"tls       {self.hello.ja4_model()}  ja3={self.hello.ja3()}",
            f"tcp       {self.tcp.name}  ttl={self.tcp.ttl} window={self.tcp.window} "
            f"opts={','.join(self.tcp.option_names())}",
            f"js        platform={self.props.platform} tz={self.props.timezone}",
            f"screen    {self.props.screen[0]}x{self.props.screen[1]} "
            f"avail={self.props.avail[0]}x{self.props.avail[1]} "
            f"dpr={self.props.device_pixel_ratio}",
            f"cdp       {len(self.cdp.covered_tells())} tells covered "
            f"(strategy={self.cdp.strategy}), {len(self.cdp.driver_fixed)} driver-fixed",
            f"coherent  {'yes' if report.ok else 'NO'}",
        ]
        for layer in report.failures:
            for issue in layer.issues:
                lines.append(f"  ! {layer.layer}: {issue}")
        for note in report.notes:
            lines.append(f"  - {note}")
        return "\n".join(lines)


def plan_for(url: str, seed: int | None = None, salt: str = "") -> EvasionPlan:
    """Build the six-layer plan for ``url``.

    Deterministic in ``url`` and ``salt``: the same site always yields the same
    profile, TCP stack, TLS model, patch plan, property surface and behavior
    seed. Passing an explicit ``seed`` overrides that for one-off experiments
    (reproducing a support ticket, for instance) at the cost of the per-site
    stability that detectors reward.
    """
    profile = profile_for(url, salt=salt)
    seed_value = _seed_for(url, salt) if seed is None else seed & 0xFFFFFFFFFFFFFFFF
    return EvasionPlan(
        url=url,
        seed=seed_value,
        profile=profile,
        tcp=stack_for(profile),
        hello=hello_for(profile),
        cdp=patch_plan(profile, seed_value),
        props=property_spec(profile, seed_value),
        # Also keyed on the site seed, so pacing is stable across the whole visit
        # rather than restarting with fresh entropy on every page.
        clock=BehaviorClock(f"omk-behavior|{seed_value}"),
    )
