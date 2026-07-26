# r/commandline

## Title
`omk-crawl — one CLI that escalates fetchers until the page actually comes back`

## Body
I wanted a single binary-feeling CLI for “get me markdown/JSON from this URL” without remembering which Python stack works today.

```text
omk-crawl <url>                 # auto
omk-crawl <url> -t curl_cffi    # force
omk-crawl <url> --diagnose      # dry-run tool plan
omk-crawl <url> -o out.md
omk-crawl --tools
```

Also scheme helpers for non-web surfaces:

```text
omk-crawl reddit://r/commandline
omk-crawl baemin://37.5,127.0
omk-crawl appstore://378084485
omk-crawl ./app.apk
```

Install:

```bash
pipx install omk-crawl
# or
uv pip install omk-crawl==2.10.0 curl_cffi
```

It’s Python under the hood, Apache-2.0, optional deps per tool.

GitHub: https://github.com/dmae97/omk-crawling  
PyPI: https://pypi.org/project/omk-crawl/

## Image
`images/card_cli.png` — terminal-style card

## Flair
Tool / Software
