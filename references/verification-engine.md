# 엔진 통합 검증 (2026-09-12)

## 변경 단위

- `targets.py`, `route_engine.py`, `router.py`, `cli.py`: CLI·Python·dry-run의 대상 분기 통합.
- `execution.py`, `request_runner.py`, `tools/base.py`: 요청별 예산·실행 기록·지원 조건 검사와
  반환형/발생형 예외 처리. HTTP 인증·요청 제한 상태와 헤더를 잃지 않도록 정규화했다.
- `tools/insane_search_tool.py`, `detect.py`, `stability.py`: 어댑터 내부 deadline,
  SDK 가용성·프로필 검증, 실제 브라우저 응답 상태와 종료 처리.
- `.github/workflows/ci.yml`: 테스트가 필요로 하는 기존 `curl` extra를 CI 설치에 명시.
  제품의 필수 런타임 의존성은 추가하지 않았다.

## 관측 결과

| 검사 | 결과 |
| --- | --- |
| 첫 회귀 실행 | 신규 테스트 52개 RED 확인 후 구현 |
| 마지막 허용 호출의 성공 처리 | 2개 RED 후 GREEN |
| 추가 경계 | 명시적 웹 어댑터의 robots, 로컬 입력 지연, 내부 브라우저 정책, 서버 backpressure, SDK 가용성, 만료 후 브라우저 시작을 검증 |
| SDK 프로필 | 무효 프로필 1개 RED, 유효 프로필/세션 재사용 대조 1개 PASS 후 수정 |
| 반환형/발생형 HTTP 오류 | 7개 RED 후 수정; 401·429·500의 상태와 종료 의미 보존 |
| 실제 기본 CLI + 로컬 HTTP 서버 | `503 → 200`, HTTP 2회, 브라우저 없이 성공; 서버 스레드 종료 확인 |
| 격리된 core+dev 환경 | HTTP/브라우저 SDK 없이 핵심 회귀 96개 PASS |
| 격리된 CI 동등 환경 (`.[curl,dev]`) | **469 passed, 2 deselected**, 종료 0 |
| `ruff check omk_crawl/ tests/` | 종료 0 |
| 변경 Python 경로 17개 `basedpyright --level error` | 타입 오류 0, 종료 0 |
| 변경 소스 10개 primary LSP 재검사 | clean 10, 오류 0 |
| `python3 -m build --wheel --outdir <임시 경로>` | 종료 0 |
| wheel 검사 | Python 모듈 55개가 현재 원본과 일치, `py.typed` 포함 |
| `python3 -I -S` wheel 실행 | 추가 패키지 없이 HAR 동기/비동기 API·CLI 성공, SDK 없는 웹 요청은 호출 0회의 `tool_missing` |
| 표준 `HTTPError` 정규화 | 네트워크 없이 429 및 `Retry-After` 헤더 보존 확인 |

이번 단위에서 테스트 70개를 추가했다. 전체 테스트 명령은
`/tmp/omk-engine-upgrade-inyf37oj/venv/bin/python -m pytest -q --tb=short`였다.
임시 venv에 프로젝트가 선언한 dev/curl extra를 설치했으며 공유 환경은 변경하지 않았다.

## 자체 리뷰

- 최대 호출 수에 딱 맞춰 성공한 응답을 실패로 덮어쓰지 않는다.
- 어댑터가 예외를 던지거나 오류 결과로 반환해도 대체 경로를 사용할 수 있다.
  실제 HTTP 오류는 별도로 보존해 인증·요청 제한을 다른 클라이언트로 우회하지 않는다.
- 입력 옵션 값과 SDK 예외 원문을 새 execution trace에 복사하지 않는다.
- 단순한 부분 문자열로 서비스 도메인을 판정하지 않는다.
- deadline은 협조적이다. 실행 중인 SDK 작업을 강제로 죽이거나 모든 하위 HTTP 요청을
  세는 샌드박스가 아니다. 상세 계약은 [engine.md](engine.md)에 있다.

## 한계와 검사 구분

- 실제 앱·기기 및 외부 사이트 차단 통과율은 측정하지 않았다. HTTP 검증은 소유한
  loopback 서버, 브라우저 분기는 SDK fixture로 수행했다.
- Python 3.10/3.11/3.13 CI 실행과 전체 코드 커버리지 비율은 이번 로컬 검사 범위 밖이다.
- 타입 검사는 오류 수준 통과이며 전체 엄격 경고까지 정리했다는 뜻이 아니다.
- 이전 소스의 LSP 캐시 알림 2건은 새 primary LSP·독립 검사에서 재현되지 않았다.
  현재 파일에 없는 절대 import를 가리키는 알림 1건은 세션 캐시에 계속 남아 오진으로
  표시했다. 소스 억제 주석이나 타입·린트 규칙 변경은 없다.
- core+dev 전체 검사에서 기존 일부 테스트의 `curl_cffi` 설치 전제를 확인했고,
  CI 설치 조건을 맞춘 뒤 위의 전체 통과 결과를 얻었다. 실패를 테스트 제외로 숨기지 않았다.
