# Agent Reach 읽기 어댑터

`agent_reach`는 별도 설치된 Agent Reach 실행 파일을 읽기 전용 하위 프로세스로 호출한다.
`crawl()`, `crawl_async()`, `SmartRouter`, CLI의 대상 분기를 통해 사용할 수 있다. 이 문서는
오프라인 가짜 실행 파일 테스트로 확인한 어댑터 계약만 설명한다. 실제 플랫폼 연결, 로그인
상태, 채널별 응답을 라이브 검증했다는 뜻은 아니다.

## 실행 파일과 사용 형식

기본 실행 파일명은 `agent-reach`다. 다른 실행 파일을 쓰려면 프로세스를 시작하기 전에
`OMK_AGENT_REACH_BIN`에 실행 파일명 또는 경로를 설정한다. Python에서
`AgentReachTool(executable=...)`로도 지정할 수 있다.

```bash
export OMK_AGENT_REACH_BIN=/path/to/agent-reach
omk-crawl 'reach://github/search?q=agent%20reach' --json
```

`reach://<channel>/<action>?q=<query>` 또는
`reach://<channel>/<action>?url=<url>`은 디코딩한 값 하나를 positional argv로 전달한다.
상위 action이 지원하는 경우 `&limit=5`를 `--limit 5`로 변환한다. 모든 과정은 셸을 거치지
않는다. 실제 channel/action 조합만 허용하므로 `reach://web/hot?q=x` 같은 가상의 조합은
프로세스 생성 전에 거부한다.

| channel | action과 실제 argv |
| --- | --- |
| `github` | `search <q> [--limit N]`, `read <url>` |
| `twitter` | `search <q> [--limit N]`, `profile <q>`, `tweets <q> [--limit N]`, `thread <url>` |
| `youtube` | `read <url>`, `search <q> [--limit N]`, `subtitle <url>`, `transcribe <url>` |
| `facebook` | `search <q> [--limit N]`, `profile <q>` |
| `instagram` | `search <q> [--limit N]`, `profile <q>`, `posts <q> [--limit N]` |
| `bilibili` | `search <q> [--limit N]`, `read <url>`, `hot [--limit N]`, `subtitle <url>` |
| `xiaohongshu` | `search <q> [--limit N]`, `read <url>`, `comments <url> [--limit N]` |
| `linkedin` | `jobs <q> [--limit N]`, `read <url>` |
| `boss` | `search <q> [--limit N]`, `read <url>` |
| `v2ex` | `hot [--limit N]`, `node <q> [--limit N]`, `read <url>`, `user <q>` |
| `xueqiu` | `quote <q>`, `search <q> [--limit N]`, `hot_posts [--limit N]`, `hot_stocks [--limit N]` |
| `xiaoyuzhou` | `transcribe <url>` |
| `web` | `search <q> [--limit N]`, `read <url>` |

hot 계열은 positional query가 없다. 따라서 다음은 각각 정확히 아래 argv가 된다.

```text
reach://bilibili/hot                 → agent-reach bilibili hot
reach://bilibili/hot?limit=3         → agent-reach bilibili hot --limit 3
reach://v2ex/hot                     → agent-reach v2ex hot
reach://xueqiu/hot_posts?limit=3     → agent-reach xueqiu hot_posts --limit 3
reach://xueqiu/hot_stocks            → agent-reach xueqiu hot_stocks
reach://xiaoyuzhou/transcribe?url=<episode-url>
                                      → agent-reach xiaoyuzhou transcribe <episode-url>
```

`reach://bilibili/hot?q=x`처럼 zero-positional action에 `q`/`url`을 붙이면 값을 버리거나
가상의 positional 인자를 만들지 않고 `invalid_target`으로 거부한다. `setup`, `add`,
`update`, `login` 같은 쓰기·관리 action도 거부하며, 어댑터가 설치나 로그인을 대신 실행하지
않는다. 알 수 없는 조합, 중복 매개변수, 잘못된 limit, NUL·제어 문자(원본 및 디코딩 argv),
잘못된 HTTPS port는 프로세스 생성 전에 거부한다.

## HTTPS 대상 매핑

자동 대상 분기는 다음 공식 기본 도메인과 그 하위 도메인의 **HTTPS** URL을 `agent_reach`로
선택한다. 경로나 쿼리에 도메인 문자열이 있는 것만으로 선택하지 않으며, 사용자 정보·접미사를
이용한 유사 도메인도 선택하지 않는다. 일반 HTTP URL은 기존 웹 경로에 남는다.

| 기본 도메인 | channel |
| --- | --- |
| `github.com` | `github` |
| `youtube.com`, `youtu.be` | `youtube` |
| `bilibili.com`, `b23.tv` | `bilibili` |
| `v2ex.com` | `v2ex` |
| `twitter.com`, `x.com` | `twitter` |
| `facebook.com` | `facebook` |
| `instagram.com` | `instagram` |
| `xiaohongshu.com`, `xhslink.com` | `xiaohongshu` |
| `linkedin.com` | `linkedin` |
| `zhipin.com` | `boss` |
| `xueqiu.com` | `xueqiu` |
| `xiaoyuzhoufm.com` | `xiaoyuzhou` |

Reddit은 기존 `reddit` 어댑터가 우선한다. HTTPS 호출의 action은 `read`이며 원래 URL을
그대로 인자로 전달한다.

```text
agent-reach read <original-https-url>
```

