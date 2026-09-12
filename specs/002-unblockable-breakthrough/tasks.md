# Tasks: Unblockable Breakthrough Layer (v2.12)

> Spec: `./spec.md` · Plan: `./plan.md` · 헌법: `.specify/memory/constitution.md`
> 상태: 전부 구현·검증 완료 (증거: `tests/test_breakthrough.py` 47건 포함 251건 통과).

## Phase 1 — Foundation (일관성 코어)

- [x] **T1** [fingerprint] `FingerprintProfile` + 내장 프로필 5종(chrome-win/mac/linux, firefox-win, safari-mac) — 검증: `TestFingerprintProfiles::test_builtin_profiles_are_self_coherent`
- [x] **T2** [fingerprint] `coherence_issues()` 5-룰 감사(CH-on-non-Chromium, 플랫폼, 메이저 버전, 모바일, impersonate 정합) — 검증: `test_coherence_audit_catches_*`
- [x] **T3** [fingerprint] `profile_for(url, salt)` 도메인 결정적 선택 + `match_impersonate` — 검증: `test_profile_for_is_deterministic_per_site`
- [x] **T4** [behavior] `BehaviorClock` — lognormal think/inter-request, dwell, Bezier mouse_path, scroll_plan, 전부 시드 결정적 — 검증: `TestBehaviorClock` 8건

## Phase 2 — Implementation (웜업·어댑터·탐지·라우팅)

- [x] **T5** [warmup] `WarmSession`(TTL/clearance/curl_kwargs/직렬화) + `SessionWarmup`(record/get/invalidate/acquire) — 검증: `TestWarmSession`, `TestSessionWarmup`
- [x] **T6** [warmup] 드라이버 체인 acquire(nodriver→patchright→camoufox), 전부 실패 시 RuntimeError (P2) — 검증: `test_acquire_fail_closed_when_no_driver`
- [x] **T7** [warmup] `warm_crawl` — 캐시→획득→리플레이→폐기→라우터 폴�� — 검증: `TestWarmCrawl` 2건
- [x] **T8** [tools] `CamoufoxTool`(sync+async, humanize/geoip) — 검증: 계약 + `test_missing_tool_fails_closed`
- [x] **T9** [tools] `NodriverTool`(CDP, sync-in-loop 가드) — 검증: `test_nodriver_sync_fetch_rejects_running_loop`
- [x] **T10** [tools] `PatchrightTool`(지문 일관 컨텍스트) — 검증: `test_browser_layer_contract`
- [x] **T11** [detect] KASADA/PERIMETERX/AWS_WAF BlockType + 마커 — 검증: `TestNewWafDetection` 4건
- [x] **T12** [routing] DEFAULT_ORDER 8종 + 벤더별 ROUTE_TABLE + _PRIORITY(AUTH 최상위) — 검증: `TestRoutingTables` 6건
- [x] **T13** [tools/registry] ESCALATION_CHAIN 갱신 + ALL_TOOLS 등록 — 검증: `test_registered_and_in_chain`, `test_core.py::test_escalation_order`
- [x] **T14** [**init**] 공개 API exports + `__version__ = "2.12.0"` — 검증: import 스모크
- [x] **T15** [pyproject] version 2.12.0 + extras(camoufox/nodriver/patchright) + `all` 갱신 — 검증: TOML 파싱

## Phase 3 — Security & Hardening

- [x] **T16** [proxy_engine_v2] Firebase 검증 키 소스 제거 → `OMK_PROXY_VALIDATE_KEY` env + `validate_batch` fail-closed — 검증: `gitleaks dir .` 0 findings
- [x] **T17** [quarantine] 스캔 트리에서 시크릿 형상 아티팩트 격리(`~/.cache/omk-crawling-quarantine/`, 복구 절차 포함) — 검증: 재스캔 0 findings
- [x] **T18** [lint] pre-existing ruff 오류 정리(backtrack_guard, stealth_decision, proxy_engine_v2, freshness_engine) + lazy aiohttp — 검증: `ruff check omk_crawl/ tests/` 0 errors

## Phase 4 — Quality Gates

- [x] **T19** `python3 -m pytest tests/ -q` — 251 passed, 2 deselected(live)
- [x] **T20** `ruff check omk_crawl/ tests/` — All checks passed
- [x] **T21** `gitleaks dir .` — no leaks found (+ `.gitleaksignore` 정당 사유 기록)
- [x] **T22** basedpyright 신규 모듈 0 errors (기존 strict 잔여는 베이스라인 문서화)

## Phase 5 — Docs Sync (헌법 §문서 동기화)

- [ ] **T23** SKILL.md v2.12.0 + 3 도구 + 돌파 레이어 + 라우팅 표 갱신
- [ ] **T24** README.md 돌파 레이어 섹션 + 체인 갱신
- [ ] **T25** CHANGELOG.md v2.12.0 엔트리
- [ ] **T26** references/tools/{camoufox,nodriver,patchright}.md 신규 + references/routing.md 갱신
- [ ] **T27** spec-kit 최신화: constitution + 001(as-built) + 002(본 스펙) — 본 태스크로 완료
