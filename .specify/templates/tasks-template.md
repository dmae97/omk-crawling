# Tasks: [FEATURE NAME]

> Spec: `./spec.md` · Plan: `./plan.md`
> 규칙: 각 태스크는 독립 검증 가능·헌법 P1(증거) 준수·완료 시 [x].

## Phase 1 — Foundation

- [ ] **T1** [모듈] 설명 — 검증: `pytest tests/test_x.py -q`

## Phase 2 — Implementation

- [ ] **T2** …

## Phase 3 — Quality Gates

- [ ] **Tn** `pytest tests/ -q` 전부 통과
- [ ] **Tn** `ruff check omk_crawl/ tests/` 0 errors
- [ ] **Tn** `gitleaks dir .` 0 findings

## Phase 4 — Docs Sync (헌법 §문서 동기화)

- [ ] **Tn** SKILL.md / README.md / CHANGELOG.md / references/ 갱신
