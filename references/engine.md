# 대상 인식 수집 엔진

`crawl()`, `crawl_async()`, `SmartRouter`, CLI가 같은 대상 판별·실행 계획을 쓴다.
이 문서는 해당 진입점의 계약이다. `AdaptiveFetcher`, `AsyncBatchFetcher`, 직접 호출한
개별 도구는 각각의 실행 규칙을 유지한다. 검사 결과는
[엔진 통합 검증](verification-engine.md)에 기록했다.

## 사용

```bash
omk-crawl https://example.com --json --total-timeout 30 --max-fetches 4
omk-crawl https://example.com --no-browser --diagnose
omk-crawl capture.har --json
omk-crawl app.apk --json
omk-crawl 'android://SERIAL/packages' --json
```

```python
from omk_crawl import SmartRouter, crawl

router = SmartRouter(total_timeout=30, max_fetches=4, allow_browser=False)
result = router.crawl("https://example.com", timeout=10)
print(result.status, result.metadata["stop_reason"])
print(result.metadata["execution"]["trace"])

capture = crawl("capture.har")  # 웹 수집기를 거치지 않음
```

실제 앱·기기·세션은 소유자 또는 접근 권한자의 허가가 필요하다. APK/IPA 경로는
정적 분석이며 앱 실행, 인증 해제, TLS 검증 무력화 기능이 아니다.

## 대상과 선택 조건

| 입력 | 경로 |
| --- | --- |
| 일반 HTTP(S) URL | 설치되어 있고 요청 조건을 지원하는 웹 어댑터 |
| `reddit://`, 실제 `reddit.com` 및 하위 도메인 | Reddit 어댑터 |
| `appstore://`, `ios://` | App Store 메타데이터 |
| `baemin://` | 기존 Baemin 클라이언트 |
| `android://`, `adb://`, `device://` | adb/scrcpy 어댑터 |
| `.har` | 네트워크 재전송 없는 HAR 분석 |
| `.apk`, `.apks`, `.xapk`, `.ipa` | 패키지 정적 분석 |
| 문서 확장자 또는 기존 로컬 파일 | markitdown |

`--tool` / `SmartRouter(tools=[...])`는 자동 도구 선택을 덮어쓴다. 명시적으로 고른
HTTP 어댑터는 서비스 도메인에서도 robots 검사를 유지한다. `reddit.com`이라는
문자열이 경로나 쿼리에 들어 있다는 이유로 Reddit 어댑터를 선택하지 않는다.

전달한 `headers`, `cookies`, `proxy`, `session`, `timeout`을 지원하지 않는 어댑터는
호출 전에 제외한다. `execution.skipped`에는 도구명·제외 사유·미지원 항목명만 남기며
옵션 값은 기록하지 않는다. `diagnose(url, **kwargs)`에서도 같은 계획을 확인할 수 있다.

## 예산

기본값은 어댑터 최대 8개, 어댑터별 재시도 1회, 공유 deadline 120초다.
`max_fetches`를 생략하면 `max_attempts × (max_retries + 1)`이며 기본 16회다.

- `max_fetches`는 재시도를 포함한 **최상위 어댑터 호출 수**다. 브라우저 하위 리소스나
  개별 어댑터의 모든 HTTP 요청을 세는 값이 아니다.
- `total_timeout`은 대기·재시도·전환 사이에 공유하는 **협조적 deadline**이다. 남은 시간을
  지원 어댑터의 timeout에 전달하고, 기한 뒤 도착한 결과는 성공으로 처리하지 않는다.
  timeout을 무시하는 SDK나 실행 중인 동기 작업을 강제로 종료하는 장치는 아니다.
- `total_timeout=None`은 Python API에서 공유 deadline을 끈다.
- `--no-browser` / `allow_browser=False`는 브라우저 어댑터와 내부 선택적 브라우저 전환을
  끈다. 로컬 파일 분석에는 네트워크용 요청 간 지연을 적용하지 않는다.
- LLM 어댑터는 기본 제외다. `--allow-llm` / `allow_llm=True`로 명시해야 하며
  설정된 공급자의 비용이 발생할 수 있다.

## 실패와 실행 기록

로그인 필요, 서버의 요청 제한·`Retry-After`, 실패한 unsafe method는 다른 클라이언트로
감춰서 재시도하지 않는다. 어댑터가 비HTTP 실행 오류를 반환하거나 예외로 던져도
남은 허용 경로를 시도한다. HTTP 예외는 상태 코드·헤더를 보존해 같은 종료 정책을 적용한다.
쿠키·프록시 비밀번호가 들어갈 수 있는 예외 원문을 새 실행 trace에 복사하지 않는다.
기존 `CrawlResult.error`의 상세 설명은 별도이므로 외부 공유 전에 검토해야 한다.

`metadata.execution`은 요청마다 독립적인 `trace`, `skipped`, `fetches`, `max_fetches`,
`elapsed_ms`, `total_timeout`, `target_tool`, `timeout_enforcement`를 제공한다.
기존 `router.history`는 계속 누적되지만 `metadata.attempts`는 현재 요청의 도구 수다.

주요 종료 사유: `success`, `auth_required`, `rate_limited`, `retry_after_limit`,
`retry_exhausted`, `unsafe_method`, `robots_denied`, `deadline_exceeded`, `fetch_limit`,
`no_eligible_tools`, `tool_missing`, `invalid_target`, `invalid_options`, `hard_error`,
`max_attempts`, `tools_exhausted`.

## 첫 어댑터 내부 동작

`insane_search`는 기존 HTTP 프로필을 정해진 순서로 시도하며, 기본 18초를 프로필마다
갱신하지 않고 내부 작업 전체에 공유한다. HTTP 상태·헤더를 보존하고 인증·서버 대기
지시는 즉시 반환한다. 브라우저 전환은 실제 navigation 응답 상태를 사용하고, 예외가
발생해도 `finally`에서 브라우저 닫기를 호출한다. 남은 시간이 0인 상태로 브라우저를
시작해 timeout이 비활성화되는 경로도 차단한다.

SDK 미설치는 처리된 결과 상태로 남긴다. 사용자 지정 헤더는 내부
브라우저에 무작정 복사하지 않는다. HTTP로 처리할 수 없다면 해당 요청 조건을 지원하는
별도 렌더러에 맡긴다. 내부 이력은 `profile_attempts`, 최상위 호출 이력은
`execution.trace`에서 구분해 본다.

어떤 사이트·앱도 모두 통과한다는 보장은 없다. 실제 대상별 접근 권한, 기기 상태,
서버 동작과 설치된 SDK를 별도로 검증해야 한다.
