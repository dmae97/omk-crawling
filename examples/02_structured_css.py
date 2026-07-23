"""02 — CSS 스키마로 구조화 추출 (LLM 없이, 무료/결정적).

quotes.toscrape.com 의 각 인용문을 {text, author} JSON으로 뽑는다.
반복 구조는 항상 이 방식을 먼저 시도한다.
"""
import asyncio
import json

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, JsonCssExtractionStrategy

URL = "https://quotes.toscrape.com/"

SCHEMA = {
    "name": "Quotes",
    "baseSelector": "div.quote",          # 반복 단위
    "fields": [
        {"name": "text", "selector": "span.text", "type": "text"},
        {"name": "author", "selector": "small.author", "type": "text"},
        {"name": "tags", "selector": "a.tag", "type": "text"},   # 첫 매치
    ],
}


async def main() -> None:
    cfg = CrawlerRunConfig(extraction_strategy=JsonCssExtractionStrategy(SCHEMA))
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(URL, config=cfg)
        if not result.success:
            print("FAILED:", result.error_message)
            return
        rows = json.loads(result.extracted_content)
        print(f"extracted {len(rows)} quotes")
        print(json.dumps(rows[0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
