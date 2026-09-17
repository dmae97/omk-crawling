# 돌파(Breakthrough) 레이어 — fingerprint · behavior · warmup

> v2.12 신규. 목표: 접근 권한이 있는 콘텐츠에 대해 어떤 안티봇 스택 앞에서도
> 바이트를 확보한다. 승부처는 트릭 수가 아니라 **일관성·행동·세션 재사용**이다.

## 왜 (연구 근거)

| 연구 | 핵심 | 적용 |
| ------ | ------ | ------ |
| arXiv:2406.07647 (FP-Inconsistent) | 우회 봇은 **속성 간·시간축 불일치**에서 잡힌다 | fingerprint 일관성 + 사이트별 지문 고정 |
| arXiv:2606.30119 (멀티레이어 지문) | 어설픈 스텔스 패치는 **탐지를 높인다** | 엔진 레벨 위장(camoufox) 또는 CDP 네이티브(nodriver)만 |
| arXiv:2602.09606 (TLS/JA4) | 핸드셰이크만으로 bad-bot 검출 | impersonation과 토큰 리플레이의 정합성 |
| arXiv:2606.14525 (봇 탐지 분류) | 벤더별 기법 분류(2026) | KASADA/PERIMETERX/AWS_WAF 탐지 추가 |
| arXiv:2502.01608 (실사용자 지문) | 정적 크롤은 인증·상호작용 트리거를 놓친다 | 사람처럼 진입하는 웜업 플로우 |

## ① fingerprint.py — 크로스레이어 지문 일관성

```python
from omk_crawl import profile_for, coherence_issues, match_impersonate

profile = profile_for("https://example.com")   # 사이트별 결정적(시간축 일관성)
profile.headers()        # UA + Client Hints + Accept-Language (한 가지 이야기)
profile.curl_kwargs()    # {"impersonate": ..., "headers": ...} — TLS와 헤더 일치
profile.browser_context_kwargs()  # playwright 컨텍스트 (locale/tz/viewport)

# 임의 헤더 세트 감사 — 레이어가 서로 다른 이야기를 하면 잡아낸다
issues = coherence_issues(headers, impersonate="chrome124")

# 기존 impersonate 타깃에 맞는 일관 프로필
match_impersonate("firefox133")
```

감사 5-룰: ①Chromium이 아닌데 Client Hints 전송 ②UA OS ↔ Sec-CH-UA-Platform
불일치 ③UA Chrome 메이저 ↔ Client Hints 메이저 불일치 ④모바일 토큰 ↔
Sec-CH-UA-Mobile 불일치 ⑤TLS impersonate ↔ UA 패밀리/버전 불일치.

## ② behavior.py — 시드 결정적 인간 행동

```python
from omk_crawl import BehaviorClock

clock = BehaviorClock("example.com|session-1")
clock.think_time()              # 사고 정지 (lognormal ~1.1s, clamp 0.4–4.0)
clock.dwell_time(8000)          # 읽기 체류 (~180 chars/s + jitter)
clock.inter_request_delay()     # 요청 간 페이싱 (버스트 인간 케이던스)
clock.mouse_path((0, 0), (400, 300))   # Bezier + jitter 궤적
clock.scroll_plan(8000, 1080)          # 청크 스크롤 정지점들
```

같은 시드 → 같은 열(재현·테스트 가능). 난수형 스텔스의 시간축 불일치 방지.

## ③ warmup.py — 세션 웜업 & 클리어런스 재사용

한 번 통과(cf_clearance·datadome·_abck·aws-waf-token 수확) → 같은 지문으로
가볍게 N회 리플레이. 벤더의 토큰이 지문 신호에 바인딩되므로 TLS/헤더/쿠키가
반드시 일치해야 한다.

```python
from omk_crawl import warm_crawl, SessionWarmup

r = warm_crawl("https://protected.example.com/article")
# 1. 캐시된 유효 세션 재사용 (도메인별 TTL, 기본 30분)
# 2. 없으면 브라우저(nodriver→patchright→camoufox)로 루트에 사람처럼 진입
# 3. WarmSession.curl_kwargs()로 curl_cffi 리플레이 (TLS+헤더+쿠키 일치)
# 4. 차단 시 세션 폐기 → SmartRouter 전체 래더로 폴��

manager = SessionWarmup()          # ~/.cache/omk-crawl/warm-sessions.json (0600)
manager.record(url, cookies, tool="manual")   # 내 브라우저 세션 수동 주입도 가능
manager.get(url); manager.invalidate(url)
```

