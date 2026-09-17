# Feature Specification: Deep Evasion Layers (v2.14)

> Constitution: `.specify/memory/constitution.md`
> Builds on: `../001-smart-escalation-core/spec.md`, `../002-unblockable-breakthrough/spec.md`
> Plan: `./plan.md` · Tasks: `./tasks.md`

> **Language note.** Specs 001 and 002 are written in Korean. This one is in English
> because the tooling in this environment corrupted Korean text on the way into the
> repository (characters were silently dropped, e.g. a word ending up one syllable
> short). Rather than commit damaged prose, the specification is written in the
> language that survives the toolchain intact. The code itself is unaffected and
> contains no Korean.

## Overview

The v2.12/2.13 breakthrough layer enforced coherence down to the HTTP header layer
and the `curl_cffi` TLS target. Three surfaces below and around that remained
unmanaged — the TCP/IP stack, the JavaScript object surface, and the CDP protocol
artifacts — along with the policy question of what to do when a crawl lands on a
challenge page. This specification closes those four gaps with six modules and a
single arbiter.

The thesis is unchanged from v2.12: the winning move is not *more tricks*, it is
**agreement between layers** (FP-Inconsistent, arXiv:2406.07647; multi-layer
web-agent fingerprinting, arXiv:2606.30119). A trick is another thing to get wrong;
agreement is a property that can be derived and verified.

## Problem

1. **The TCP stack is unmanaged.** Kernel defaults expose the OS through TTL,
   window, MSS and TCP option *ordering*. A User-Agent claiming Windows over an
   unmistakably Linux SYN is a contradiction that passive fingerprinters read before
   a single HTTP byte is sent.
2. **CDP artifacts have no contract.** The patchright/nodriver adapters exist, but
   nothing states which tells are patched, in what order, or whether the patch
   survives `Function.prototype.toString`. A patch that can be read back is worse
   than the original tell.
3. **The JS object surface contradicts the headers.** `navigator.languages`,
   `Intl.DateTimeFormat().timeZone`, `screen.*`, the unmasked WebGL renderer and a
   canvas hash are all readable by the page, and all default to the host's values.
4. **Behavior has no time structure.** `BehaviorClock` supplies timings and
   trajectories but no keystroke dynamics and no burst/pause session shape.
5. **No challenge policy.** v2.12 scoped CAPTCHA-solver integration out and kept only
   an extension point. Nothing decides between "a real browser can clear this",
   "delegate to a configured solver", and "this needs a person — refuse".

## User stories

| ID | Story | Priority |
| ---- | ------- | ---------- |
| US-1 | As an operator I want the TCP signature to agree with the OS my UA claims | P1 |
| US-2 | As an operator I want CDP leaks patched against my profile, without the patches becoming new tells | P1 |
| US-3 | As an operator I want the JS object surface to tell the same story as the headers | P1 |
| US-4 | As an operator I want a challenge page to be classified and a policy applied automatically | P1 |
| US-5 | As a developer I want to self-check my configuration offline, before a real site sees it | P1 |
| US-6 | As a developer I want typing and session rhythm reproducible from a seed | P2 |

## Requirements

### Functional

- **FR-1**: `tcp.py` defines OS-level SYN signatures (`TcpStackProfile` for Windows
  10/11, macOS 14, Linux 6, Android 14, iOS 17). `stack_for(profile)` returns the
  stack matching the profile's OS claim, deterministically.
- **FR-2**: `apply_to_socket()` sets the fields `setsockopt` genuinely controls
  (`IP_TTL`, `TCP_MAXSEG`, `TCP_NODELAY`, `SO_RCVBUF`) and reports kernel-owned
  fields (window scaling, option order, timestamps, DF, ISN) in
  `EmulationReport.unsupported`. Silent degradation is prohibited.
- **FR-3**: `tls.py` normalizes GREASE (RFC 8701) and computes deterministic JA3 and
  JA4-style model identifiers from a ClientHello model. `audit_tls()` reports family
  disagreements between the handshake and the profile; `drift_report()` compares two
  hellos on the temporal axis.
- **FR-4**: `cdp.py` provides a registry of automation tells with boolean JS probes
  and a seeded `PatchPlan`. Patched functions must keep `[native code]` under
  `Function.prototype.toString`, and no helper may be attached to `window`.
- **FR-5**: `audit_cdp(surface)` returns leaks with severity and rationale, worst
  first, and never invents a verdict for a tell that cannot be probed from JS.
- **FR-6**: `browser_props.py` derives the JS surface (navigator, screen, Intl
  timezone, WebGL, PDF plugins, canvas/audio noise) from the profile. Noise is
  stable for a given seed — a device that resamples its canvas hash per visit is
  itself the signal.
- **FR-7**: `audit_props(observed, profile)` reports platform, locale, timezone,
  screen-versus-viewport, renderer-plausibility and touch-point inconsistencies.
