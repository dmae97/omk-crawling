"""03 — BFS 딥크롤로 여러 페이지 순회 (도메인/깊이/페이지 상한).

quotes.toscrape.com 의 페이지네이션을 따라가며 각 페이지의 마크다운 길이를 출력.
항상 max_depth / max_pages / include_external 로 확산을 통제한다.
"""
import asyncio

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

START = "https://quotes.toscrape.com/"


async def main() -> None:
    cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=2,             # 시작 URL 기준 깊이
            include_external=False,  # 같은 도메인만
            max_pages=10,            # 안전 상한
        ),
        stream=False,                # True면 async iterator 로 스트리밍
    )
    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun(START, config=cfg)
        print(f"crawled {len(results)} pages")
        for r in results:
            depth = r.metadata.get("depth") if r.metadata else None
            n = len(r.markdown.raw_markdown) if r.success else 0
            print(f"  depth={depth} ok={r.success} {n:>6} chars  {r.url}")


if __name__ == "__main__":
    asyncio.run(main())
