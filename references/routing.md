# 크로스-툴 결정 트리 (무엇을 언제)

목표를 위에서부터 내려가며 **처음 맞는 줄**의 도구를 쓴다. 확신 없으면 가장 싸고 가벼운 것부터.

CLI·`crawl()`·`crawl_async()`의 실제 자동 분기와 예산은 [엔진 계약](engine.md)을 따른다.
HAR/APK/IPA·기기 URI는 웹 체인에 보내지 않으며, LLM 어댑터는 `allow_llm=True` 또는
`--allow-llm`을 명시해야 한다. 아래 표는 자동 체인 외의 수동 도구 조합도 포함한다.

```text
0. 데이터가 웹/HTTP에 없다 → Android 앱에만 있다
      └─ scrcpy (화면 미러·제어, 필요하면 --record 로 근거 남김)

1. 대상이 "파일"이다
      ├─ HAR → har (오프라인 엔드포인트·선택적 JSON 본문)
      ├─ APK/XAPK/APKS·IPA → apk / ipa (정적 분석)
      └─ PDF·docx·pptx·xlsx·이미지·오디오·이미 받은 HTML → markitdown

2. 대상이 "플랫폼/웹페이지"다
   2a. 플랫폼 특화 읽기·검색·댓글·자막
         ├─ GitHub·YouTube·Bilibili·V2EX → agent-reach (기본 채널)
         ├─ X·Reddit·小红书·LinkedIn 등 → agent-reach channels로 지원 동작 확인
         └─ 자동 설치·로그인은 하지 않으며, 부족한 부분만 [agent-reach.md](agent-reach.md)
   2b. 일반 단일 URL 본문 확인 → 네이티브 fetch / crawl4ai
         └─ Agent Reach web은 부족할 때만 사용
   2c. 접근 자격은 있는데 403/차단
         ├─ 단일 하드블록 URL → insane-search (형제 스킬)
         ├─ TLS/JA3·HTTP2 핑거프린트 차단 (브라우저 없이도 될 것 같다)
         │     └─ curl-impersonate / curl_cffi   (가장 가벼움)
         ├─ JS 렌더·Cloudflare Turnstile·강한 봇탐지
         │     └─ scrapling (스텔스 브라우저 + 자기복구 파싱) → [tools/scrapling.md](tools/scrapling.md)
         ├─ DataDome·Kasada·PerimeterX (행동 기반 벤더)
         │     └─ nodriver (CDP 네이티브, 아티팩트無) → camoufox (엔진 레벨 위장)
         ├─ AWS WAF 토큰 챌린지
         │     └─ camoufox
         └─ 한 번 통과한 세션을 싸게 재사용 (cf_clearance 등)
               └─ warm_crawl (웜업→리플레이→폴��) → [breakthrough.md](breakthrough.md)
   2d. 로그인·다단계 클릭·폼 등 "사람처럼 조작"이 필요
         └─ browser-use (LLM 에이전트가 브라우저 운전)
   2e. 여러 페이지를 체계적으로 순회(크롤)
         ├─ 웹 → LLM용 Markdown / 사이트·문서 전체 딥크롤 / MCP 서버
         │     └─ crawl4ai
         ├─ 큐·오토스케일·프록시 로테이션·재시도 등 프로덕션 인프라 (JS/TS 또는 Python)
         │     └─ crawlee
         └─ 성숙한 파이프라인·미들웨어·확장 생태계, 대규모 클래식 크롤
               └─ scrapy
   2f. 위에서 HTML을 얻었고 "구조화"만 남았다
         ├─ 예시 몇 개로 규칙 학습(브라우저 X, 초경량) → autoscraper
         ├─ 반복 패턴 CSS/XPath 스키마(무료·결정적)     → crawl4ai / scrapling
         └─ 비정형·추론 필요                             → crawl4ai LLM 추출 (유료)
   2g. 대상 호스트가 .onion 이다
         └─ tor (curl_cffi → Tor SOCKS5h 브릿지, 자동 라우팅·escalation 없음)
               ├─ 기본 브릿지 socks5h://127.0.0.1:9050 (Tor Browser는 9150)
               └─ --tor-proxy / OMK_TOR_PROXY 로 교체
   2h. 요청마다 무료 프록시 로테이션
         └─ --proxy-pool (RotationCrawler + FreshnessEngine)
               ├─ https 대상 → 검증된 풀 전체 (TLS가 MITM을 차단)
               ├─ http 대상 → integrity 카나리 통과 + 측정 anonymity≥anonymous 노드만
               │     카나리는 CANARY_TARGETS(icanhazip/ifconfig/checkip/ident/httpbin) 중 랜덤
               │     anonymity는 httpbin.org/headers 반사 헤더 vs 직접 측정한 내 IP로 판정
               └─ .onion 대상 → 풀 우회, TorTool 직행 (HTTP 프록시는 onion 도달 불가)
```

## 비교 요약

