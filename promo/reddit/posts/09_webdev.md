# r/webdev

## Title
`Open-source CLI that retries the right scraper when a site returns a blank JS shell`

## Body
Frontend folks: ever curl a marketing site and get `<div id="root"></div>`?

I published **omk-crawl** — a small Python CLI that treats that as a signal to escalate:

curl-impersonate → headless markdown → stealth → (optional) browser agent

```bash
pip install omk-crawl curl_cffi
omk-crawl https://yoursite.example -v -o out.md
```

Useful if you:
- archive docs
- build internal content tools
- prototype scrapers without rewriting glue each time

Apache-2.0, on PyPI as `omk-crawl` 2.10.0.

Repo: https://github.com/dmae97/omk-crawling

## Image
`images/card_router.png`
