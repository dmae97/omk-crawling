# r/Python

## Title options
1. `omk-crawl 2.10.0 — auto-escalating web crawl toolbox (curl_cffi / crawl4ai / scrapling / browser-use)`
2. `[Showcase] A small router for Python scraping stacks instead of one-off scripts`

## Body (Showcase thread comment OR text post with Show flair)

**Affiliation:** I’m the author.

`omk-crawl` is a zero-core-dep CLI/router. Optional extras pull in the heavy tools only when you want them.

```python
from omk_crawl import crawl
r = crawl("https://example.com", verbose=True)
print(r.tool, r.markdown[:500])
```

```bash
pip install "omk-crawl[targets]"   # or pip install omk-crawl curl_cffi
omk-crawl https://httpbin.org/html -o out.md
omk-crawl --tools
```

Design choices that might interest this sub:
- adapter contract (`capabilities`, unsupported kwargs reported, not silent no-ops)
- mid-escalation re-route from live block detection
- typed-ish result object (`CrawlResult`) shared across tools
- target clients as plain Python modules (Baemin, Reddit, Naver helpers)

Not a framework religion post — just a toolbox so scripts stay boring.

- PyPI: https://pypi.org/project/omk-crawl/  
- Source: https://github.com/dmae97/omk-crawling  
- License: Apache-2.0 (upstream notices preserved)

## Image
`images/card_cli.png`

## Notes
Prefer the monthly Showcase thread if active. Avoid “best scraper ever” tone.
