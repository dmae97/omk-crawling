# Implementation Plan: Smart Escalation Core (as-built, v2.11)

> Spec: `./spec.md` · 헌법: `.specify/memory/constitution.md`

## 기술 설계

### 아키텍처 맵

```text
user code / CLI (omk_crawl.cli)
   │
   ▼
SmartRouter (router.py)  ── lazy ──▶ route_engine.py (sync/async 에스컬레이션 루프)
   │                                      │
   │            ┌─────────────────────────┼──────────────────────────┐
   │            ▼                         ▼                          ▼
   │      tools/ 어댑터군            detect.py                   routing.py
   │   ESCALATION_CHAIN          BlockType 탐지               ROUTE_TABLE
   │   (BaseTool 계약,           (CF/Akamai/DataDome/…       preferred_order
   │    lazy import, P5)          confidence)                SiteMemory(영속)
   │            │
   │            ▼
   │   result.py (CrawlResult 정규화) → pipeline.py (마크다운/추출)
   │
   ├─ resilience.py (retry, TokenBucket, cache, ImpersonateRotator)
   ├─ stability.py  (CircuitBreaker, SessionManager, TimeoutBudget)
   ├─ adaptive.py   (AdaptiveFetcher — API 디스커버리)
   ├─ naver.py / baemin.py / reddit.py (타깃 클라이언트)
   └─ mobile/ (apk_analyze, ipa_analyze, appstore, device/scrcpy)
```

### 핵심 설계 결정

| 결정 | 선택 | 근거 |
| ------ | ------ | ------ |
| D1 | 코어 zero-dep, 도구는 lazy import (P4) | 미설치 환경에서도 router/diagnose/CLI 동작 |
| D2 | 예외 대신 `CrawlResult` 반환 계약 (P5) | 에스컬레이션 루프가 실패를 데이터로 다룸 |
| D3 | 탐지-주도 재정렬은 1회만 (`rerouted` 플래그) | 루프 안정성 — 재정렬 진동 방지 |
| D4 | SiteMemory = Bayesian (s+1)/(s+f+2) + 원자적 JSON | cold-start에서도 사전분포로 안정 순위 |
| D5 | `_RouteEngine` Protocol + import_module 지연 주입 | 순환 import 회피, fail-closed 초기화 검사 (P2) |
| D6 | BlockType을 Flag(비트 조합)으로 | 복합 차단(CF+TLS)을 하나의 값으로 표현 |

### 에스컬레이션 루프 (route_engine)

1. 체인 구성: `tools.ESCALATION_CHAIN`에서 `available()` 필터 → SiteMemory 순위 적용.
2. robots.txt 검사(옵션) → 도구 fetch (rate-limit + 지수 백오프 재시도).
3. `detect_block` 분석 → 성공이면 마크다운 보장 후 반환.
4. 실패 시: hard ERROR면 중단 / AUTH면 중단 / confidence≥0.5면 1회 재정렬.
5. 전부 실패하면 최고 점수 결과 + `escalation_exhausted` 메타 반환.

## 데이터/상태 모델

| 상태 | 경로 | 스키마 | 원자성 |
|------|------|--------|--------|
| SiteMemory | `~/.cache/omk-crawl/site-memory.json` (env `OMK_CRAWL_SITE_MEMORY`) | `{version, domains{domain{tool{successes,failures,total_latency_ms}}}}` | tmp+rename, flock, 0600 |

## 보안·가드레일 설계

- 시크릿 0 — 타깃 클라이언트의 쿠키/토큰은 런타임 주입만.
- robots fail-open(읽기 실패 시 허용)하되 `respect_robots` 기본 True.
- AUTH_REQUIRED → 에스컬레이션 테이블 `[]` → 즉시 중단.

## 테스트 전략 (P6)

- `test_core.py`: CrawlResult, 탐지 휴리스틱, 레지스트리, router diagnose, pipeline.
- `test_router.py` / `test_routing.py`: 재정렬·SiteMemory·우선순위.
- `test_contract.py`: 모든 어댑터의 BaseTool 계약.
- `test_site_memory.py`: 영속화 원자성·동시성.
- live 네트워크는 `-m live`로 분리, 기본 deselect.

## 롤아웃

- v2.11.0: star 버튼 CLI, CI PyPI 게시 안정화. 문서: SKILL.md/README/CHANGELOG 동기화 완료.
