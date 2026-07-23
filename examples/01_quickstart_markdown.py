"""01 — 페이지를 LLM용 Markdown으로.

raw_markdown(전체)와 fit_markdown(노이즈 제거 본문)을 함께 뽑는다.
    pip install -U crawl4ai && crawl4ai-setup
"""
import asyncio

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

URL = "https://quotes.toscrape.com/"


async def main() -> None:
    cfg = CrawlerRunConfig(
        cache_mode=CacheMode.ENABLED,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.48, threshold_type="fixed")
        ),
    )
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(URL, config=cfg)
        if not result.success:
            print("FAILED:", result.error_message)
            return
        print("raw_markdown:", len(result.markdown.raw_markdown), "chars")
        print("fit_markdown:", len(result.markdown.fit_markdown), "chars")
        print("--- fit preview ---")
        print(result.markdown.fit_markdown[:800])


if __name__ == "__main__":
    asyncio.run(main())
