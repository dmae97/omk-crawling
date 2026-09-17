"""CDP surface patching — making the automation layer stop announcing itself.

Driving a browser over the Chrome DevTools Protocol leaves artifacts that have
nothing to do with TLS, headers or behavior: `navigator.webdriver`, chromedriver's
`cdc_*` globals, Playwright/Puppeteer binding names, `Runtime.enable` side
effects in `Error.stack`, headless plugin counts. patchright and nodriver exist
precisely to remove these — but nothing in this project ever *checked* whether
they were removed, or in what order they should be removed.

Two rules drive the design:

  **A patch is a detection vector unless it looks native.** Anything installed
  from a script must survive `Function.prototype.toString` — a replaced
  `navigator.permissions.query` whose source is visible is *worse* than the
  original tell. Every patch here goes through a closure-scoped ``native()``
  wrapper whose ``toString`` reports ``[native code]``.

  **Nothing may touch ``window``.** Exposing an ``__omkNative`` global would
  hand detectors a single unique token to key on. All helpers live in a closure;
  the composed script is one IIFE.

:func:`probe_script` produces a JS expression returning ``{key: bool}`` where
``true`` means *this tell is leaking*. Feed that mapping straight into
:func:`audit_cdp` — the same function the offline tests and the benchmark use,
so mock verification and live verification exercise identical logic.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from omk_crawl.browser_props import BOOTSTRAP, BrowserPropertySpec, props_body
from omk_crawl.fingerprint import FingerprintProfile

__all__ = [
    "BOOTSTRAP",
    "CDP_TELLS",
    "CDPLeak",
    "Patch",
    "PatchPlan",
    "Tell",
    "audit_cdp",
    "DRIVER_FIXED",
    "full_script",
    "patch_body",
    "patch_js",
    "patch_plan",
    "probe_script",
]

# How bad a surviving tell is. "high" = trivially observable by any vendor tag;
# "low" = only meaningful in combination with others.
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True, slots=True)
class Tell:
    """One CDP/automation leak vector.

    Attributes:
        key: Stable identifier used in probe output and audits.
        probe: JS boolean expression, ``true`` when the tell **is leaking**.
        severity: "high" | "medium" | "low".
        why: What a detector learns from it.
        patchable: Whether an init script can remove it (a launch flag or UA
            contradiction is fixed elsewhere — that is the cross-layer point).
    """

    key: str
    probe: str
    severity: str
    why: str
    patchable: bool = True

    @property
    def rank(self) -> int:
        return _SEVERITY_ORDER.get(self.severity, 3)


# The registry. Probes are deliberately simple expressions so a detector's own
# implementation is easy to compare against.
CDP_TELLS: tuple[Tell, ...] = (
    Tell(
        key="webdriver",
        probe="navigator.webdriver === true",
        severity="high",
        why="W3C WebDriver flag; present in every non-patched automation browser.",
    ),
    Tell(
        key="cdc_globals",
        probe=(
            "Object.getOwnPropertyNames(window).some("
            "k => k.startsWith('cdc_') || k.startsWith('$cdc_'))"
        ),
        severity="high",
        why="chromedriver injects uniquely-named cdc_/\\$cdc_ document properties.",
    ),
    Tell(
        key="dom_automation",
        probe="('domAutomation' in window) || ('domAutomationController' in window)",
        severity="high",
        why="Legacy ChromeDriver automation IPC handle.",
    ),
    Tell(
        key="playwright_bindings",
        probe=(
            "['__playwright__binding__','__pwInitScripts','__playwright_target__']"
            ".some(k => k in window)"
        ),
        severity="high",
        why="Playwright leaves binding globals on the page object.",
    ),
    Tell(
        key="puppeteer_bindings",
        probe=(
            "['__puppeteer_evaluation_script__','__puppeteer_utility_world__']"
            ".some(k => k in window)"
        ),
        severity="high",
        why="Puppeteer evaluation-world markers.",
    ),
    Tell(
        key="selenium_globals",
        probe=(
            "['__selenium_unwrapped','__webdriver_evaluate','__fxdriver_evaluate',"
            "'__driver_evaluate','__webdriver_script_fn','_Selenium_IDE_Recorder']"
            ".some(k => k in window)"
        ),
        severity="high",
        why="Selenium/FirefoxDriver residue on the window object.",
    ),
    Tell(
        key="stack_trace_markers",
        probe="(() => { try { throw new Error(); } catch (e) {"
              " return /puppeteer|playwright|pptr:|cdp\\./i.test(e.stack || ''); } })()",
        severity="medium",
        why="Injected wrapper frames leak into every stack trace the page can generate.",
    ),
    Tell(
        key="notification_permission",
        probe="typeof Notification !== 'undefined' && Notification.permission === 'denied'",
        severity="medium",
        why="Headless Chrome defaults notifications to denied; a real user's default is 'default'.",
    ),
    Tell(
        key="outer_dimensions_zero",
        probe="window.outerWidth === 0 || window.outerHeight === 0",
        severity="medium",
        why="Headless windows report zero outer dimensions.",
    ),
    Tell(
        key="plugins_empty",
        probe="navigator.plugins.length === 0",
        severity="low",
        why="Chrome always ships PDF viewer plugins; an empty list is a headless tell.",
    ),
    Tell(
        key="chrome_runtime_missing",
        probe="typeof window.chrome === 'undefined' || typeof chrome.runtime === 'undefined'",
        severity="low",
        why=(
            "window.chrome exists on every real Chrome page; "
            "its absence is a strong headless tell."
        ),
    ),
    Tell(
        key="webgl_software_renderer",
        probe="(() => { try { const c = document.createElement('canvas');"
              " const gl = c.getContext('webgl'); if (!gl) return true;"
              " const d = gl.getExtension('WEBGL_debug_renderer_info'); if (!d) return true;"
              " return /SwiftShader|llvmpipe|Software/i.test("
              "String(gl.getParameter(d.UNMASKED_RENDERER_WEBGL))); }"
              " catch (e) { return true; } })()",
        severity="medium",
        why="SwiftShader/llvmpipe renderers betray a GPU-less headless host.",
    ),
    Tell(
        key="headless_user_agent",
        probe="/HeadlessChrome|Headless/i.test(navigator.userAgent)",
        severity="high",
        why="Stock headless UA token.",
        patchable=False,  # fixed by the FingerprintProfile UA, not by JS
    ),
)

_TELLS_BY_KEY: dict[str, Tell] = {t.key: t for t in CDP_TELLS}

# Tells that are real but **not observable from JavaScript** — so they are not
# probes, and :func:`audit_cdp` must never invent a verdict for them. They are
# kept in the registry because the fix still has to happen, and only the
# operator can make it.
#
# ``Runtime.enable`` is the notable one. It is protocol state on the driver
# side, not a page property: two candidate JS probes were tried and both were
# empirically rejected against a real Chromium — the DevTools console-formatter
# trick returned False with Runtime.enable demonstrably active (it detects an
# open DevTools front-end, not the protocol domain), and a stack-frame regex
# matched only the *harness's* own ``UtilityScript.evaluate`` frames, which
# page-authored code never produces. Reporting either as a leak would be a false
# positive. The real fix is driver choice: nodriver/patchright avoid enabling
# the domain, which is why they sit first in the escalation chain for
# behavior-based vendors.
DRIVER_FIXED: dict[str, str] = {
    "runtime_enable": (
        "Runtime.enable is protocol state, not a JS property; use a CDP-native "
        "driver (nodriver/patchright) that does not enable the domain"
    ),
    "cdp_websocket": (
        "an open --remote-debugging-port is discoverable from outside the page; "
        "launch with --remote-debugging-pipe instead"
    ),
    "launch_flags": (
        "--enable-automation / --headless switches are visible to the process "
        "tree; prefer patchright's patched launch flags"
    ),
}


@dataclass(frozen=True, slots=True)
class CDPLeak:
    """A tell observed to be leaking, with severity and rationale."""

    key: str
    severity: str
    why: str

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "severity": self.severity, "why": self.why}


def audit_cdp(surface: Mapping[str, Any]) -> list[CDPLeak]:
    """Leaks present in a probe surface, worst first.

    ``surface`` maps :attr:`Tell.key` to the boolean produced by that tell's
    probe (extra keys are ignored; missing keys are treated as not leaking, so
    a partial probe still yields a usable, conservative report).

    Truthiness is what counts — so a probe that returned a non-empty string or
    a number is treated as a leak rather than silently passing.
    """
    leaks: list[CDPLeak] = []
    for key, value in surface.items():
        tell = _TELLS_BY_KEY.get(key)
        if tell is None or not value:
            continue
        leaks.append(CDPLeak(key=tell.key, severity=tell.severity, why=tell.why))
    leaks.sort(key=lambda leak: _SEVERITY_ORDER.get(leak.severity, 3))
    return leaks


def probe_script() -> str:
    """JS expression evaluating every probe into ``{key: bool}``.

    Returns an IIFE so it is safe to hand straight to ``page.evaluate`` /
    ``Runtime.evaluate``. Keys match :attr:`Tell.key`, so the result feeds
    :func:`audit_cdp` with no translation.
    """
    entries = ",\n    ".join(
        f"{tell.key}: (() => {{ try {{ return Boolean({tell.probe}); }}"
        f" catch (e) {{ return true; }} }})()"
        for tell in CDP_TELLS
    )
    return "(() => ({\n    " + entries + "\n  }))()"


# ─ Patching ───────────────────────────────────────────────────────────────

# The helper installer lives in browser_props.BOOTSTRAP and is imported, not
# duplicated: cdp.py and browser_props.py must install byte-identical helpers,
# or a page could observe two different Function.prototype.toString
# replacements — a tell created by trying to hide one.

# Strategy chosen from the seed: two genuinely different ways to remove the
# chromedriver globals, so the patch footprint is not byte-identical across
# deployments. Both are effective; the choice only defeats signature matching
# against one specific cleanup pattern.
_SCRUB_DELETE = """
  for (const key of Object.getOwnPropertyNames(window)) {
    if (key.startsWith('cdc_') || key.startsWith('$cdc_') || key.startsWith('cdc_ado')) {
      try { delete window[key]; } catch (e) { define(window, key, undefined); }
    }
  }
