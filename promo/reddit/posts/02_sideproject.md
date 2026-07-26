# r/sideproject

## Title
`I built a “smart crawl router” so I stop duct-taping crawl4ai + curl_cffi + scrapling every weekend`

## Body
Built this for myself first.

**Problem:** every scraping side project starts the same — curl works until it doesn’t, then Playwright, then “why is Cloudflare mad”, then a different library, then the glue code rots.

**Thing I shipped:** `omk-crawl` — a tiny Python toolbox that:

- auto-escalates tools light → heavy
- exposes one CLI: `omk-crawl <url>`
- has adapters for markdown, stealth, LLM browser
- plus a few real-world target clients (Reddit JSON, Baemin shops, mobile APK/IPA surface)

```bash
pip install omk-crawl==2.10.0
omk-crawl https://example.com --diagnose
omk-crawl reddit://r/sideproject
```

What I’m proud of in 2.10.0:
- detection-aware routing (WAF/TLS/JS get different tool orders)
- mobile layer without needing a device for static analysis
- Reddit path that doesn’t die on the new www “please wait for verification” shell

Links:
- https://pypi.org/project/omk-crawl/
- https://github.com/dmae97/omk-crawling

Feedback welcome — especially “this API is confusing” and “this should be a single function”.

## Image
`images/card_hero.png` or hero jpeg

## Flair
Show / Feedback
