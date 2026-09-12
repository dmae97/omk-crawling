# Implementation Plan: Unblockable Breakthrough Layer (v2.12)

> Spec: `./spec.md` · 헌법: `.specify/memory/constitution.md`

## 기술 설계

### 목표 아키텍처

```text
crawl(url) ──▶ SmartRouter
                 │  (001 코어: 탐지 → 재정렬 → 에스컬레이션)
                 ▼
   ESCALATION_CHAIN (v2.12, light → heavy)
   ⓪ insane_search  8-profile TLS rotation + stealth fallback
   ① curl_cffi      TLS impersonation, no browser
   ② crawl4ai       vanilla Playwright render + Markdown
   ③ scrapling      stealth fetcher + anti-bot tricks
   ④ camoufox       anti-detect Firefox (C++-level FP injection)   ← NEW
   ⑤ patchright     patched undetected Playwright                  ← NEW
   ⑥ nodriver       CDP-native Chrome (no WebDriver artifacts)     ← NEW
   ⑦ browser_use    LLM agent (last resort)

warm_crawl(url) ──▶ SessionWarmup (warmup.py)                      ← NEW
   get(cache) ─miss─▶ acquire: nodriver→patchright→camoufox 로
                      사이트 루트 사람처럼 진입(behavior.py) → 쿠키 수확
   └─▶ WarmSession.curl_kwargs(): 동일 지문(TLS+헤더+쿠키) 리플레이
       차단 시 → invalidate + SmartRouter 폴��

fingerprint.py (P7) — 정체성 단일 소스                          ← NEW
   FingerprintProfile: impersonate↔UA↔ClientHints↔locale/tz↔viewport
   coherence_issues(): 5-룰 크로스레이어 감사
   profile_for(url): 사이트별 결정적 선택(시간축 일관성)

behavior.py — 시드 결정적 인간 행동                              ← NEW
   think_time/dwell_time/inter_request_delay (lognormal/gauss)
   mouse_path (Bezier+jitter), scroll_plan (청크+jitter)

detect.py 확장 — KASADA / PERIMETERX / AWS_WAF 마커 + BlockType   ← NEW
routing.py 확장 — DEFAULT_ORDER 8종 + 벤더별 ROUTE_TABLE + 우선순위
```

### 핵심 설계 결정

| 결정 | 선택 | 근거 |
| ------ | ------ | ------ |
| D1 | 승부처를 "트릭 수"가 아니라 **일관성**에 둔다 (fingerprint.py) | FP-Inconsistent(arXiv:2406.07647): 우회 봇은 크로스속성·시간축 불일치에서 검출. 멀티레이어 지문(arXiv:2606.30119): 어설픈 스텔스 패치는 오히려 탐지율 상승 |
| D2 | 사이트별 지문 고정(`profile_for`, sha256 도메인 키잉) | 같은 사이트에 지문이 바뀌면 시간축 불일치로 플래그 (D1의 두 번째 축) |
| D3 | 웜업-리플레이 분리(무거운 브라우저 1회 → 가벼운 리플레이 N회) | 클리어런스 토큰(cf_clearance·datadome·_abck·aws-waf-token)이 벤더의 실제 통과 증명 — TLS/JA4 연구(arXiv:2602.09606)상 토큰과 TLS를 같이 재현해야 유효 |
| D4 | 브라우저 3종을 무게·강도 순 배치(camoufox→patchright→nodriver) | 2026 스텔스 브라우저 실측 우선순위: camoufox(엔진 레벨 주입) → patchright(Playwright 호환) → nodriver(CDP 네이티브, DataDome/Kasada 최강) |
| D5 | 행동 난수는 전부 시드 결정적 | 재현·테스트 가능(P6) + 세션 내 시간축 일관성(D1) |
| D6 | 신규 블록 유형을 Flag로 추가, 우선순위는 AUTH 최상위 유지 | 복합 탐지(KASADA | PERIMETERX) 해석 일관 + P3 불변 |
| D7 | 프록시 검증 키를 소스에서 제거하고 env 전용으로 | 하드코딩 크레덴셜 = P2 위반. `validate_batch` 진입 시 부재면 즉시 실패 |

### 리서치 근거

**arXiv (2024–2026)**

- arXiv:2406.07647 — FP-Inconsistent: 탐지기는 지문 속성 간·시간축 불일치를 직접 검출 → D1/D2
- arXiv:2606.30119 — 멀티레이어(네트워크/HTTP/브라우저) 웹 에이전트 지문: 어설픈 스텔스가 탐지를 높임 → D1/D4
- arXiv:2602.09606 — TLS/JA4 핸드셰이크만으로 bad-bot 검출 → curl_cffi impersonation + 토큰 리플레이 정합성(D3)
- arXiv:2606.14525 — 봇 탐지 보급률·기법 분류(2026) → 탐지 벤더 커버리지(Kasada/PX/AWS WAF 추가)
- arXiv:2502.01608 — 실사용자 상호작용 속 지문: 정적 크롤이 놓치는 인증·상호작용 트리거 → 웜업 플로우(D3)

