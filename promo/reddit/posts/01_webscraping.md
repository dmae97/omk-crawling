# r/webscraping — Monthly Self-Promotion (July 2026)

**Where:** comment under the Monthly Self-Promotion sticky (preferred), or a short text post if mods allow.

## Title (only if making a post)
`[Self-Promo] omk-crawl 2.10.0 — auto-escalating crawl toolbox (curl_cffi → crawl4ai → scrapling → browser-use)`

## Body
Hey — author of **omk-crawl** here (Apache-2.0).

I got tired of rewriting the same “which fetcher do I use this week?” glue, so I shipped a small router that escalates only when needed:

1. TLS fingerprint (`curl_cffi`)
2. browser markdown (`crawl4ai`)
3. stealth (`scrapling`)
4. LLM browser agent (`browser-use`) last resort

Also ships target helpers I’ve actually used:
- **Baemin** shop list via public food-shop-list geo API (`baemin://lat,lng`)
- **Reddit** listings via old.reddit JSON warmup (www challenge shell is useless alone)
- **APK/IPA** static surface + App Store metadata (`appstore://`)

```bash
pip install omk-crawl==2.10.0 curl_cffi
omk-crawl https://example.com -v
omk-crawl reddit://r/webscraping
omk-crawl baemin://37.5,127.0
```

- PyPI: https://pypi.org/project/omk-crawl/
- GitHub: https://github.com/dmae97/omk-crawling
- v2.10.0 notes: https://github.com/dmae97/omk-crawling/releases/tag/v2.10.0

Happy to answer architecture / adapter contract questions. Not selling a SaaS — library only.

## Image
`images/card_router.png` — “escalation ladder” diagram

## Flair
Self-Promo / Show
