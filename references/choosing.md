# crawl4ai 내부 모드 고르기

> 8개 도구 중 **무엇을** 쓸지(크로스-툴)는 [`routing.md`](routing.md)가 상위. 이 문서는
> **crawl4ai를 쓰기로 정한 뒤** crawl4ai 내부에서 어느 모드를 쓸지를 다룬다.
>
> 웹 접근 패밀리 한 줄 요약: 단일 차단 URL → `insane-search` · 스텔스/정밀 반복 → `scrapling` ·
> LLM Markdown·딥크롤·MCP → `crawl4ai`(omk-crawling 기본 엔진).

셋은 배타적이지 않다. 흔한 결합:
- `insane-search`나 `scrapling.StealthyFetcher`로 **뚫은 HTML** → crawl4ai `arun("raw:<html>")`로 마크다운화/구조화.
- crawl4ai로 **URL을 대량 발견**(prefetch) → 민감/까다로운 페이지만 `scrapling` 스텔스로 재요청.

## 2단계 — crawl4ai 내부 모드

| 원하는 것 | 쓸 것 |
|-----------|-------|
| 페이지 → 읽기 좋은 마크다운 | `AsyncWebCrawler.arun(url)` → `result.markdown` |
| 페이지 → LLM용 노이즈 제거 본문 | `PruningContentFilter` → `result.markdown.fit_markdown` |
| 쿼리 연관 본문만 | `BM25ContentFilter(user_query=...)` |
| 반복 패턴(카드/표/목록) 구조화 | `JsonCssExtractionStrategy` (무료·결정적) |
| 비정형·추론 필요한 추출 | `LLMExtractionStrategy` (API 비용 발생) |
| 사이트/문서 전체 순회 | `deep_crawl_strategy=BFS/DFS/BestFirst` |
| "이 주제에 충분한가?"를 스스로 판단하며 크롤 | `AdaptiveCrawler.digest(start_url, query=...)` |
| 수백~수천 URL 병렬 | `arun_many(urls, dispatcher=MemoryAdaptiveDispatcher())` |
| JS 실행/무한스크롤/로그인 상태 재사용 | `CrawlerRunConfig(js_code=..., scan_full_page=True)`, `BrowserConfig(user_data_dir=...)` |
| 다른 도구(Claude Code 등)에서 호출 | Docker 서버의 **MCP 엔드포인트** |

## 3단계 — 비용·속도 원칙

1. **CSS/XPath 먼저.** 반복 구조는 스키마 추출이 무료·즉시·결정적. LLM은 최후.
2. **prefetch로 발견, 선별 처리.** `CrawlerRunConfig(prefetch=True)`는 마크다운/추출/미디어를 건너뛰어 URL·링크만 5~10배 빠르게 모은다. 2단계 크롤(발견 → 선택 처리)에 최적.
3. **fit-markdown으로 토큰 절약.** LLM에 넘기기 전 보일러플레이트 제거.
4. **캐시.** 반복 실행/개발 중엔 `CacheMode.ENABLED`, 신선도가 중요하면 `BYPASS`.