- **FR-8**: `behavior.py` gains `typing_plan(text)` (seeded dwell/flight with
  neighbouring-key typos and corrections) and `session_rhythm(n_pages)` (burst/pause
  structure) without changing the v2.12 API.
- **FR-9**: `captcha.py` classifies challenge HTML into `CaptchaKind` (11 families,
  plus `NONE` and `UNKNOWN`) and provides a solver contract with three backends: a
  fail-closed null backend, a deterministic mock, and an environment-gated HTTP
  backend.
- **FR-10**: `resolve_challenge()` returns `proceed`, `clearance_flow`, `solver`, or
  `refuse`, always with a reason. Challenges that require human judgement are refused
  before any backend is consulted.
- **FR-11**: `evasion.py::plan_for(url)` binds all six layers to one seeded identity
  and `coherence_report()` re-audits them per layer.
- **FR-12**: `verify.py` is an offline mock anti-bot detector scoring seven weighted
  checks. It performs no network I/O.

### Non-functional

- **NFR-1**: The new core modules are zero-dependency: standard library plus the
  existing core (P4).
- **NFR-2**: Every path fails closed. Missing credentials, unsupported socket
  options and unclassifiable challenges are reported explicitly, never skipped (P2).
- **NFR-3**: Tests are offline and deterministic; no unseeded randomness (P6).
- **NFR-4**: No credentials in source. The solver endpoint and key are read only from
  `OMK_CAPTCHA_ENDPOINT` and `OMK_CAPTCHA_KEY` (P2).
- **NFR-5**: Importing any new module has no side effects and adds no third-party
  dependency.

## Guardrails

- **Never bypass authentication.** `AUTH_REQUIRED` keeps its top position in the
  escalation priority table; no module here crosses a login wall (P3).
- **Interactive challenges are refused.** Image grids, sliders and press-and-hold
  challenges exist to require a person. `resolve_challenge` refuses them and does not
  consult a solver, so no configuration mistake can turn this into a
  CAPTCHA-defeating tool.
- **The solver is an extension point, not a service.** No bundled endpoint, no
  bundled key, no default activation.
- **Emulation is not overstated.** Fields a userspace socket cannot control are
  reported as unsupported. Nothing claims to have emulated what it did not (P1).
- Stealth and fingerprint coherence apply only to client discrimination against
  content the operator is authorized to reach.

## Acceptance criteria

- [x] AC-1: For every built-in profile, `stack_for` returns a stack whose OS matches
  the UA claim, and `audit_tcp` catches injected mismatches.
- [x] AC-2: Applying a stack to a real socket changes `IP_TTL` to the requested value
  (verified against the kernel) and reports the kernel-owned fields as unsupported.
- [x] AC-3: GREASE normalization is idempotent; the same profile yields the same JA3
  and different families yield different JA3; `audit_tls` catches family
  disagreements; `drift_report` ignores GREASE-only churn.
- [x] AC-4: `patch_plan` is deterministic, emits no fragment twice, and the composed
  script contains exactly one bootstrap and attaches nothing to `window`.
- [x] AC-5: `audit_cdp` detects injected `navigator.webdriver`, `cdc_*` globals and
  Playwright bindings, and treats truthy non-booleans as leaks.
- [x] AC-6: `spoof_script` is byte-identical for the same `(profile, seed)`;
  `audit_props` catches timezone, platform, language, renderer and
  screen-smaller-than-viewport inconsistencies; the screen invariant holds for every
  profile and seed.
- [x] AC-7: `typing_plan` and `session_rhythm` are seed-reproducible, and the v2.12
  behavior API is unchanged.
- [x] AC-8: `classify_captcha` separates Turnstile, reCAPTCHA v3, hCaptcha, DataDome,
  Kasada, AWS WAF, image grid, slider and press-and-hold; a benign page classifies as
  `NONE` while generic challenge language classifies as `UNKNOWN`;
  `resolve_challenge` refuses every human-judgement kind even with a solver
  available.
- [x] AC-9: `plan_for` is deterministic per site (including across pages of the same
  site), and `coherence_report()` passes for every built-in identity.
- [x] AC-10: The mock detector separates the three reference configurations, and the
  naive-stealth configuration scores *worse* than an honest client.
- [x] AC-11: `pytest tests/ -q` passes in full, `ruff check` reports zero errors,
  `gitleaks dir .` reports zero findings, and `omk-crawl --help` / `--diagnose` behave.
- [x] AC-12: `scripts/bench_evasion.py` records detection score, plan latency,
  determinism and fleet diversity for the three strategies in
  `benchmarks/evasion/latest.json`.

## Out of scope

- Real SYN packet forgery or BPF injection. This specification provides the signature
  and the audit; wire-level application is the operator's to perform.
- Commercial CAPTCHA service accounts, pricing and terms.
- Wire-capture (tcpdump/BPF) JA3 verification. Auditing is model-based; the real
  handshake remains `curl_cffi`'s.
- Browser engine (C++) level patches — that is camoufox's role.