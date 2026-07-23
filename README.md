# omk-crawling

> **웹·데이터 수집/추출 툴박스** — 10개 도구를 목적별로 라우팅하는 OMK 스킬 + Python 패키지.
> 크롤링은 하나의 도구로 끝나지 않는다. 바이트 확보 → 순회 → 브라우저 조작 → 구조화 → 변환 → 모바일.

```
omk-crawl https://example.com
```

한 줄이면 → TLS 감지 → 자동 에스컬레이션 → 통합 결과.

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │            SmartRouter                   │
                    │  detect → route → escalate → result     │
                    └──────────┬──────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
   ① curl_cffi          ② crawl4ai          ③ scrapling
   TLS/JA3 위장          브라우저 렌더         스텔스 브라우저
   0ms browser           + Markdown           + anti-bot
          │                    │                    │
          └────────────────────┼────────────────────┘
                               ▼ (still blocked?)
                        ④ browser-use
                        LLM 에이전트가 운전
                               │
                               ▼
                        CrawlResult (unified)
                        .markdown .html .extracted
```

**Escalation chain**: `curl_cffi` → `crawl4ai` → `scrapling` → `browser-use`
각 단계에서 응답을 분석(detect.py)하고, 차단되면 다음 도구로 자동 전환.

## Install

### As OMK Skill (git clone + one-liner)

```bash
git clone https://github.com/user/omk-crawling.git
cd omk-crawling
./install.sh              # symlink (개발용, 수정 즉시 반영)
./install.sh --copy       # copy (안정적)
./install.sh --uninstall  # 제거
```

### As Python Package

```bash
# Core (zero-dep router + CLI)
pip install omk-crawl

# Pick your tools
pip install omk-crawl[curl]        # curl_cffi (TLS fingerprint)
pip install omk-crawl[crawl4ai]    # crawl4ai (browser + markdown)
pip install omk-crawl[scrapling]   # scrapling (stealth)
pip install omk-crawl[browser]     # browser-use (LLM agent)
pip install omk-crawl[all]         # everything
```

## Quick Start

### CLI

```bash
omk-crawl https://example.com                    # auto-escalate
omk-crawl https://example.com --tool curl_cffi   # force tool
omk-crawl https://example.com -o out.md          # save markdown
omk-crawl https://example.com --json             # JSON output
omk-crawl https://example.com -v                 # verbose escalation
omk-crawl --diagnose https://example.com         # dry-run: what would we try?
omk-crawl --tools                                # list installed tools
omk-crawl report.pdf                             # file → markdown (markitdown)
```

### Python

```python
from omk_crawl import crawl

# One-liner with auto-escalation
r = crawl("https://example.com")
print(r.markdown)       # LLM-ready markdown
print(r.summary())      # [ok] https://example.com | via curl_cffi | HTTP 200 | 42ms

# Verbose escalation
r = crawl("https://protected-site.com", verbose=True)
#   [omk-crawl] [1/4] Trying curl_cffi...
#   [omk-crawl]   ✗ curl_cffi: blocked — TLS fingerprint block
#   [omk-crawl] [2/4] Trying crawl4ai...
#   [omk-crawl]   ✓ crawl4ai succeeded (1204ms)

# Force specific tool
r = crawl("https://example.com", tool="scrapling")

# Pipeline: fetch → extract → convert
from omk_crawl.pipeline import Pipeline
result = (
    Pipeline()
    .fetch()
    .extract_css("div.product", {"title": "h2", "price": ".price"})
    .to_markdown()
    .run("https://shop.example.com")
)
print(result.extracted)  # [{"title": "...", "price": "..."}]
```

### Async

```python
import asyncio
from omk_crawl import crawl_async

async def main():
    r = await crawl_async("https://example.com")
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
omk_crawl/              # Python package
  __init__.py           # Public API: crawl(), CrawlResult
  router.py             # SmartRouter — auto-detect + escalate
  detect.py             # Block detection (TLS, CF, JS, WAF)
  result.py             # Unified CrawlResult dataclass
  pipeline.py           # Composable fetch → extract → convert
  cli.py                # CLI entry point (omk-crawl)
  tools/                # Tool adapters (6 adapters)
    base.py             # BaseTool ABC
    curl_cffi_tool.py   # ① TLS fingerprint
    crawl4ai_tool.py    # ② Browser + Markdown
    scrapling_tool.py   # ③ Stealth
    browser_use_tool.py # ④ LLM agent
    autoscraper_tool.py # Example-based extraction
    markitdown_tool.py  # File → Markdown
tests/                  # 27 tests (pytest)
references/             # Per-tool deep-dive docs (14 files)
examples/               # Runnable examples (7 files)
scripts/                # check-versions.sh
SKILL.md                # OMK skill definition
NOTICE.md               # Licenses + shoutouts to all 11 projects
```

## Development

```bash
pip install -e ".[all,dev]"
pytest tests/ -v                    # 27 tests
bash scripts/check-versions.sh      # upstream version drift
ruff check omk_crawl/               # lint
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
