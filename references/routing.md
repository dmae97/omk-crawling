# 크로스-툴 결정 트리 (무엇을 언제)

목표를 위에서부터 내려가며 **처음 맞는 줄**의 도구를 쓴다. 확신 없으면 가장 싸고 가벼운 것부터.

```
0. 데이터가 웹/HTTP에 없다 → Android 앱에만 있다
      └─ scrcpy (화면 미러·제어, 필요하면 --record 로 근거 남김)

1. 대상이 "파일"이다 (PDF·docx·pptx·xlsx·이미지·오디오·이미 받은 HTML)
      └─ markitdown  (→ Markdown, LLM 입력용)

2. 대상이 "웹페이지"다
   2a. 딱 한 URL만 열어 본문 확인 → insane-search (형제 스킬)
   2b. 접근 자격은 있는데 403/차단
         ├─ TLS/JA3·HTTP2 핑거프린트 차단 (브라우저 없이도 될 것 같다)
         │     └─ curl-impersonate / curl_cffi   (가장 가벼움)
         └─ JS 렌더·Cloudflare Turnstile·강한 봇탐지
               └─ scrapling (스텔스 브라우저 + 자기복구 파싱) → [tools/scrapling.md](tools/scrapling.md)
   2c. 로그인·다단계 클릭·폼 등 "사람처럼 조작"이 필요
         └─ browser-use (LLM 에이전트가 브라우저 운전)
   2d. 여러 페이지를 체계적으로 순회(크롤)
         ├─ 웹 → LLM용 Markdown / 사이트·문서 전체 딥크롤 / MCP 서버
         │     └─ crawl4ai
         ├─ 큐·오토스케일·프록시 로테이션·재시도 등 프로덕션 인프라 (JS/TS 또는 Python)
         │     └─ crawlee
         └─ 성숙한 파이프라인·미들웨어·확장 생태계, 대규모 클래식 크롤
               └─ scrapy
   2e. 위에서 HTML을 얻었고 "구조화"만 남았다
         ├─ 예시 몇 개로 규칙 학습(브라우저 X, 초경량) → autoscraper
         ├─ 반복 패턴 CSS/XPath 스키마(무료·결정적)     → crawl4ai / scrapling
         └─ 비정형·추론 필요                             → crawl4ai LLM 추출 (유료)
```

## 비교 요약

| 도구 | 층 | 브라우저 | 강점 | 약점/비용 |
|------|----|----------|------|-----------|
| curl-impersonate / curl_cffi | Fetch | ✗ | TLS/JA3 위장, 초경량·초고속 | JS 실행 불가 |
| scrapling | Fetch/추출 | ✓(스텔스) | Cloudflare 자동해결, 자기복구 셀렉터 | 무거움 → [tools/scrapling.md](tools/scrapling.md) |
| insane-search | Fetch | ✓ | 단일 하드블록 돌파 | 대량 X |
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
