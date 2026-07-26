# r/devtools

## Title
`Devtool drop: omk-crawl — diagnose + auto-escalate scrapers when a site starts fighting you`

## Body
Built for the moment your scraper returns 200 with an empty shell.

```bash
omk-crawl https://target.example --diagnose
# shows preferred tool order for detected block type
omk-crawl https://target.example -v
```

What’s inside 2.10.0:
- block detection (CF / Akamai / DataDome / Imperva markers, TLS, JS shell)
- tool chain reorder mid-run
- unified adapter capabilities (timeout/proxy/headers — no silent ignore)
- extras: Reddit/Baemin helpers, APK/IPA static, App Store lookup

```bash
pip install omk-crawl==2.10.0 curl_cffi
```

GitHub: https://github.com/dmae97/omk-crawling  
PyPI: https://pypi.org/project/omk-crawl/

I’m the author — roast the CLI flags if they’re bad.

## Image
`images/card_stack.png`
