# Decodo egress 프록시 배선

OMK 수집·진단 레인의 아웃바운드 IP를 Decodo 로테이션 프록시로 라우팅하는 표준 경로다.
자격증명은 코드·문서·로그에 절대 박지 않는다 — env 파일 하나에만 둔다.

## 자격증명 배치 (fail-closed)

리포 루트 `.env.local` (`.env.*`는 `.gitignore` 처리됨, mode 600):

```bash
DECODO_ENDPOINT=gate.decodo.com:7000   # 대시보드의 엔드포인트
DECODO_USER=<계정 사용자명>            # 세션/로케일 파라미터 인코딩 가능 (예: user-sess-abc-session_duration-10)
DECODO_PASSWORD=<프록시 비밀번호/토큰>
```

- `DECODO_USER`가 비어 있으면 `v9/netproxy.mjs`의 `resolveProxyUrl`이 TypeError를 던진다 —
  부분 설정에서 조용히 직출 IP로 빠지는 일은 없다 (egress가 실제로 쓰일 때만 차단).
- `OMK_HTTP_PROXY=<완성 URL>` 또는 `DECODO_PROXY=<완성 URL>`를 지정하면 조립 없이 그 URL이 우선한다.
- 허용 스킴: `http`, `https`, `socks5`, `socks5h`. 그 외는 TypeError.
- `NO_PROXY`는 항상 `localhost,127.0.0.1,::1,169.254.169.254,metadata.google.internal`을 포함한다 —
  프록시 경유라도 내부 주소를 향한 요청은 만들지 않는다.

## 소비자

| 레인 | 경로 |
| --- | --- |
| serverhack 표면 평가 | `v9/serverhack/run.mjs` — 기본 **직결**, opt-in `options.proxy:true` 또는 `OMK_PROXY_OFFENSIVE=1`일 때만 프록시 |
| redteam 실측 러너 | `v9/redteam/session-runner.mjs`·`system-wrap.mjs`·`benchmark.mjs` — `proxyEnv()`가 자식 `omk`에 `HTTP(S)_PROXY`/`ALL_PROXY`/`NO_PROXY` 전달 |
| crawling 도구 | crawl4ai/crawlee/scrapling/curl_cffi 등은 `proxies=`/`--proxy` 인자로 같은 env를 그대로 소비 |

## 검증

```bash
# egress IP 확인 (자격증명 완성 후)
loadProxyEnvFile로 .env.local 로드 → makeFetch()("https://ip.decodo.com/json")
```

## 주의

- curl 경유 fetch는 `%{header_json}` + `-o <tmpfile>`로 헤더/바디를 분리한다 —
  프록시의 CONNECT 프렐루드가 바디에 섞이지 않는다.
- `redirect: manual` + 홉마다 `assertPublicHttpTarget` 재검증은 프록시 경유와
  독립적으로 적용된다 (Location이 내부를 가리키면 프록시 여부와 무관하게 차단).
