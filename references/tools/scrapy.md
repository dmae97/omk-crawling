# scrapy — 클래식 대규모 크롤 프레임워크

- Repo: https://github.com/scrapy/scrapy · Docs: https://docs.scrapy.org
- PyPI `Scrapy` v2.17.0 · Python ≥3.10 · **BSD-3-Clause**

## 언제

성숙한 크롤 프레임워크가 필요할 때: 스파이더·**아이템 파이프라인**·**미들웨어**·`AutoThrottle`·재시도·
`robots.txt` 준수·재개·거대한 서드파티 생태계(scrapy-playwright, scrapy-redis 분산 등). 규모가 크고
오래 굴릴 크롤이면 scrapy. 순수 HTTP가 기본이라 빠르고 가벼우며, JS는 `scrapy-playwright`로 확장.

## 설치 / 프로젝트

```bash
pip install scrapy
scrapy startproject myproj && cd myproj
scrapy genspider quotes quotes.toscrape.com
scrapy crawl quotes -O quotes.json      # -O 덮어쓰기, -o 이어쓰기
scrapy shell "https://quotes.toscrape.com"   # 셀렉터 인터랙티브 실험
```

## 최소 스파이더

```python
import scrapy

class QuotesSpider(scrapy.Spider):
    name = "quotes"
    start_urls = ["https://quotes.toscrape.com/"]
    custom_settings = {"AUTOTHROTTLE_ENABLED": True, "ROBOTSTXT_OBEY": True}

    def parse(self, response):
        for q in response.css("div.quote"):
            yield {
                "text": q.css("span.text::text").get(),
                "author": q.css("small.author::text").get(),
            }
        next_page = response.css("li.next a::attr(href)").get()
        if next_page:
            yield response.follow(next_page, self.parse)   # 페이지네이션
```

## 패턴 / 함정

- **AutoThrottle + DOWNLOAD_DELAY**로 서버 부담·차단을 줄인다. `CONCURRENT_REQUESTS`로 동시성 상한.
- 정제·검증·저장은 **Item Pipeline**, 헤더·프록시·재시도는 **Downloader Middleware**로 분리.
- JS 렌더가 필요하면 `scrapy-playwright`. 분산은 `scrapy-redis`.
- `ROBOTSTXT_OBEY=True`(기본)로 robots 준수. 대규모 전 `scrapy shell`로 셀렉터를 먼저 확정.
