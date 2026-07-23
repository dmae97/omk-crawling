# crawlee — 큐·오토스케일·프록시 크롤 인프라

- Repo: https://github.com/apify/crawlee (JS/TS) · https://github.com/apify/crawlee-python (Python)
- npm `crawlee` v3.17.0 · PyPI `crawlee` v1.8.3 (Python ≥3.10) · **Apache-2.0**
- Docs: https://crawlee.dev · https://crawlee.dev/python

## 언제

프로덕션급 크롤 인프라가 필요할 때: 자동 **요청 큐**·중복제거, **오토스케일**(동시성 자동조절),
**프록시 로테이션**, 세션 풀, 통합 **Dataset/KeyValueStore** 저장, 재시도/에러 처리. HTTP 크롤러와
헤드리스(Playwright) 크롤러를 같은 API로 전환. JS/TS가 1급, Python도 공식 지원.

crawl4ai가 "웹→LLM Markdown"에 특화라면, crawlee는 "**대규모 수집 파이프라인의 뼈대**"다.

## 설치

```bash
# Python
pip install 'crawlee[all]'          # beautifulsoup/playwright extra 포함
# playwright 브라우저: playwright install
# JS/TS
npm install crawlee playwright
```

## 최소 예제 (Python, HTTP 크롤러)

```python
import asyncio
from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext

async def main():
    crawler = BeautifulSoupCrawler(max_requests_per_crawl=50)   # 안전 상한

    @crawler.router.default_handler
    async def handler(ctx: BeautifulSoupCrawlingContext):
        await ctx.push_data({"url": ctx.request.url, "title": ctx.soup.title.string})
        await ctx.enqueue_links()          # 페이지 내 링크 자동 큐잉(중복제거)

    await crawler.run(["https://crawlee.dev"])
    await crawler.export_data("out.json")

asyncio.run(main())
```

JS 렌더가 필요하면 `PlaywrightCrawler` / `PlaywrightCrawlingContext`로 교체(같은 라우터 API).

## 최소 예제 (JS/TS)

```js
import { PlaywrightCrawler, Dataset } from 'crawlee';
const crawler = new PlaywrightCrawler({
  async requestHandler({ page, request, enqueueLinks, pushData }) {
    await pushData({ url: request.url, title: await page.title() });
    await enqueueLinks();
  },
  maxRequestsPerCrawl: 50,
});
await crawler.run(['https://crawlee.dev']);
```

## 패턴 / 함정

- `max_requests_per_crawl`, 동시성, `RequestQueue`로 대규모를 통제. 프록시는 `ProxyConfiguration`.
- HTTP 크롤러(BeautifulSoup)로 충분하면 브라우저를 쓰지 말 것(속도·자원). JS 필요할 때만 Playwright.
- 결과는 `push_data` → `export_data`/Dataset. 재실행 시 큐/스토리지 상태가 남을 수 있으니 정리.
