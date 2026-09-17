# Plan: Deep Evasion Layers (v2.14)

> Spec: `./spec.md` · Tasks: `./tasks.md`

## Approach

Six modules are stacked **bottom-up**. Each takes one `FingerprintProfile` and
derives that layer's representation from it, so coherence is a property of
construction rather than an after-the-fact review (an extension of P7).

```
                    ┌──────────────────────────────
   policy/arbiter → │ evasion.EvasionPlan          │  plan_for(url) → 6 layers
                    └──────────────────────────────┘
                       ↑        ↑        ↑        ↑
   ┌───────────┐ ┌──────────┐ ────────┐ ┌───────────┐ ┌───────────┐
   │ captcha   │ │ cdp      │ │ props  │ │ behavior  │ │ tcp / tls │
   │ classify  │ │ patches  │ │ JS     │ │ typing    │ │ stack /   │
   │ + policy  │ │          │ │ surface│ │ + rhythm  │ │ hello     │
   └───────────┘ └──────────┘ └────────┘ └───────────┘ └───────────┘
                            ↓
                  fingerprint.FingerprintProfile   (v2.12, single source of truth)
                            ↓
                     verify.MockDetector          offline self-check
```

## Design decisions

1. **TCP splits into "apply" and "honestly report".** Fields `setsockopt` controls
   (`IP_TTL`, `TCP_MAXSEG`, `TCP_NODELAY`, `SO_RCVBUF`) are actually set; kernel-owned
   fields (window scaling, option order, timestamps, DF) land in
   `EmulationReport.unsupported`. `syn_signature()` still describes the complete
   target, so an operator with a raw-socket path can emit it. Nothing claims to have
   forged a packet (P1).

2. **TLS is model-based auditing.** The real ClientHello is produced by `curl_cffi`
   against a real browser build. This module derives a deterministic model from the
   profile, normalizes GREASE and computes JA3/JA4-style identifiers, so profiles can
   be audited offline and drift detected over time. It does not claim to have emitted
   wire bytes.

3. **The CDP patch must look native.** All injection goes through a closure-scoped
   `native()` wrapper whose `toString` reports `[native code]`; helpers stay out of
   `window`. Facts established by execution, not inspection: writing the composed
   script into a real Chromium is what proves it.

4. **JS noise is seed-deterministic.** Canvas and audio perturbation derive from a
   seeded LCG embedded in the script, so the same `(profile, seed)` yields the same
   hash. Re-randomising per visit is the over-time inconsistency detectors score.

5. **Challenge policy is three-way and fail-closed.** `proceed` (nothing there),
   `clearance_flow` (a browser clears it, no third party, no cost), `solver` (only on
   explicit opt-in against an env-gated endpoint), `refuse` (human judgement, or
   unrecognized). Interactive kinds never reach a backend.

6. **`verify.py` ships as product code.** The mock detector is not test scaffolding;
   it is the operator's self-check. Tests and the benchmark consume the same
   implementation, so mock verification and live verification exercise identical
   logic.

## Real-path wiring

| Point | Connection |
| ------- | ------------ |
| `warmup._acquire_patchright` | Builds a full plan; context kwargs and the composed init script come from one identity, installed before any page script runs |
| `tools/curl_cffi_tool` | `evade=True` derives the TLS target and headers from the plan and records the cross-layer audit in metadata |
| `cli` | `omk-crawl <url> --evasion` prints the plan, its coherence audit and the offline score (`--json` for machines) |
| `scripts/bench_evasion.py` | Three strategies scored offline; exits non-zero if the headline property regresses, so CI can gate on it |

## Risks

| Risk | Mitigation |
| ------ | ----------- |
| TCP application fails per-platform | `apply_to_socket` never raises; each field is attempted independently and failures land in `unsupported`/`errors`. The test asserts a real kernel value change on Linux |
| A CDP patch becomes a new tell | `native()` disguising is enforced by an acceptance criterion, and `audit_cdp` self-checks before a site is contacted |
| The captcha module is mistaken for a bypass tool | Classification, policy and a contract only. Human-judgement kinds refuse before a backend is consulted; the HTTP solver is inert without environment credentials |
| Regression across the existing suite | New modules are purely additive; `behavior` is extended without changing existing signatures. Verified: 469 existing tests still pass |