명시적으로 `agent_reach`를 선택해 다른 HTTPS URL을 전달하면 channel은 `web`이다.
`agent_reach`는 `TARGET_TOOLS`에는 등록되어 있지만 **`ESCALATION_CHAIN`에는 없다**.
일반 웹 실패 시 끼어드는 fallback이 아니라 대상 분기나 `--tool agent_reach` /
`SmartRouter(tools=["agent_reach"])`로 선택하는 어댑터다.

## 결과, raw opt-in, 오류 sanitize

성공 응답은 stdout의 JSON 객체에서 `ok`가 `true`이고 `items`가 객체들의 목록이어야 한다.
결과는 `CrawlStatus.OK`, `status_code=200`이다. `extracted`에는 공개 normalized 필드인
`title`, `url`, `author`, `published_at`, `text`만 보존한다. Markdown에도 같은 필드를
비활성 텍스트로 렌더링한다. 응답 본문과 item은 항상 신뢰할 수 없는 데이터이며
`metadata.untrusted_content`는 성공·오류 모두 `true`다.

**raw 보존은 명시적 opt-in뿐이다.** 기본값에서는 `metadata.raw_response`와 item의 `raw`
및 기타 비정규 필드를 포함하지 않는다. 상위 원본에 자격 증명이나 중첩 비밀이 있어도 기본
결과로 복사하지 않는다. 원본이 반드시 필요할 때만 프로세스 환경에
`OMK_AGENT_REACH_RAW=1`을 설정한다. 이 경우 parsed envelope 전체와 item의 `raw`를 보존하므로
민감정보가 그대로 포함될 수 있다.

실제 CLI와 마찬가지로 `ok:false` envelope가 stdout에 있고 종료 코드가 1이어도 generic
nonzero exit보다 envelope를 먼저 해석한다. `metadata.returncode`는 그대로 보존한다.
`error.code`를 종료 사유로 매핑하고, 오류 문구는 code와 `error.message`(없으면 `hint`)로
만든다. 메시지와 중첩 진단 값은 출력 전에 sanitize한다. Bearer 자격 증명,
`authorization`/`cookie`/`x-api-key`/`token`/`password`/`secret` 계열 필드, URL
userinfo와 query secret을 `[REDACTED]`로 바꾼다. stdout/stderr 원문 자체는 복사하지 않는다.

주요 `metadata` 필드는 다음과 같다.

| 필드 | 의미 |
| --- | --- |
| `channel`, `action` | 대상에서 해석한 실제 채널과 action |
| `untrusted_content` | 항상 `true` |
| `returncode` | 종료·정리 후 관측한 하위 프로세스 반환 코드 |
| `raw_response` | `OMK_AGENT_REACH_RAW=1`일 때만 parsed JSON 원본 |
| `stop_reason` | 어댑터가 분류한 종료 사유 |
| `retry_after_seconds` | 상위가 제공한 대기 값이 있을 때만 보존 |

`stop_reason`은 성공 시 `success`다. 입력·실행 오류에는 `invalid_target`, `tool_missing`,
`output_too_large`, `deadline_exceeded`, `hard_error`를 사용한다. `error.code` 대응은 다음과
같다.

| `error.code` | `stop_reason` |
| --- | --- |
| `NEED_LOGIN` | `auth_required` |
| `RATE_LIMITED` | `rate_limited` |
| `PLATFORM_BLOCKED` | `platform_blocked` |
| `NEED_SETUP` | `needs_setup` |
| `NOT_FOUND` | `not_found` |
| `BAD_INPUT` | `invalid_target` |
| `UPSTREAM_BROKEN` | `upstream_broken` |
| `TIMEOUT` | `deadline_exceeded` |
| `BUSY` | `busy` |

공통 `fetch_sync`/`fetch_async` 실행기는 `agent_reach`를 재시도하지 않는다.
`_stop_reason`과 exhaustion 경로도 위 어댑터 사유를 `hard_error`/`tools_exhausted`로 덮어쓰지
않는다. 따라서 공개 sync/async router 모두 한 번 호출 후 같은 사유를 반환한다.
`retry_after_seconds`는 `error` 객체를 우선하며 자동 대기하거나 다른 백엔드로 바꾸지 않는다.

stdout과 stderr를 각각 최대 1MiB로 제한하고 초과 시 `output_too_large`로 실패한다. POSIX에서는
독립 process group을 소유하고 `REACH_API_SUPERVISED=1`을 자식 환경에 설정해 상위 Agent Reach의
후속 자식도 같은 그룹에 남게 한다. 정상 종료·시간 초과·출력 초과 후에도 그룹을 종료하고
직접 자식을 회수한다. 비POSIX 플랫폼은 이 정리 보장을 제공할 수 없으므로 프로세스를 만들기
전에 `TOOL_MISSING` / `tool_missing`으로 fail closed한다. 실행 파일 생성 시 `ValueError`도
실제 spawn 없이 `invalid_target`으로 변환한다.

## 라이브 확인은 별도 작업

실제 설치·채널 상태는 Agent Reach의 진단 명령으로 별도 확인해야 한다.

```bash
agent-reach doctor --text
agent-reach check <channel> --text
```

이번 어댑터 변경과 비라이브 테스트는 위 명령을 **실행하지 않았다**. 따라서 실제 채널의
인증, 설정, 네트워크, 상위 API 동작은 검증되지 않았으며, `available()`가 참이라는 것도
실행 파일을 찾을 수 있다는 뜻일 뿐 라이브 성공을 뜻하지 않는다.
