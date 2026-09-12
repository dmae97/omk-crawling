# Feature Specification: Unblockable Breakthrough Layer (v2.12)

> 헌법: `.specify/memory/constitution.md` · 기반: `../001-smart-escalation-core/spec.md`
> Plan: `./plan.md` · Tasks: `./tasks.md`

## 개요

v2.11의 에스컬레이션 코어 위에 **돌파(breakthrough) 레이어**를 얹는다.
목표: *접근 권한이 있는 콘텐츠에 대해* 어떤 안티봇 스택(Cloudflare,
DataDome, Kasada, PerimeterX/HUMAN, AWS WAF, Imperva, Akamai) 앞에서도
바이트를 확보한다. 승부처는 "더 많은 트릭"이 아니라 **크로스레이어
핑거프린트 일관성 + 인간 행동 리얼리즘 + 세션 재사용**이다 — 2024–2026
arXiv 연구가 지목하는 탐지 축 그 자체.

## 배경과 문제

v2.11의 한계:

1. 브라우저 선택지가 vanilla Playwright(crawl4ai)와 scrapling뿐 — DataDome/
   Kasada/PerimeterX 같은 행동 기반 벤더가 Playwright 아티팩트를 잡아낸다.
2. 도구마다 UA/헤더/TLS가 제각각 — 레이어 간 불일치로 탐지된다
   (FP-Inconsistent, arXiv:2406.07647).
3. 요청 간 간격이 기계적 — cadence 탐지에 노출.
4. 챌린지 통과 쿠키(cf_clearance 등)를 매번 새로 얻는다 — 비용과 탐지 노출
   증가.

## 사용자 스토리

| ID | 스토리 | 우선순위 |
| ---- | -------- | ---------- |
| US-1 | 운영자로서 DataDome/Kasada 사이트에서도 CDP-네이티브 또는 안티디텍트 브라우저가 자동 선택되길 원한다 | P1 |
| US-2 | 운영자로서 모든 도구가 동일한 지문 이야기(TLS=헤더=브라우저)를 하길 원한다 — 내가 신경 쓰지 않아도 | P1 |
| US-3 | 운영자로서 한 번 통과한 세션을 가벼운 클라이언트로 재사용해 비용을 낮추길 원한다 | P1 |
| US-4 | 운영자로서 `warm_crawl(url)` 한 줄로 웜업→리플레이→폴백이 완결되길 원한다 | P2 |
| US-5 | 개발자로서 지문/행동이 시드로 재현 가능하길 원한다(디버그·테스트) | P2 |

## 요구사항

### Functional

- **FR-1**: `fingerprint.py`는 TLS impersonation·UA·Client Hints·Accept-Language·locale/timezone·viewport가 일치하는 `FingerprintProfile`을 제공하고, `coherence_issues()` 감사를 내장한다 (P7).
- **FR-2**: `profile_for(url)`은 사이트별로 동일 프로필을 결정적으로 반환한다(시간축 일관성).
- **FR-3**: `behavior.py`는 시드 결정적 인간 타이밍(think/dwell/inter-request)과 상호작용 계획(Bezier 마우스, 청크 스크롤)을 제공한다.
- **FR-4**: `warmup.py`는 실제 브라우저로 사이트에 사람처럼 진입해 클리어런스 쿠키를 수확하고(`SessionWarmup.acquire`), 도메인별 TTL 캐시(`WarmSession`)로 영속한다.
- **FR-5**: `WarmSession.curl_kwargs()`는 동일 지문(TLS+헤더+쿠키)으로 curl_cffi 리플레이 kwargs를 만든다.
- **FR-6**: `warm_crawl(url)`은 캐시→획득→리플레이→(차단 시)라우터 폴백을 한 호출로 수행한다.
- **FR-7**: 안티디텍트 브라우저 어댑터 3종을 에스컬레이션 체인에 추가한다: camoufox(C++ 레벨 지문 주입), patchright(패치된 Playwright), nodriver(CDP 네이티브 Chrome).
- **FR-8**: 탐지기가 KASADA/PERIMETERX/AWS_WAF를 식별하고 라우팅 테이블이 벤더별 최적 도구를 우선한다(DataDome/Kasada/PerimeterX → nodriver 우선).
- **FR-9**: 신규 블록 유형 우선순위에서 AUTH_REQUIRED가 여전히 최상위(우회 금지, P3).

### Non-Functional

- **NFR-1**: 신규 코어 모듈(fingerprint/behavior/warmup)은 zero-dep 유지 (P4).
- **NFR-2**: 신규 어댑터는 BaseTool 계약 + lazy import (P5), 미설치 시 `TOOL_MISSING` (P2).
- **NFR-3**: 전 테스트가 오프라인 결정적으로 통과 (P6) — 브라우저 경로는 monkeypatch.
- **NFR-4**: 웜 세션은 원자적 쓰기(0600)로 영속하고 TTL 만료 시 자동 폐기.
- **NFR-5**: 크레덴셜 하드코딩 0 — 프록시 검증 키는 환경변수(`OMK_PROXY_VALIDATE_KEY`)에서만 읽는다 (P2).

## 가드레일

- AUTH_REQUIRED 우회 금지 — 신규 벤더 유형 추가 후에도 우선순위 최상위 유지.
- 웜업·스텔스·지문 일관성은 접근 자격 있는 콘텐츠의 클라이언트 차별에 한정.
- 행동 시뮬레이션은 도메인당 최소 간격(P8) 위에서만 동작한다.

## 수용 기준

- [x] AC-1: 모든 내장 프로필이 `coherence_issues()==[]`를 통과하고, 불일치 조합은 감사가 잡아낸다 (단위 테스트).
- [x] AC-2: 같은 시드의 BehaviorClock 두 개는 같은 타이밍/경로 열을 만든다.
- [x] AC-3: `warm_crawl`이 (mock된) 세션 리플레이로 OK를 반환하고, 리플레이 실패 시 세션 폐기 + 라우터 폴백한다.
- [x] AC-4: KASADA/PERIMETERX/AWS_WAF 마커 HTML이 해당 BlockType으로 탐지된다.
- [x] AC-5: KASADA 탐지 시 선호 순서 1순위가 nodriver다.
- [x] AC-6: `pytest tests/ -q` 251건 통과, `ruff check` 0 errors, `gitleaks dir .` 0 findings.

## 범위 외

- CAPTCHA 솔버 SaaS 연동(2captcha 등) — 어댑터 확장점만 남긴다.
- 상주 프록시 풀 서비스화(현재 proxy_engine_v2/freshness_engine는 로컬).
- residential proxy 벤더 키 관리 UI.
- iOS Safari(mobile) 지문 프로필 확장.
