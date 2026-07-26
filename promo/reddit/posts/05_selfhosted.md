# r/selfhosted

## Title
`Self-hosted crawl toolbox (CLI) — route between curl impersonation, crawl4ai, scrapling, optional browser agent`

## Body
Not a Docker mega-stack — a **library/CLI you run where your jobs already are**.

I self-host scrapers and got sick of maintaining five entrypoints. `omk-crawl` is one command that picks a fetcher and only escalates when the lighter path fails:

- `curl_cffi` for TLS/JA3 issues
- `crawl4ai` for JS → markdown
- `scrapling` when stealth matters
- `browser-use` only if you actually want an LLM agent (and have a key)

```bash
pip install omk-crawl curl_cffi
omk-crawl https://example.com -o page.md
omk-crawl reddit://r/selfhosted --json
```

Why it might fit this sub:
- no SaaS account
- runs on a VPS/cron
- optional tools — don’t install Playwright until you need it
- Apache-2.0

Repo: https://github.com/dmae97/omk-crawling  
PyPI: https://pypi.org/project/omk-crawl/2.10.0/

Happy to take “please add Docker compose” feedback if that’s what people actually want.

## Image
`images/card_stack.png`

## Flair
Software Release / Guide
