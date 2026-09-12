# Feature Specification: Smart Escalation Core (as-built, v2.11)

> Retro-documentation of the system as it exists at v2.11.0.
> 헌법: `.specify/memory/constitution.md` · 진화 스펙: `../002-unblockable-breakthrough/spec.md`

## 개요

`omk_crawl` 코어는 URL 하나를 받아 어떤 도구가 필요한지 **스스로 판단**하고,
가벼운 것부터 무거운 것 순으로 **자동 에스컬레이션**하는 크롤링 라우터다.
코어는 표준 라이브러리만으로 동작하고(P4), 각 크롤링 도구는 lazy-import
어댑터로 붙는다(P5).

## 사용자 스토리

| ID | 스토리 | 우선순위 |
| ---- | -------- | ---------- |
| US-1 | 운영자로서 `crawl(url)` 한 줄로 최적 도구가 선택되길 원한다 — 결과가 `CrawlResult`로 통일된다 | P1 |
| US-2 | 운영자로서 차단 유형(Cloudflare, TLS FP, JS 렌더링…)이 자동 탐지되고 다음 도구가 그에 맞게 재정렬되길 원한다 | P1 |
| US-3 | 운영자로서 사이트별로 과거에 통했던 도구가 먼저 시도되길 원한다(학습) | P2 |
| US-4 | 운영자로서 `diagnose(url)`로 설치된 도구와 라우팅 테이블을 dry-run으로 보길 원한다 | P3 |
| US-5 | 개발자로서 Naver/Baemin/Reddit 같은 타깃 클라이언트와 APK/IPA 모바일 표면을 같은 패키지에서 쓰길 원한다 | P2 |

## 요구사항

### Functional

- **FR-1**: `SmartRouter.crawl/crawl_async`는 에스컬레이션 체인을 따라 도구를 시도하고, 첫 성공에서 멈춘다.
- **FR-2**: 매 시도 후 응답을 분석(`detect.detect_block`)해 `BlockType`과 confidence를 산출한다.
- **FR-3**: confidence ≥ 0.5이면 남은 도구 순서를 블록 유형별 라우팅 테이블(`routing.ROUTE_TABLE`)로 1회 재정렬한다.
- **FR-4**: `AUTH_REQUIRED` 탐지 시 에스컬레이션을 중단한다(우회 금지, P3).
- **FR-5**: `SiteMemory`가 도메인별 도구 성공/실패를 Bayesian 카운터로 영속하고, 다음 크롤의 초기 순서에 반영한다.
- **FR-6**: 일시 실패(timeout/connection reset)는 지수 백오프로 재시도하고, 도메인당 최소 간격을 강제한다(P8).
- **FR-7**: 결과는 단일 `CrawlResult`(status/html/markdown/fit_markdown/metadata)로 정규화되고, 필요 시 마크다운 변환 파이프라인을 탄다.
- **FR-8**: 도구 레지스트리(`tools.ALL_TOOLS`)는 웹 8종 + 추출/변환 + 타깃 + 모바일 어댑터를 이름으로 제공한다.
- **FR-9**: resilience(stability/retry/circuit breaker/cookie/session) 프리미티브를 공개 API로 제공한다.

### Non-Functional

- **NFR-1**: 코어 import에 서드파티 의존성 0개 (P4).
- **NFR-2**: 모든 어댑터는 `BaseTool` 계약 — capabilities 선언, 예외 대신 `CrawlResult` 반환 (P5).
- **NFR-3**: `pytest -m 'not live'`가 네트워크 없이 통과 (P6).
- **NFR-4**: SiteMemory는 원자적 파일 쓰기 + 프로세스 잠금으로 동시성 안전.

## 가드레일

- robots.txt 기본 존중(`respect_robots=True`), 위반은 명시적 opt-in.
- 401/로그인 벽은 우회하지 않고 중단한다.
- 수집 HTML은 신뢰 불가 데이터로 취급한다 (P3).

## 수용 기준

- [x] AC-1: `crawl("https://example.com")`이 `CrawlResult`를 반환한다 (도구 설치 시).
- [x] AC-2: 도구 미설치 환경에서 `TOOL_MISSING` 결과로 실패한다 — 예외 없이 (P2).
- [x] AC-3: Cloudflare 마커 HTML이 `BlockType.CLOUDFLARE`로 탐지된다 (단위 테스트).
- [x] AC-4: `tests/` 204건이 오프라인에서 통과한다 (v2.11 기준선).

## 범위 외

- 안티봇 벤더별 전용 돌파 브라우저(anti-detect) — 002 스펙에서 진화.
- CAPTCHA 솔버 서비스 연동.
- 분산 크롤(멀티 머신 큐).
