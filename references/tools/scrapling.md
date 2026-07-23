# scrapling — 스텔스 스크래핑 프레임워크

- Repo: https://github.com/d4vinci/Scrapling · Docs: https://scrapling.readthedocs.io
- PyPI `scrapling` v0.4.11 · Python ≥3.9 · **BSD-3-Clause**

## 언제

**안티봇 보호**(Cloudflare Turnstile, DataDome, PerimeterX 등)가 있는 사이트에서
JS 렌더 + 스텔스 브라우저로 데이터를 추출할 때. `curl_cffi`로 안 되는(브라우저 상호작용
필요) + `browser-use`보다 결정적이고 저렴한 경로를 원할 때. **적응형 셀렉터**로 페이지
구조가 바뀌어도 추출이 살아남는다.

단일 차단 URL만 열면 되는 경우는 `insane-search`(형제 스킬)가 더 가볍다.
대량 정형 수집은 `crawl4ai`/`scrapy`/`crawlee`가 더 싸고 빠르다.

## 설치

```bash
pip install scrapling
scrapling install            # 스텔스 브라우저(Camoufox) 다운로드
```

## 최소 예제 — 스텔스 fetch

```python
from scrapling import StealthyFetcher

fetcher = StealthyFetcher()
page = fetcher.fetch("https://example.com", headless=True)
print(page.status)
print(page.css("h1::text"))           # CSS 셀렉터
print(page.xpath("//title/text()"))   # XPath
```

## 적응형 셀렉터 (구조 변경에 강한 추출)

```python
# 1. "학습" 단계: 요소에 이름표
element = page.css_first("div.product-card")
element.set_marker("product_card")

# 2. 페이지 구조가 바뀐 뒤에도 마커로 재추적
relocated = page.find_marker("product_card")   # DOM 변화에도 위치 복원
```

## 스파이더 (여러 페이지 순회)

```python
from scrapling.spiders import Spider

class MySpider(Spider):
    name = "products"
    start_urls = ["https://example.com/products"]

    def parse(self, page):
        for card in page.css("div.product"):
            yield {
                "name": card.css("h2::text"),
                "price": card.css(".price::text"),
            }
        next_link = page.css_first("a.next::attr(href)")
        if next_link:
            yield page.follow(next_link, self.parse)
```

## MCP 서버

```bash
uvx scrapling mcp            # MCP 서버 시작 → 다른 에이전트에서 도구로 호출
```

## 패턴 / 함정

- **스텔스 vs 일반**: `StealthyFetcher`(스텔스 브라우저) vs `Fetcher`(일반 HTTP). 안티봇 없으면 `Fetcher`가 빠름.
- **Cloudflare Turnstile**: `StealthyFetcher`가 자동 해결. 실패 시 `headless=False`로 시도.
- **프록시**: `fetcher.fetch(url, proxy="http://user:pass@host:port")`.
- **적응형 셀렉터**는 DOM 구조 변경에 강하지만, 콘텐츠 자체가 바뀌면 재학습 필요.
- `browser-use`와 달리 **결정적** — 같은 페이지면 같은 결과. LLM 비용 없음.
- 권한 없는 페이월/인증 우회 금지. 안티봇 우회는 **접근 자격이 있을 때**만.
