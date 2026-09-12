# Tasks: Smart Escalation Core (as-built, v2.11)

> Spec: `./spec.md` · Plan: `./plan.md`
> Retro: 이미 구현·검증된 항목을 체크 상태로 기록한다 (헌법 P1: 증거 = 테스트 스위트).

## Phase 1 — Foundation

- [x] **T1** [result] `CrawlResult`/`CrawlStatus` 단일 결과 모델 — 검증: `tests/test_core.py::TestCrawlResult`
- [x] **T2** [detect] `BlockType` Flag + `detect_block` 휴리스틱(CF/Akamai/DataDome/Imperva/WAF/TLS/JS/429/401) — 검증: `tests/test_core.py::TestDetection`
- [x] **T3** [tools] `BaseTool` 계약(capabilities, contract_metadata,_missing/_error) — 검증: `tests/test_contract.py`
- [x] **T4** [routing] `ROUTE_TABLE`/`preferred_order`/`reorder_tools` — 검증: `tests/test_routing.py`

## Phase 2 — Implementation

- [x] **T5** [router] `SmartRouter` + rate limit + 지수 백오프 재시도 — 검증: `tests/test_router.py`
- [x] **T6** [route_engine] sync/async 에스컬레이션 루프 + 1회 재정렬 + best-attempt 반환 — 검증: `tests/test_router.py`
- [x] **T7** [routing] `SiteMemory` Bayesian 학습 + 원자적 영속화(flock, 0600) — 검증: `tests/test_site_memory.py`
- [x] **T8** [tools] 웹 어댑터 5종(insane_search, curl_cffi, crawl4ai, scrapling, browser_use) + 추출/변환 + 타깃 + 모바일 — 검증: `tests/test_core.py::TestToolRegistry`
- [x] **T9** [resilience/stability] retry·TokenBucket·CircuitBreaker·SessionManager — 검증: 패키지 공개 API import
- [x] **T10** [adaptive] AdaptiveFetcher API 디스커버리, [cookies] CookieManager — 검증: import 경로
- [x] **T11** [cli] `omk-crawl` 엔트리 + diagnose — 검증: `tests/test_cli.py`

## Phase 3 — Quality Gates

- [x] **T12** `python3 -m pytest tests/ -q` — 204 passed (v2.11 기준선, live 제외)
- [x] **T13** `ruff check omk_crawl/ tests/` — 0 errors
- [x] **T14** CI: PyPI 게시 idempotent + live 테스트 기본 제외 — `.github/workflows`

## Phase 4 — Docs Sync

- [x] **T15** SKILL.md(v2.11.0) / README.md / CHANGELOG.md / NOTICE.md 갱신
