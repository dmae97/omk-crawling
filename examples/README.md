# omk-crawling Examples

안전한 스크래핑 샌드박스([quotes.toscrape.com](https://quotes.toscrape.com)) 등을 대상으로
툴박스의 대표 도구를 실행 예제로 보여준다 (crawl4ai + markitdown + autoscraper + curl_cffi).

## 설치

```bash
pip install -U crawl4ai 'markitdown[all]' autoscraper curl_cffi
crawl4ai-setup       # Playwright Chromium (crawl4ai 예제용)
```

## 예제

| 파일 | 도구 | 보여주는 것 |
|------|------|-------------|
| `01_quickstart_markdown.py` | crawl4ai | 페이지 → 마크다운 + fit-markdown |
| `02_structured_css.py` | crawl4ai | `JsonCssExtractionStrategy` (LLM 없이) |
| `03_deep_crawl.py` | crawl4ai | `BFSDeepCrawlStrategy` + 페이지 상한 |
| `04_batch_arun_many.py` | crawl4ai | `arun_many` + `MemoryAdaptiveDispatcher` |
| `05_markitdown_convert.py` | markitdown | 파일(HTML/PDF/Office) → Markdown |
| `06_autoscraper_learn.py` | autoscraper | 예시로 규칙 학습 → 다른 페이지 재적용 |
| `07_curl_impersonate.py` | curl_cffi | 브라우저 TLS 핑거프린트로 fetch |

나머지 도구(scrapy·crawlee·browser-use·scrcpy)의 최소 예제는 `references/tools/<도구>.md` 참조.

## 실행

```bash
for f in examples/0*.py; do echo "== $f =="; python "$f"; done
```

## 에스컬레이션

전체 툴박스 라우팅은 [`../references/routing.md`](../references/routing.md). crawl4ai 내부 에스컬레이션:

```
arun(url) → result.markdown              # 기본
  └─ 잡음 많음 → PruningContentFilter → fit_markdown
       └─ 반복 구조 → JsonCssExtractionStrategy (무료) / autoscraper
            └─ 비정형/추론 → LLMExtractionStrategy (유료)
       └─ 여러 페이지 → deep_crawl_strategy=BFS/DFS/BestFirst (또는 scrapy/crawlee)
            └─ 수백+ URL → arun_many + MemoryAdaptiveDispatcher
  └─ TLS 핑거프린트 403 → curl_cffi(impersonate)
  └─ 봇 차단/Cloudflare → scrapling(StealthyFetcher)
  └─ 로그인·다단계 조작 → browser-use
  └─ 단일 차단 URL만 → insane-search
```
