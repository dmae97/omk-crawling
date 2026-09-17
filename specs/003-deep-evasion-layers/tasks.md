# Tasks: Deep Evasion Layers (v2.14)

> Spec: `./spec.md` · Plan: `./plan.md`
> All items below are complete; the state is recorded rather than aspirational.

## T1 — TCP stack layer (FR-1, FR-2, AC-1, AC-2)

- [x] T1.1 `omk_crawl/tcp.py`: `TcpStackProfile` (windows-10/11, macos-14, linux-6,
  android-14, ios-17) plus `stack_for(profile)`
- [x] T1.2 `syn_signature()`, `option_kinds()`, `audit_tcp()`
- [x] T1.3 `apply_to_socket(sock, profile) -> EmulationReport`: real `setsockopt`
  plus an explicit `unsupported` report. Verified against the kernel: TTL changes
  64 -> 128 on a real socket

## T2 — TLS / JA3 normalization (FR-3, AC-3)

- [x] T2.1 GREASE constants and `normalize_grease`
- [x] T2.2 `TlsClientHello` with `ja3_string`/`ja3`/`ja4_model`/`signature`
- [x] T2.3 `hello_for(profile)` and `audit_tls(hello, profile)`
- [x] T2.4 `drift_report(previous, current)` for the temporal axis

## T3 — CDP patching (FR-4, FR-5, AC-4, AC-5)

- [x] T3.1 `CDP_TELLS` registry (probe, severity, rationale, patchability)
- [x] T3.2 `patch_plan(profile, seed)`, `patch_body`, `patch_js`, `full_script`,
  native-toString bootstrap shared with `browser_props`
- [x] T3.3 `audit_cdp(surface) -> list[CDPLeak]`
- [x] T3.4 `probe_script()` returning `{key: bool}` for live or mock surfaces
- [x] T3.5 Tells that JS cannot fix are documented in `DRIVER_FIXED`, not faked

## T4 — Browser property spoofing (FR-6, FR-7, AC-6)

- [x] T4.1 `property_spec(profile, seed)`: navigator, screen, Intl, WebGL, plugins,
  canvas/audio noise seeds
- [x] T4.2 `spoof_script(spec)` with seed-deterministic LCG noise
- [x] T4.3 `audit_props(observed, profile)`
- [x] T4.4 `webgl_identity(profile)` as the single source of renderer strings

## T5 — Behavior extensions (FR-8, AC-7)

- [x] T5.1 `BehaviorClock.typing_plan(text)`: dwell/flight, neighbouring-key typos,
  corrections
- [x] T5.2 `BehaviorClock.session_rhythm(n_pages)`: burst/pause phases

## T6 — Challenge classification and policy (FR-9, FR-10, AC-8)

- [x] T6.1 `CaptchaKind` (11 families + `NONE` + `UNKNOWN`), `CaptchaChallenge`,
  `classify_captcha(html)`
- [x] T6.2 `SolverBackend` contract, `NullSolver`, `MockSolver`, `HttpSolver`
- [x] T6.3 `resolve_challenge()`: `proceed`/`clearance_flow`/`solver`/`refuse`, with
  interactive kinds refused before any backend lookup

## T7 — Arbiter (FR-11, AC-9)

- [x] T7.1 `EvasionPlan` and `plan_for(url, seed, salt)`
- [x] T7.2 `coherence_report()`, `browser_kwargs()`, `init_script()`, `curl_kwargs()`,
  `as_metadata()`, `describe()`

## T8 — Offline verifier (FR-12, AC-10)

- [x] T8.1 `verify.py`: seven weighted checks and `score(surface) -> Verdict`
- [x] T8.2 Reference surfaces: `stock_surface`, `naive_stealth_surface`,
  `evasion_surface`

## T9 — Tests (AC-11)

- [x] T9.1 `tests/test_evasion_layers.py`: 132 tests covering T1-T8
- [x] T9.2 Three real-browser tests (skipped only when no Chromium build is present)
  proving the composed script executes, disguises function sources, and produces
  stable canvas noise
- [x] T9.3 Existing suite regression check: 469 tests still pass

## T10 — Benchmark (AC-12)

- [x] T10.1 `scripts/bench_evasion.py`: three strategies, plan latency,
  determinism, fleet diversity
- [x] T10.2 `benchmarks/evasion/latest.json` produced

## T11 — Real-path wiring (AC-11)

- [x] T11.1 `warmup._acquire_patchright` uses the plan and installs the init script
- [x] T11.2 `tools/curl_cffi_tool` honours `evade=True`
- [x] T11.3 `cli --evasion` prints plan, audit and score

## T12 — Documentation and version sync (AC-11)

- [x] T12.1 `pyproject.toml`, `omk_crawl.__version__`, `CHANGELOG.md`,
  `references/breakthrough.md`, `SKILL.md`
- [x] T12.2 Four quality gates: pytest, ruff, gitleaks, CLI smoke

## Defects found and fixed during implementation

These were found by running things, not by reading them. Each has a regression test.

- **Duplicate fragment aborts the whole script.** Two tells shared one patch
  fragment; emitting it once per tell redeclared its `const`s, and the resulting
  `SyntaxError` silently disabled every patch after it. Patching is now keyed on the
  fragment, with `covers` recording which tells it addresses.
- **A patchable tell lost its fragment mapping**, so it was marked patchable while
  nothing patched it. Caught by the coverage test.
- **The seed was keyed on the full URL**, so `/a` and `/b` on the same site presented
  different canvas noise - intra-session drift, the exact inconsistency
  FP-Inconsistent measures. Now keyed on the host, matching `profile_for`.
- **The macOS device-pixel-ratio pool offered 1.0**, which contradicts a Retina panel
  at a 1680 px viewport. The coherence self-check caught it; the pool was wrong, not
  the audit.