## 어댑터 선택 (라우팅 테이블 1순위)

| 탐지 | 1순위 | 이유 |
| ------ | ------- | ------ |
| Cloudflare / Akamai / Imperva / WAF | insane_search → camoufox | 브레이커 → 엔진 레벨 위장 |
| DataDome / Kasada / PerimeterX | nodriver | CDP 네이티브, 자동화 아티팩트無 |
| AWS WAF | camoufox | 토큰 챌린지, 안티디텍트 강함 |
| TLS fingerprint | insane_search → curl_cffi | 프로필 로테이션 |

## 가드레일 (헌법 P3)

- AUTH_REQUIRED(401/로그인)는 절대 우회하지 않는다 — 라우팅 테이블 `[]`.
- 웜업·스텔스·지문 일관성은 접근 자격이 있는 콘텐츠의 클라이언트 차별에 한정.
- 세션 쿠키는 로컬 0600 캐시, TTL 만료 자동 폐기, 같은 도메인에만 리플레이.

---

# Deep evasion layers - tls, tcp, cdp, props, captcha, evasion (v2.14)

> v2.12 enforced coherence down to the HTTP headers. v2.14 derives the layers below
> (TCP), above (the JS object surface) and beside (CDP artifacts) it from the same
> identity, and adds the challenge policy v2.12 left open.
> Spec: `specs/003-deep-evasion-layers/`. Benchmark: `python3 scripts/bench_evasion.py`.

> **Language note.** The v2.12 section above is Korean. This section is English
> because the tooling in this environment dropped characters from Korean text on the
> way into the repository. Correct English is preferable to damaged prose; the code
> itself contains no Korean.

## In one line

```python
from omk_crawl import plan_for

plan = plan_for("https://example.com")   # six layers from one seed
plan.coherence_report().ok               # self-audit; failures come with reasons
plan.init_script()                       # CDP patches + JS surface in one IIFE
plan.curl_kwargs()                       # a TLS target and headers that agree
```

```bash
omk-crawl https://example.com --evasion          # plan + audit + offline score
omk-crawl https://example.com --evasion --json
python3 scripts/bench_evasion.py                 # three-strategy comparison
```

## tcp.py - the TCP/IP stack

A UA claiming Windows over a Linux SYN is a contradiction by itself. The stack is
derived from `FingerprintProfile.platform_os`, never chosen independently.

```python
from omk_crawl import stack_for, apply_to_socket

stack = stack_for(profile)     # windows-11 / macos-14 / linux-6 / android-14 / ios-17
stack.option_names()           # ['mss','nop','ws','nop','nop','sackOK'] - order is the tell
report = apply_to_socket(sock, stack)
report.applied                 # values the kernel actually accepted (TTL verified)
report.unsupported             # kernel-owned: window_scaling, options, timestamps, df
```

Only what `setsockopt` controls is applied. The rest is reported as unsupported
rather than claimed (constitution P1).

## tls.py - JA3/JA4 normalization

GREASE is redrawn per connection, so it must be stripped before hashing or the
fingerprint is unstable. The model is audited; the real handshake remains
`curl_cffi`'s.

```python
from omk_crawl import hello_for, normalize_grease, audit_tls

hello = hello_for(profile)         # per-family ClientHello model
hello.ja3(), hello.ja4_model()     # deterministic after GREASE removal
audit_tls(hello, profile)          # GREASE / ALPN / TLS1.3 vs the UA family
```

`drift_report(previous, current)` compares two hellos on the temporal axis and treats
GREASE-only churn as stable.

## browser_props.py - the JS object surface

`navigator.*`, `screen.*`, the Intl timezone, the unmasked WebGL renderer, PDF
plugins and canvas/audio noise are all derived from the profile.

```python
from omk_crawl import property_spec, spoof_script, audit_props

spec = property_spec(profile, seed)   # same (profile, seed) -> byte-identical
script = spoof_script(spec)           # seeded LCG pins the canvas noise
audit_props(observed, profile)        # platform / locale / tz / screen>=viewport / renderer
```