""".strip("\n")

_SCRUB_REDEFINE = """
  for (const key of Object.getOwnPropertyNames(window)) {
    if (key.startsWith('cdc_') || key.startsWith('$cdc_') || key.startsWith('cdc_ado')) {
      define(window, key, undefined);
    }
  }
""".strip("\n")

_GLOBALS_PATCH = """
  for (const key of ['domAutomation','domAutomationController','__playwright__binding__',
                     '__pwInitScripts','__playwright_target__',
                     '__puppeteer_evaluation_script__','__puppeteer_utility_world__',
                     '__selenium_unwrapped','__webdriver_evaluate','__fxdriver_evaluate',
                     '__driver_evaluate','__webdriver_script_fn','_Selenium_IDE_Recorder']) {
    if (key in window) { define(window, key, undefined); }
  }
""".strip("\n")

_WEBDRIVER_PATCH = """
  try {
    Object.defineProperty(Navigator.prototype, 'webdriver',
      { get: native(function () { return false; }, 'get webdriver', 0),
        configurable: true });
  } catch (e) { /* prototype already frozen — reported by audit_cdp */ }
""".strip("\n")

_CHROME_OBJECT_PATCH = """
  if (typeof window.chrome === 'undefined') {
    const start = Date.now() / 1000;
    define(window, 'chrome', {
      runtime: {},
      app: { isInstalled: false, InstallState: {}, RunningState: {} },
      csi: native(function csi() {
        return { onloadT: Date.now(), startE: Math.round(start * 1000),
                 pageT: Date.now() - start * 1000, tran: 15 };
      }, 'csi', 0),
      loadTimes: native(function loadTimes() {
        return { requestTime: start, startLoadTime: start, commitLoadTime: start,
                 finishDocumentLoadTime: start, finishLoadTime: start,
                 firstPaintTime: start, navigationType: 'Other',
                 wasNpnNegotiated: true, npnNegotiatedProtocol: 'h2',
                 wasAlternateProtocolAvailable: false, connectionInfo: 'h2' };
      }, 'loadTimes', 0),
    });
  }
