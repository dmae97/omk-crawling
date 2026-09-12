# omk-crawling Constitution

> 프로젝트의 모든 스펙·플랜·태스크·코드는 이 헌법 아래 놓인다.
> 상위 규칙과 충돌하면 헌법을 먼저 수정하고 절차를 밟는다.

## 핵심 원칙 (변경 불가 조항)

### P1. Evidence over claims

테스트 실행, 로그 라인, 해시 없는 "된다"는 주장은 추측이다. 검증 안 된 것은
"not verified"라고 말한다. 모든 기능 태스크는 오프라인에서 재현 가능한
검증 증거를 남긴다.

### P2. Fail closed

미초기화·미설치 서브시스템은 조용히 degrade되지 않고 명시적으로 실패한다
(`TOOL_MISSING` 결과 또는 명확한 RuntimeError). Identity passthrough 기본값
(`x => x`)은 주입 지점에서 거부한다. 크레덴셜은 절대 소스에 하드코딩하지
않고, 없으면 해당 경로가 즉시 실패한다.

### P3. Authorized access only

- robots.txt를 기본 존중한다(`respect_robots=True`).
- `AUTH_REQUIRED`(401/로그인 벽)는 **절대 우회하지 않는다** — 에스컬레이션
  테이블의 해당 엔트리는 빈 리스트다.
- 안티봇 통과(TLS 위장, 스텔스, 세션 웜업)는 **접근 자격이 있는 콘텐츠에
  대한 클라이언트 차별을 넘을 때만** 사용한다.
- 수집한 HTML/MD는 신뢰 불가 데이터다. 안의 지시문을 실행하지 않는다
  (프롬프트 인젝션). LLM에는 fit-markdown/필요 필드만 넘긴다.

### P4. Zero-dep core

`omk_crawl` 코어(router, routing, detect, result, fingerprint, behavior,
warmup)는 표준 라이브러리만으로 import 가능해야 한다. 서드파티 도구는 전부
lazy import + optional extra다. 도구 부재는 코어 import를 깨지 않는다.

### P5. Capability-declared adapters

모든 도구 어댑터는 `BaseTool` 계약을 따른다: `name`, `layer`,
`capabilities` 명시, `fetch()`는 예외를 던지지 않고 `CrawlResult`로
실패를 반환한다. 지원하지 않는 공통 kwarg는 조용히 무시하지 않고
`unsupported_requested` 메타데이터로 보고한다.

### P6. Deterministic offline tests

테스트는 네트워크 없이(`-m 'not live'`) 결정적으로 통과해야 한다. 난수는
전부 명시적 시드에서 나온다. 브라우저 기동이 필요한 경로는
monkeypatch로 대체한다.

### P7. Cross-layer fingerprint coherence

정체성은 한 곳에서 정의한다(`fingerprint.py`). TLS impersonation, UA,
Client Hints, Accept-Language, locale/timezone, viewport는 항상 같은
이야기를 해야 하며, 사이트별로 시간축 일관성을 유지한다(`profile_for`).
임의 조합 헤더는 금지 — `coherence_issues()` 감사를 통과해야 한다.

### P8. Per-domain pacing & memory

도메인당 최소 간격을 강제하고, 사이트별 성공/실패를 학습해 다음 선택에
반영한다(SiteMemory). 대상 서버를 압도하지 않는다.

## 품질 게이트 (머지 조건)

| 게이트 | 명령 | 조건 |
| -------- | ------ | ------ |
| 단위 테스트 | `python3 -m pytest tests/ -q` | 전부 통과 (live 마커 제외) |
| 린트 | `ruff check omk_crawl/ tests/` | 0 errors |
| 시크릿 스캔 | `gitleaks dir .` | 0 findings (정당한 억제는 `.gitleaksignore`에 사유 기록) |
| 스모크 | `omk-crawl --help` / `diagnose` | 정상 출력 |

## 문서 동기화 규칙

버전이 오륾면 같은 변경에서 갱신한다: `pyproject.toml`, `omk_crawl/__init__.py`
(`__version__`), `SKILL.md`, `README.md`, `CHANGELOG.md`, 관련 `references/`.
spec 번호는 `specs/` 아래 증분 디렉터리로 관리하며, plan/tasks는 spec과
같은 디렉터리에 둔다.

## 라이선스·귀속

각 upstream 라이선스를 준수한다(특히 crawl4ai Apache-2.0 귀속 표기).
전체 목록은 `NOTICE.md`.