**라이브러리 (2026-08 기준)**

- camoufox ≥0.4 — 안티디텍트 Firefox, 지문을 C++/엔진 레벨에서 주입, Playwright 숨김, humanize·geoip
- patchright ≥1.55 — 패치된 undetected Playwright(드롭인)
- nodriver ≥0.48 — undetected-chromedriver 공식 후속, CDP 직결, WebDriver 아티팩트 없음
- 유지/확인: crawl4ai 0.9.x(안티봇 탐지+프록시 에스컬레이션 내장), scrapling 0.4.12, curl_cffi 0.15.0, browser-use 0.13.7, crawlee 1.8.3

## 변경 대상 모듈

| 파일 | 변경 | 이유 |
| ------ | ------ | ------ |
| `omk_crawl/fingerprint.py` | 신규 | FR-1/FR-2, P7 |
| `omk_crawl/behavior.py` | 신규 | FR-3 |
| `omk_crawl/warmup.py` | 신규 | FR-4/5/6 |
| `omk_crawl/tools/{camoufox,nodriver,patchright}_tool.py` | 신규 | FR-7 |
| `omk_crawl/detect.py` | KASADA/PERIMETERX/AWS_WAF +_TOOL_MODULES | FR-8 |
| `omk_crawl/routing.py` | DEFAULT_ORDER 8종, ROUTE_TABLE 벤더별, _PRIORITY | FR-8/FR-9 |
| `omk_crawl/tools/__init__.py` | 레지스트리+체인 갱신 | FR-7 |
| `omk_crawl/__init__.py` | 공개 API + **version** 2.12.0 | 문서 동기화 |
| `omk_crawl/tools/curl_cffi_tool.py` | ProxySpec/헤더 타입 정합 | 사전 존재 타입 오류 정리 |
| `omk_crawl/tools/proxy_engine_v2.py` | 키 env화 + fail-closed + lazy aiohttp | NFR-5, P2 |
| `tests/test_breakthrough.py` | 신규 47 테스트 | AC-1~6 |
| `tests/test_core.py` | 체인 순서 갱신 | FR-7 |

## 데이터/상태 모델

| 상태 | 경로 | 스키마 | 수명 |
|------|------|--------|------|
| WarmSession | `~/.cache/omk-crawl/warm-sessions.json` (env `OMK_CRAWL_WARM_SESSIONS`) | `{version, sessions{domain{cookies,profile,acquired_at,tool,ttl}}}` | TTL 기본 1800s, 만료 시 get에서 자동 폐기. tmp+rename 원자 쓰기, 0600 |

## 보안·가드레일 설계

- 세션 쿠키는 로컬 캐시에만, 0600, TTL 제한 — 리플레이는 같은 도메인에만.
- 웜업 브라우저는 headless 기본, `respect_robots`/도메인 페이싱은 라우터와 동일 정책.
- 프록시 검증(Firebase)은 env 키 없으면 즉시 RuntimeError (P2 fail-closed).
- `.gitleaksignore`에 정당한 억제 사유 기록; 스캔 트리에서 시크릿 형상 데이터 제거.

## 테스트 전략 (P6)

- fingerprint: 내장 프로필 자기 일관성, 불일치 5-룰 각각, profile_for 결정성/솔트, match_impersonate.
- behavior: 동일 시드 동일 열, 경계값(clamp), 마우스 경로 양끝점, 스크롤 계획 커버리지.
- warmup: TTL/클리어런스 판정/curl_kwargs 일관성, 직렬화 왕복, 영속화 재기동, 드라이버 전부 실패 시 RuntimeError, warm_crawl 리플레이 성공·폐기·폴��(curl_cffi/SmartRouter monkeypatch).
- detect/routing: 신규 벤더 마커, 테이블 ⊆ ALL_TOOLS, 벤더별 1순위, AUTH 빈 리스트.
- 어댑터: 계약(layer/needs_browser/capabilities), 미설치 fail-closed, nodriver sync-in-loop 가드.

## 롤아웃

- 버전 2.12.0. extras 추가: `camoufox` / `nodriver` / `patchright` (+`all`).
- 문서 동기화: SKILL.md, README.md, CHANGELOG.md, references/(tools 3종 신규 + routing.md), specs/.
- 하위 호환: 공개 API는 추가만, 기존 시그니처 불변. `ESCALATION_CHAIN` 순서는 확장(문서화).
