"""04 — arun_many 로 여러 URL 병렬 수집 + 메모리 적응 디스패처.

수십~수천 URL 을 동시성/레이트리밋/메모리 상한 아래서 안전하게 처리한다.
"""
import asyncio

from crawl4ai import (
    AsyncWebCrawler,
    CrawlerRunConfig,
    CacheMode,
    MemoryAdaptiveDispatcher,
    RateLimiter,
)

URLS = [f"https://quotes.toscrape.com/page/{i}/" for i in range(1, 6)]


async def main() -> None:
    dispatcher = MemoryAdaptiveDispatcher(
        memory_threshold_percent=80.0,   # 메모리 압박 시 자동 스로틀
        max_session_permit=5,            # 동시성 상한
        rate_limiter=RateLimiter(base_delay=(0.5, 1.5)),
    )
    cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS)
    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun_many(URLS, config=cfg, dispatcher=dispatcher)
        ok = sum(1 for r in results if r.success)
        print(f"{ok}/{len(results)} succeeded")
        for r in results:
            n = len(r.markdown.raw_markdown) if r.success else 0
            print(f"  ok={r.success} {n:>6} chars  {r.url}")


if __name__ == "__main__":
    asyncio.run(main())
