<p align="center">
  <img src="assets/omk-crawling-hero.jpeg" alt="OMK-Crawling — Cyberpunk anime girl at multi-monitor crawling workstation, neon teal and magenta Night City aesthetic" width="100%" />
</p>

<h1 align="center">omk-crawling</h1>

<p align="center">
  <strong>Smart web crawling toolbox — 10 tools, one router.</strong><br/>
  Fetch → Crawl → Browser → Extract → Convert → Mobile. Auto-escalates until it works.
</p>

<p align="center">
  <a href="https://github.com/user/omk-crawling/blob/main/LICENSE.txt"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-00d7ff?style=for-the-badge" /></a>
  <a href="https://pypi.org/project/omk-crawl/"><img alt="PyPI" src="https://img.shields.io/badge/pypi-omk--crawl-ff2d95?style=for-the-badge&logo=pypi&logoColor=white" /></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776ab?style=for-the-badge&logo=python&logoColor=white" />
  <img alt="Tools" src="https://img.shields.io/badge/tools-10-success?style=for-the-badge" />
  <img alt="Tests" src="https://img.shields.io/badge/tests-27%20passed-brightgreen?style=for-the-badge" />
</p>

<p align="center">
  <a href="https://github.com/user/omk-crawling/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/user/omk-crawling?style=social" /></a>
  <a href="https://github.com/user/omk-crawling/network/members"><img alt="GitHub forks" src="https://img.shields.io/github/forks/user/omk-crawling?style=social" /></a>
  <a href="https://github.com/user/omk-crawling/issues"><img alt="GitHub issues" src="https://img.shields.io/github/issues/user/omk-crawling?style=social" /></a>
</p>

<p align="center">
  <code>omk-crawl https://example.com</code> — one command. TLS detection → auto-escalation → unified result.
</p>

---

## Why omk-crawling?

Web crawling never ends with one tool. A site might block your TLS fingerprint, require JS rendering,
hide behind Cloudflare, or need a full LLM agent to navigate a login flow. **omk-crawling routes
across 10 tools automatically**, escalating from the lightest to the heaviest until the data is yours.

```
curl_cffi (0ms browser) → crawl4ai (render) → scrapling (stealth) → browser-use (LLM agent)
```

---

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
   TLS/JA3 spoof        browser render      stealth browser
   0ms browser           + Markdown          + anti-bot bypass
          │                    │                    │
          └────────────────────┼────────────────────┘
                               ▼ (still blocked?)
                        ④ browser-use
                        LLM agent drives browser
                               │
                               ▼
                        CrawlResult (unified)
                        .markdown .html .extracted
```

---

## Install

### As OMK Skill (git clone + one-liner)

```bash
git clone https://github.com/user/omk-crawling.git
cd omk-crawling
./install.sh              # symlink (dev mode, edits reflect instantly)
./install.sh --copy       # stable copy
./install.sh --uninstall  # remove
```

### As Python Package

```bash
pip install omk-crawl              # core (zero-dep router + CLI)
pip install omk-crawl[curl]        # + curl_cffi (TLS fingerprint)
pip install omk-crawl[crawl4ai]    # + crawl4ai (browser + markdown)
pip install omk-crawl[scrapling]   # + scrapling (stealth)
pip install omk-crawl[browser]     # + browser-use (LLM agent)
pip install omk-crawl[all]         # everything
```

---

## Quick Start

### CLI

```bash
omk-crawl https://example.com                    # auto-escalate
omk-crawl https://example.com --tool curl_cffi   # force specific tool
omk-crawl https://example.com -o out.md          # save markdown to file
omk-crawl https://example.com --json             # JSON output
omk-crawl https://example.com -v                 # verbose escalation log
omk-crawl --diagnose https://example.com         # dry-run: what would we try?
omk-crawl --tools                                # list installed/missing tools
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
```

### Pipeline

```python
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

---

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

---

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
tests/                  # 27 tests (pytest)
references/             # Per-tool deep-dive docs (14 files)
examples/               # Runnable examples (7 files)
scripts/                # check-versions.sh
assets/                 # Hero image
SKILL.md                # OMK skill definition
NOTICE.md               # Licenses + shoutouts to all 11 projects
install.sh              # One-liner skill installer
```

---

## Development

```bash
pip install -e ".[all,dev]"
pytest tests/ -v                    # 27 tests
bash scripts/check-versions.sh      # upstream version drift
ruff check omk_crawl/               # lint
```

---

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

---

## License

Apache-2.0. See [LICENSE.txt](LICENSE.txt) and [NOTICE.md](NOTICE.md).

> This product includes software developed by UncleCode (https://x.com/unclecode)
> as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai).

---

## Star History

<p align="center">
  <a href="https://star-history.com/#user/omk-crawling&Date">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=user/omk-crawling&type=Date&theme=dark" />
      <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=user/omk-crawling&type=Date" />
      <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=user/omk-crawling&type=Date" width="100%" />
    </picture>
  </a>
</p>

---

<p align="center">
  <sub>Built with 💜 in the Night City · OMK//CONTROL · 2026</sub>
</p>