""".strip("\n")

_STACK_SCRUB_PATCH = """
  const _prepare = Error.prepareStackTrace;
  Error.prepareStackTrace = native(function prepareStackTrace(err, frames) {
    const filtered = Array.prototype.filter.call(frames, (frame) => {
      const file = String(frame.getFileName() || '') + String(frame.getFunctionName() || '');
      return !/playwright|puppeteer|pptr:|cdp\\.|__pw/i.test(file);
    });
    if (typeof _prepare === 'function') { return _prepare.call(Error, err, filtered); }
    return err.name + ': ' + err.message + '\\n' +
      filtered.map((f) => '    at ' + f).join('\\n');
  }, 'prepareStackTrace', 2);
""".strip("\n")

_NOTIFICATION_PATCH = """
  if (typeof Notification !== 'undefined') {
    // A real first-time visitor sits at 'default'; headless Chrome reports
    // 'denied'. The Permissions API must tell the same story, or the pair is
    // itself the tell.
    try {
      Object.defineProperty(Notification, 'permission',
        { get: native(function () { return 'default'; }, 'get permission', 0),
          configurable: true });
    } catch (e) { /* frozen — audit_cdp reports it rather than hiding it */ }
    if (navigator.permissions && navigator.permissions.query) {
      const _query = navigator.permissions.query.bind(navigator.permissions);
      define(navigator.permissions, 'query',
        native(function query(desc) {
          const result = _query(desc);
          if (desc && desc.name === 'notifications') {
            return Promise.resolve(Object.assign(result, { state: 'prompt' }));
          }
          return result;
        }, 'query', 1));
    }
  }
