# r/LocalLLaMA

## Title
`Small open tool for “URL → clean markdown” before you stuff pages into a local LLM`

## Body
If you build local RAG / agent loops, you already know the painful part is often **getting text out of the web**, not the model.

`omk-crawl` is a router that tries light fetchers first and escalates to browser markdown tools when the page is a JS shell:

```bash
pip install omk-crawl crawl4ai
omk-crawl https://docs.example.com -o page.md
# fit/noise-reduced markdown when crawl4ai is available
```

Also useful bits for agent toolchains:
- `--diagnose` before spending browser time
- JSON output for pipelines
- optional browser-use adapter if you truly need an agent (bring your own key)

Not an LLM itself — plumbing.

https://github.com/dmae97/omk-crawling  
https://pypi.org/project/omk-crawl/

## Image
`images/card_hero.png`

## Tone
Keep claims about “bypassing bot protection” minimal; frame as fetch routing for RAG.
