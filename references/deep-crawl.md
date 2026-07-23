# 딥크롤 · adaptive · 대규모 병렬

## 딥크롤 전략

`CrawlerRunConfig(deep_crawl_strategy=...)`에 붙인다. 결과는 페이지들의 리스트(또는 `stream=True`면 async iterator).

```python
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.deep_crawling import (
    BFSDeepCrawlStrategy,       # 너비 우선: 얕고 넓게 (문서 사이트 전체)
    DFSDeepCrawlStrategy,       # 깊이 우선: 특정 갈래 깊게
    BestFirstCrawlingStrategy,  # 점수 높은 링크 우선 (주제 집중)
)

cfg = CrawlerRunConfig(
    deep_crawl_strategy=BFSDeepCrawlStrategy(
        max_depth=2,             # 시작 URL 기준 깊이
        include_external=False,  # 같은 도메인만
        max_pages=50,            # 안전 상한
    ),
    stream=False,
)

async with AsyncWebCrawler() as crawler:
    results = await crawler.arun("https://docs.example.com", config=cfg)
    for r in results:
        depth = r.metadata.get("depth")
        print(depth, r.url, len(r.markdown.raw_markdown))
```

스트리밍(메모리 절약, 도착 즉시 처리):

```python
cfg = CrawlerRunConfig(deep_crawl_strategy=BFSDeepCrawlStrategy(max_depth=2), stream=True)
async for r in await crawler.arun("https://docs.example.com", config=cfg):
    process(r)
```

## 필터 체인 (어떤 링크를 따라갈지)

```python
from crawl4ai.deep_crawling.filters import (
    FilterChain, DomainFilter, URLPatternFilter, ContentTypeFilter,
)

filters = FilterChain([
    DomainFilter(allowed_domains=["docs.example.com"]),
    URLPatternFilter(patterns=["*/guide/*", "*/api/*"]),
    ContentTypeFilter(allowed_types=["text/html"]),
])
strategy = BFSDeepCrawlStrategy(max_depth=3, filter_chain=filters, max_pages=100)
```

## 스코어러 (BestFirst — 주제 집중)

```python
from crawl4ai.deep_crawling.scorers import KeywordRelevanceScorer

scorer = KeywordRelevanceScorer(keywords=["pricing", "limits", "quota"], weight=0.8)
strategy = BestFirstCrawlingStrategy(max_depth=3, max_pages=30, url_scorer=scorer)
# 점수 높은 링크부터 큐잉 → 관련 페이지를 먼저·적게 방문
```

## Crash recovery (긴 크롤 재개)

```python
strategy = BFSDeepCrawlStrategy(
    max_depth=3,
    resume_state=saved_state,        # 체크포인트에서 재개 (JSON 직렬화 가능)
    on_state_change=save_to_redis,   # 각 URL 처리 후 호출 → 외부 저장
)
```

## Prefetch (빠른 URL 발견)

```python
cfg = CrawlerRunConfig(prefetch=True)   # 마크다운/추출/미디어 스킵 → 5~10배 빠름
# 1단계: prefetch 딥크롤로 URL·링크만 수집
# 2단계: 선별한 URL만 풀 처리(또는 scrapling 스텔스)
```

## Adaptive 크롤 (스스로 충분함을 판단)

```python
from crawl4ai import AsyncWebCrawler, AdaptiveCrawler, AdaptiveConfig

config = AdaptiveConfig(
    confidence_threshold=0.7,  # 이 정도 확신이면 중단
    max_depth=5, max_pages=20,
    strategy="statistical",    # LLM 없이 통계 기반 (또는 "embedding")
)
async with AsyncWebCrawler() as crawler:
    ac = AdaptiveCrawler(crawler, config)
    state = await ac.digest(start_url="https://news.example.com", query="latest release notes")
    # 쿼리에 충분한 정보가 모이면 조기 종료 → 낭비 없는 크롤
```

## 대규모 병렬 — `arun_many` + 디스패처

```python
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, MemoryAdaptiveDispatcher, RateLimiter

dispatcher = MemoryAdaptiveDispatcher(
    memory_threshold_percent=80.0,        # 메모리 압박 시 자동 스로틀
    max_session_permit=10,                # 동시성 상한
    rate_limiter=RateLimiter(base_delay=(1.0, 3.0)),
)
async with AsyncWebCrawler() as crawler:
    results = await crawler.arun_many(urls, config=CrawlerRunConfig(), dispatcher=dispatcher)
```

### URL 패턴별 다른 설정 (한 배치, 여러 config)

```python
from crawl4ai import CrawlerRunConfig, CacheMode

configs = [
    CrawlerRunConfig(url_matcher=["*docs*", "*documentation*"], cache_mode=CacheMode.WRITE_ONLY),
    CrawlerRunConfig(url_matcher=lambda u: "blog" in u or "news" in u, cache_mode=CacheMode.BYPASS),
    CrawlerRunConfig(),  # 폴백
]
results = await crawler.arun_many(urls, config=configs)  # URL마다 맞는 설정 자동 선택
```

## 가드레일

- `max_pages`/`max_depth`/도메인 필터를 **항상** 둔다. 외부 도메인 확산(`include_external=True`)은 신중히.
- 대규모엔 `RateLimiter`와 동시성 상한으로 서버 부담·차단을 줄인다.
- robots.txt·ToS 준수. 권한 없는 페이월/인증 우회 금지.
