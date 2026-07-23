# omk-crawling

> **웹·데이터 수집/추출 툴박스** — 10개 도구를 목적별로 라우팅하는 OMK 스킬.
> 크롤링은 하나의 도구로 끝나지 않는다. 바이트 확보 → 순회 → 브라우저 조작 → 구조화 → 변환 → 모바일.

## Quick Start

```bash
# 필요한 도구만 설치
pip install -U crawl4ai && crawl4ai-setup   # 웹→Markdown·딥크롤·MCP
pip install scrapy 'crawlee[all]' browser-use autoscraper 'markitdown[all]' curl_cffi scrapling
```

```python
import asyncio
from crawl4ai import AsyncWebCrawler

async def main():
    async with AsyncWebCrawler() as crawler:
        r = await crawler.arun("https://example.com")
        print(r.markdown)

asyncio.run(main())
```

## Tool Router

| Need | Tool | Layer |
|------|------|-------|
| Single blocked URL (403/WAF) | `insane-search` | ① Fetch |
| TLS/JA3 fingerprint block | `curl-impersonate` / `curl_cffi` | ① Fetch |
| Anti-bot stealth + Cloudflare | `scrapling` | ① Fetch |
| Large-scale classic crawl | `scrapy` | ② Crawl |
| Queue · auto-scale · proxy | `crawlee` | ② Crawl |
| Web → LLM Markdown · deep crawl · MCP | `crawl4ai` | ② Crawl |
| LLM agent drives browser | `browser-use` | ③ Browser |
| Learn extraction from examples | `autoscraper` | ④ Extract |
| PDF/Office/image/audio → Markdown | `markitdown` | ⑤ Convert |
| Android-only data | `scrcpy` | ⑥ Mobile |

Full decision tree: [`references/routing.md`](references/routing.md)

## Repo Structure

```
SKILL.md              # OMK skill definition (routing + quick-ref)
NOTICE.md             # Licenses + shoutouts to all 11 upstream projects
LICENSE.txt           # Apache-2.0 (+ crawl4ai attribution addendum)
CHANGELOG.md          # Version history
references/
  routing.md          # Cross-tool decision tree
  choosing.md         # crawl4ai internal mode selection
  extraction.md       # Markdown & structured extraction
  deep-crawl.md       # Deep crawl · adaptive · batch
  docker-mcp.md       # Docker server · REST · MCP
  cli.md              # crwl CLI reference
  tools/              # Per-tool deep-dive (10 files)
examples/             # Runnable Python examples (7 files)
scripts/
  check-versions.sh   # Upstream version drift checker
```

## Shoutouts 🙏

Built on the shoulders of 11 amazing projects. See [NOTICE.md](NOTICE.md) for full attribution.

| # | Project | License | What it does |
|---|---------|---------|--------------|
| 1 | [crawl4ai](https://github.com/unclecode/crawl4ai) | Apache-2.0 | LLM-first web crawler |
| 2 | [scrapy](https://github.com/scrapy/scrapy) | BSD-3-Clause | Mature crawl framework |
| 3 | [crawlee](https://github.com/apify/crawlee) | Apache-2.0 | Production crawl infra |
| 4 | [browser-use](https://github.com/browser-use/browser-use) | MIT | LLM browser agent |
| 5 | [curl-impersonate](https://github.com/lwthiker/curl-impersonate) | MIT | TLS fingerprint bypass |
| 6 | [curl_cffi](https://github.com/lexiforest/curl_cffi) | MIT | Python curl-impersonate |
| 7 | [autoscraper](https://github.com/alirezamika/autoscraper) | MIT | Example-based extraction |
| 8 | [markitdown](https://github.com/microsoft/markitdown) | MIT | File → Markdown |
| 9 | [scrcpy](https://github.com/Genymobile/scrcpy) | Apache-2.0 | Android mirror/control |
| 10 | [scrapling](https://github.com/d4vinci/Scrapling) | BSD-3-Clause | Stealth scraping |
| 11 | insane-search | OMK | Multi-source deep search |

## License

Apache-2.0. See [LICENSE.txt](LICENSE.txt) and [NOTICE.md](NOTICE.md).

> This product includes software developed by UncleCode (https://x.com/unclecode)
> as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai).
