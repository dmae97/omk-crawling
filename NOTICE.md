# NOTICE — omk-crawling

This OMK skill packages **documentation, routing, and examples** for the crawling/scraping/
extraction toolbox below. **No upstream library is vendored here** — each is installed
separately from PyPI / npm / native packages. Only docs and examples live in this directory.
Respect each upstream license when you use the tool.

## Upstream Tools

| Tool | Version | License | Upstream |
|------|---------|---------|----------|
| crawl4ai | 0.9.2 | Apache-2.0 **(+ attribution addendum)** | https://github.com/unclecode/crawl4ai |
| scrapy | 2.17.0 | BSD-3-Clause | https://github.com/scrapy/scrapy |
| crawlee (JS) | 3.17.0 | Apache-2.0 | https://github.com/apify/crawlee |
| crawlee (Python) | 1.8.3 | Apache-2.0 | https://github.com/apify/crawlee-python |
| browser-use | 0.13.6 | MIT | https://github.com/browser-use/browser-use |
| curl-impersonate | native (C) | MIT | https://github.com/lwthiker/curl-impersonate |
| curl_cffi (binding) | 0.15.0 | MIT | https://github.com/lexiforest/curl_cffi |
| autoscraper | 1.1.14 | MIT | https://github.com/alirezamika/autoscraper |
| markitdown | 0.1.6 | MIT | https://github.com/microsoft/markitdown |
| scrcpy | 4.1 | Apache-2.0 | https://github.com/Genymobile/scrcpy |
| scrapling | 0.4.11 | BSD-3-Clause | https://github.com/d4vinci/Scrapling |

## Shoutouts 🙏

omk-crawling exists because these 11 projects did the hard work first.
Every tool below is referenced, documented, and routed by this skill.
**Thank you to every maintainer and contributor.**

### 1. Crawl4AI — [unclecode/crawl4ai](https://github.com/unclecode/crawl4ai)
> The LLM-first web crawler. Markdown output, deep crawling, MCP server, adaptive crawling.
> By **UncleCode** ([@unclecode](https://x.com/unclecode)). Apache-2.0 + attribution.
>
> *"This product includes software developed by UncleCode (https://x.com/unclecode)
> as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai)."*

### 2. Scrapy — [scrapy/scrapy](https://github.com/scrapy/scrapy)
> The mature, battle-tested crawling framework. Spiders, pipelines, middleware, 10+ years of ecosystem.
> By **Zyte** and the Scrapy community. BSD-3-Clause.

### 3. Crawlee — [apify/crawlee](https://github.com/apify/crawlee)
> Production-grade crawling infrastructure. Queues, auto-scaling, proxy rotation, unified storage.
> By **Apify**. Apache-2.0. JS/TS first-class + official Python port.

### 4. Browser-Use — [browser-use/browser-use](https://github.com/browser-use/browser-use)
> LLM agents that drive a real browser. Login, multi-step navigation, form filling, clicking.
> By **Browser Use team**. MIT.

### 5. curl-impersonate — [lwthiker/curl-impersonate](https://github.com/lwthiker/curl-impersonate)
> Browser TLS/JA3/HTTP2 fingerprint impersonation. The lightest way past fingerprint-based 403s.
> By **lwthiker**. MIT.

### 6. curl_cffi — [lexiforest/curl_cffi](https://github.com/lexiforest/curl_cffi)
> Python bindings for curl-impersonate. `requests`-like API with `impersonate="chrome124"`.
> By **lexiforest**. MIT.

### 7. Autoscraper — [alirezamika/autoscraper](https://github.com/alirezamika/autoscraper)
> Give it examples, it learns extraction rules. No browser, no selectors, ultra-lightweight.
> By **Alireza Mika**. MIT.

### 8. MarkItDown — [microsoft/markitdown](https://github.com/microsoft/markitdown)
> Convert PDF, Office, images, audio, HTML → Markdown for LLM input.
> By **Microsoft**. MIT.

### 9. scrcpy — [Genymobile/scrcpy](https://github.com/Genymobile/scrcpy)
> Android screen mirroring and control. The bridge when data lives only in a mobile app.
> By **Genymobile** (Romain Vimont). Apache-2.0.

### 10. Scrapling — [d4vinci/Scrapling](https://github.com/d4vinci/Scrapling)
> Stealth scraping framework. Cloudflare Turnstile bypass, adaptive selectors, spider framework.
> By **d4vinci**. BSD-3-Clause.

### 11. insane-search — OMK internal skill
> Exhaustive multi-source search: rg → ast-grep → knowledge graph → git history → MCP.
> Built within the OMK agent stack. Used as the "single blocked URL" breaker in this toolbox.

## crawl4ai attribution (required)

crawl4ai's license (Apache-2.0) carries a mandatory attribution addendum (see
[`LICENSE.txt`](LICENSE.txt)). Any distribution, publication, or public use of crawl4ai or
derivative works must display:

> "This product includes software developed by UncleCode (https://x.com/unclecode)
> as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai)."

Short form for agent/tool output:

> This project uses Crawl4AI (https://github.com/unclecode/crawl4ai) for web data extraction.

## License Summary

| License | Tools | Key obligation |
|---------|-------|----------------|
| Apache-2.0 | crawl4ai, crawlee, scrcpy | Attribution + NOTICE retention. crawl4ai has extra addendum. |
| MIT | browser-use, curl-impersonate, curl_cffi, autoscraper, markitdown | Retain copyright + license notice. |
| BSD-3-Clause | scrapy, scrapling | Retain copyright + license. No endorsement use. |
| OMK internal | insane-search | Internal skill, no external distribution. |

## Notes

- MIT / BSD-3-Clause / Apache-2.0 all permit use with attribution; retain upstream copyright
  and license notices when you redistribute any of these libraries' code.
- Only crawl4ai's full license text is bundled ([`LICENSE.txt`](LICENSE.txt)) because its
  addendum explicitly requires displaying attribution. For the others, this NOTICE + the
  upstream links satisfy attribution since no code is vendored.