A device that resamples its canvas hash on every visit is itself a signal. One seed
produces one noise, so the hash is stable where it should be.

## cdp.py - CDP leak patching

```python
from omk_crawl import patch_plan, patch_js, audit_cdp, cdp_probe_script

plan = patch_plan(profile, seed)   # deterministic; patching is keyed on the fragment
js = patch_js(plan)                # one IIFE, nothing attached to window
page.evaluate(cdp_probe_script())  # self-check before a real site sees anything
audit_cdp(surface)                 # leaks, worst first
```

Two rules are enforced structurally:

- **A patch must look native.** If a patched function exposes its source through
  `Function.prototype.toString`, the patch is a new tell. Every injection goes
  through a closure-scoped `native()` wrapper reporting `[native code]`.
- **Nothing is attached to `window`.** A global such as `__omkNative` would be a
  unique token for a detector to key on.

What JS cannot fix is reported honestly in `plan.driver_fixed`. `Runtime.enable` is
protocol state and cannot be undone from a script; the fix is driver choice
(nodriver/patchright).

## behavior.py - typing dynamics and session rhythm (v2.14 extension)

```python
from omk_crawl import BehaviorClock

clock = BehaviorClock("site|session")
for k in clock.typing_plan("hello world"):     # key hold/flight + neighbour-key typos
    if k.char == "\b": page.keyboard.press("Backspace")
    else: page.keyboard.type(k.char, delay=k.dwell_ms)
for phase in clock.session_rhythm(40):         # bursts of 3-7 pages, pauses of 8-420 s
    ...
```

The burst/pause structure is evasion and politeness at once (constitution P8): the
pauses are real waiting, not decoration.

## captcha.py - challenge classification and policy

This is the extension point v2.12 left open, made concrete. It is not a bypass tool.

```python
from omk_crawl import classify_captcha, resolve_challenge, HttpSolver

challenge = classify_captcha(html)         # 11 families + NONE + UNKNOWN
plan = resolve_challenge(challenge, url)   # proceed / clearance_flow / solver / refuse
plan.action, plan.reason                   # a refusal always carries its reason
```

| Decision | Applies to | Action |
| -------- | ---------- | ------ |
| No challenge | `NONE` | `proceed` |
| Clearable in-browser | Turnstile, reCAPTCHA v3, DataDome, Kasada, AWS WAF | `clearance_flow` (no cost, no third party) |
| Needs a person | image grid, slider, press-and-hold, `UNKNOWN` | `refuse` - never sent to a solver |
| Operator opt-in | clearable kinds with `prefer_solver=True` | `solver` |

- **Human-judgement challenges are never routed to a backend.** `resolve_challenge`
  refuses them before it looks at what backends exist, so a configuration mistake
  cannot route an image grid to a solver.
- **No credentials in source.** `HttpSolver` is inert unless both
  `OMK_CAPTCHA_ENDPOINT` and `OMK_CAPTCHA_KEY` are set; otherwise it fails closed with
  `NO_CREDENTIAL`.
- A login wall (`AUTH_REQUIRED`) remains out of scope (constitution P3).

## evasion.py and verify.py - arbiter and self-check

`plan_for(url)` binds the six layers into one identity and `coherence_report()`
re-audits each of them. The mock detector in `verify.py` scores seven axes with no
network access.

```python
from omk_crawl import evasion_score, evasion_surface, plan_for

plan_for("https://example.com").coherence_report().to_dict()   # per-layer verdicts
evasion_score(evasion_surface("https://example.com"))          # offline score
```

## Measured benchmark (offline, v2.14.0)

| Strategy | Score | Detected axes |
| -------- | ----- | ------------- |
| `stock` (plain library client) | 0.643 | tls_family, behavior_cadence |
| `naive_stealth` (browser headers glued on) | **0.059** | all six axes |
| `omk_evasion` (coherent plan) | **1.000** | none |

That `naive_stealth` scores *worse* than `stock` is the claim of arXiv:2606.30119,
and this benchmark shows it as a measurement rather than a restatement. Plan
construction costs 0.02 ms at p50.