""".strip("\n")

_FRAGMENTS: dict[str, str] = {
    "webdriver": _WEBDRIVER_PATCH,
    "cdc_scrub_delete": _SCRUB_DELETE,
    "cdc_scrub_redefine": _SCRUB_REDEFINE,
    "automation_globals": _GLOBALS_PATCH,
    "stack_scrub": _STACK_SCRUB_PATCH,
    "chrome_object": _CHROME_OBJECT_PATCH,
    "notification": _NOTIFICATION_PATCH,
}

# Tell -> fragment name. The indirection exists so several tells can share one
# fragment (“stack_scrub” fixes both Runtime.enable leakage and wrapper frames)
# while still being tracked as separate, individually covered tells. Patching is
# keyed on the fragment, never the tell — see patch_plan().
_TELL_FRAGMENT: dict[str, str] = {
    "webdriver": "webdriver",
    "cdc_globals": "cdc",  # expands to a seed-selected scrub variant
    "dom_automation": "automation_globals",
    "playwright_bindings": "automation_globals",
    "puppeteer_bindings": "automation_globals",
    "selenium_globals": "automation_globals",
    "stack_trace_markers": "stack_scrub",
    "chrome_runtime_missing": "chrome_object",
    "notification_permission": "notification",
}

# Property-surface tells belong to browser_props.py, which derives their values
# from the same FingerprintProfile. Patching them here as well would assign each
# one two different ways — precisely the cross-layer drift this project exists
# to prevent (constitution P7).
_DELEGATED: dict[str, str] = {
    "plugins_empty": "browser_props",
    "webgl_software_renderer": "browser_props",
    "outer_dimensions_zero": "browser_props",
}

# Tells whose fix is a launch flag or the profile UA rather than an init script.
_PROFILE_FIXED: dict[str, str] = {
    "headless_user_agent": "fixed by FingerprintProfile.user_agent (no JS patch)",
}

_STRATEGIES = ("delete", "redefine")


@dataclass(frozen=True, slots=True)
class Patch:
    """One init-script fragment.

    Attributes:
        covers: Tell keys this fragment removes. Usually one, but a shared fix
            such as the stack-trace scrub covers several — and emitting it once
            per tell would redeclare its ``const``s, which is a SyntaxError that
            kills the whole composed IIFE.
        js: Body fragment; runs inside the composed IIFE closure.
        source: How it was resolved — "patch", "strategy" (seed-selected
            variant), or "bootstrap".
    """

    covers: tuple[str, ...]
    js: str
    source: str = "patch"


@dataclass(frozen=True, slots=True)
class PatchPlan:
    """Deterministic, ordered patch set for one identity.

    Attributes:
        profile: Profile name the plan was built for.
        seed: The integer seed actually used (post-hash).
        strategy: Seed-selected global-scrubbing variant.
        patches: Ordered fragments.
        unpatchable: Tells that no init script can fix, with the reason, so the
            caller knows what still has to be handled outside the page.
    """

    profile: str
    seed: int
    strategy: str
    patches: tuple[Patch, ...]
    unpatchable: dict[str, str]
    delegated: dict[str, str] = field(default_factory=dict)
    driver_fixed: dict[str, str] = field(default_factory=dict)

    def covered_tells(self) -> tuple[str, ...]:
        """Tell keys this plan removes — patched here or by a sibling module."""
        own = [key for patch in self.patches for key in patch.covers]
        return tuple(own) + tuple(k for k in self.delegated if k not in own)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "seed": self.seed,
            "strategy": self.strategy,
            "covered": list(self.covered_tells()),
            "delegated": dict(self.delegated),
            "unpatchable": dict(self.unpatchable),
            "driver_fixed": dict(self.driver_fixed),
        }


def _seed_int(seed: int | str | bytes | None, profile_name: str) -> int:
    """Stable 64-bit seed; ``None`` derives deterministically from the profile."""
    if seed is None:
        seed = profile_name
    if isinstance(seed, int):
        return seed & 0xFFFFFFFFFFFFFFFF
    if isinstance(seed, str):
        seed = seed.encode("utf-8")
    return int.from_bytes(hashlib.sha256(seed).digest()[:8], "big")


def patch_plan(
    profile: FingerprintProfile,
    seed: int | str | bytes | None = None,
) -> PatchPlan:
    """Build the ordered patch plan for ``profile``.

    Order is fixed and meaningful: the bootstrap installs ``native()`` first,
    everything else depends on it. Determinism is total — the same
    ``(profile, seed)`` yields an identical plan and identical JS, which is
    what keeps the patched surface from drifting between sessions (the
    temporal axis FP-Inconsistent measures).
    """
    seed_value = _seed_int(seed, profile.name)
    strategy = _STRATEGIES[seed_value % len(_STRATEGIES)]

    patches: list[Patch] = [Patch(covers=(), js=BOOTSTRAP, source="bootstrap")]
    # Group tells by fragment. This is not an optimization: two tells sharing one
    # fix would otherwise emit it twice, redeclaring its `const`s and throwing a
    # SyntaxError that aborts the entire IIFE — every patch silently skipped.
    # Found by running the composed script in a real Chromium, not by inspection.
    position_of: dict[str, int] = {}
    for tell in CDP_TELLS:
        if not tell.patchable or tell.key in _DELEGATED:
            continue
        name = _TELL_FRAGMENT.get(tell.key)
        if name is None:
            continue
        source = "patch"
        if name == "cdc":
            name = f"cdc_scrub_{strategy}"
            source = "strategy"
        position = position_of.get(name)
        if position is not None:
            existing = patches[position]
            patches[position] = replace(existing, covers=existing.covers + (tell.key,))
            continue
        position_of[name] = len(patches)
        patches.append(Patch(covers=(tell.key,), js=_FRAGMENTS[name], source=source))

    return PatchPlan(
        profile=profile.name,
        seed=seed_value,
        strategy=strategy,
        patches=tuple(patches),
        unpatchable=dict(_PROFILE_FIXED),
        delegated=dict(_DELEGATED),
        driver_fixed=dict(DRIVER_FIXED),
    )


def patch_body(plan: PatchPlan) -> str:
    """The plan's fragments only — for composition inside a shared IIFE.

    Depends on the helpers from :data:`BOOTSTRAP` being in scope; use
    :func:`patch_js` for a standalone script.
    """
    return "\n\n".join(p.js for p in plan.patches if p.source != "bootstrap")


def patch_js(plan: PatchPlan) -> str:
    """Compose a plan into one self-contained init script.

    Suitable for ``Page.addScriptToEvaluateOnNewDocument``,
    ``page.add_init_script``, or a ``<script>`` tag injected before page code.
    The single closure keeps every helper out of the global object, so the patch
    adds no new fingerprintable surface of its own.
    """
    return (
        "(() => {\n  'use strict';\n"
        + BOOTSTRAP
        + "\n\n"
        + patch_body(plan)
        + "\n})();"
    )


def full_script(plan: PatchPlan, spec: BrowserPropertySpec) -> str:
    """One init script covering both the automation and property surfaces.

    Sharing a single IIFE and a single bootstrap is the point: two separate
    scripts would install ``Function.prototype.toString`` twice, and the page
    could observe the difference. Property values come from ``spec``, which was
    derived from the same profile the plan was built from (P7).
    """
    return (
        "(() => {\n  'use strict';\n"
        + BOOTSTRAP
        + "\n\n"
        + patch_body(plan)
        + "\n\n"
        + props_body(spec)
        + "\n})();"
    )