| 도구 | 층 | 브라우저 | 강점 | 약점/비용 |
| ------ | ---- | ---------- | ------ | ----------- |
| agent-reach | 플랫폼 읽기 | 채널별 | GitHub·영상·소셜·직업·웹을 한 명령으로 검색/읽기 | 채널별 인증·콘텐츠 범위 제한 → [agent-reach.md](agent-reach.md) |
| curl-impersonate / curl_cffi | Fetch | ✗ | TLS/JA3 위장, 초경량·초고속 | JS 실행 불가 |
| scrapling | Fetch/추출 | ✓(스텔스) | Cloudflare 자동해결, 자기복구 셀렉터 | 무거움 → [tools/scrapling.md](tools/scrapling.md) |
| insane-search | Fetch | ✓ | 단일 하드블록 돌파 | 대량 X |
| camoufox | 브라우저 | ✓(안티디텍트) | C++ 레벨 지문 주입, geoip | 무거움, 바이너리 별도 → [tools/camoufox.md](tools/camoufox.md) |
| nodriver | 브라우저 | ✓(CDP) | DataDome/Kasada/PerimeterX 최강 | async 전용, AGPL → [tools/nodriver.md](tools/nodriver.md) |
| patchright | 브라우저 | ✓(패치PW) | Playwright 드롭인 + 탐지 패치 | 바이너리 별도 → [tools/patchright.md](tools/patchright.md) |
| warm_crawl | 세션 | ✓→✗ | 웜업 1회 → 가벼운 리플레이 N회 | 브라우저 1회 필요 → [breakthrough.md](breakthrough.md) |
| scrapy | Crawl | ✗(기본) | 생태계·파이프라인·성숙 | JS는 플러그인 필요 |
| crawlee | Crawl | 선택 | 큐·오토스케일·프록시·통합 스토리지 | 러닝커브 |
| crawl4ai | Crawl/변환/추출 | ✓ | LLM Markdown·딥크롤·MCP | 브라우저 자원 |
| browser-use | 브라우저 | ✓ | 에이전트가 다단계 조작 | LLM 비용·비결정적 |
| autoscraper | 추출 | ✗ | 예시→규칙 학습, 초경량 | 동적/복잡 페이지 약함 |
| markitdown | 변환 | ✗ | 파일→Markdown 광범위 | 웹 크롤 아님 |
| scrcpy | 모바일 | — | Android 미러·제어·녹화 | 웹 크롤 아님, 기기 필요 |

## 비용·속도 원칙 (전 도구 공통)

1. **가장 가벼운 층부터.** 파일이면 markitdown, 핑거프린트면 curl_cffi, 그 다음 HTTP 크롤러, 마지막에 브라우저/LLM.
2. **브라우저는 필요할 때만.** JS 렌더·상호작용이 정말 필요할 때만 browser-use/Playwright 계열.
3. **LLM은 최후.** 반복 구조는 autoscraper/CSS 스키마로 비용 0. LLM 추출·browser-use는 정말 비정형일 때만.
4. **상한을 건다.** 딥크롤/스파이더엔 max_depth·max_pages·도메인 필터·rate limit·동시성 상한을 항상.
5. **.onion은 tor 전용 전송.** 일반 체인으로 도달 불가하므로 escalation은 생략하고 브릿지 한 번만 시도한다. robots.txt도 urllib가 아니라 TorTool로 가져온다(`--deep` + `.onion` 조합에서 동작).
6. **무료 프록시는 MITM을 가정.** 평문 http 응답은 `integrity` 카나리 통과 노드만 통과시키고, 200+빗나간 본문 = 즉시 burn. 카나리는 `CANARY_TARGETS`(icanhazip·ifconfig.me·checkip.amazonaws·ident.me·httpbin/ip)에서 매번 랜덤 선택 — 단일 도메인만 통과시키는 선택적 MITM을 막는다. 남은 한계: 전 엔드포인트에 위조 응답하는 MITM은 못 잡으니 http 민감정보는 https만.
7. **익명성은 측정, 신고 아님.** 카나리 통과 ≠ 익명 — 실측에서 "clean" 샘플 2개 모두 httpbin `origin`에 실제 IP를 그대로 흘렸다. 매 canary 통과 후 `http://httpbin.org/headers`를 노드 경유로 가져와 반사된 포워딩 헤더(X-Forwarded-For·Forwarded·X-Real-IP·Via·…)와 직접 측정한 내 IP를 비교한다: `transparent`(IP 누출) / `anonymous`(프록시 사실만 노출) / `elite`(포워딩 헤더 無) / `unknown`(미측정 — 모든 anonymity 게이트 탈락). `RotationCrawler` 기본값 `min_anonymity="anonymous"`로 transparent·unknown을 제외하고, `OMK_PROXY_MIN_ANONYMITY=elite`는 더 조이고 `off`는 과거 허용 동작으로 되돌린다. 내 IP를 측정 못 하면 헤더 있는 노드는 `elite`가 아니라 `anonymous`로 떨어진다(fail-closed).
