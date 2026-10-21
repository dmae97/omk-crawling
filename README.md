<p align="center">
  <img src="assets/omk-crawling-redteam-hero.png" alt="Omk crawling red-team crawler in a neon server cathedral" width="100%" />
</p>

<h1 align="center">omk-crawling</h1>

<p align="center">
  <strong>THE CRAWLER THAT REFUSES TO DIE.</strong><br/>
  Fetch. Escalate. Walk the site. Rotate the pool. Leave evidence.
</p>

<p align="center">
  <a href="https://github.com/dmae97/omk-crawling/releases/tag/v2.16.0"><img alt="release" src="https://img.shields.io/badge/release-v2.16.0-ff2e88?style=for-the-badge" /></a>
  <a href="https://github.com/dmae97/omk-crawling/blob/main/LICENSE.txt"><img alt="license" src="https://img.shields.io/badge/license-Apache--2.0-00d7ff?style=for-the-badge" /></a>
  <img alt="python" src="https://img.shields.io/badge/python-3.10%2B-3776ab?style=for-the-badge&logo=python&logoColor=white" />
  <img alt="tests" src="https://img.shields.io/badge/tests-714%20passed-22c55e?style=for-the-badge" />
</p>

<p align="center">
  <code>omk-crawl https://example.com</code><br/>
  One command. Eight-stage escalation. One honest result.
</p>

> **괴상한 한 줄:** 사이트가 문을 바꾸면 라우터가 경로를 바꾼다. 프록시가 죽으면 소각하고, 새 리스트를 긁고, 다시 시작한다.
>
> This is a top-tier crawling architecture, not a magic universal-bypass claim. It breaks through tool friction, not authorization, paywalls, robots policies, or somebody else’s account.

## What this thing actually does

Most crawlers pick one transport and pray. `omk-crawling` treats access as an observable state machine:

```text
URL
 │
 ├─ detect target and robots policy
 ├─ try the lightest viable adapter
 ├─ detect empty shells, TLS blocks, WAF pages, and transport errors
 ├─ escalate only when the evidence says to
 ├─ normalize every outcome into CrawlResult
 └─ stop with a reason instead of pretending success
```

The web escalation chain is deliberately heavy at the end:

```text
insane_search → curl_cffi → crawl4ai → scrapling
      → camoufox → patchright → nodriver → browser-use
```

The crawler does not silently turn a refusal into a success. It records the tool, status, timing, detection, route decisions, and stop reason.

## New in v2.16: the infinite loop

<p align="center">
  <img src="assets/omk-crawling-proxy-rotation.png" alt="Gloved hand holding rotating proxy masks in a cracked neon firewall" width="100%" />
</p>

The free-proxy lane is a self-healing loop, not a random list pasted into a request:

```text
public proxy sources
       ↓
harvest → parse → deduplicate → freshness touch
       ↓
validate → rank → assign TLS profile
       ↓
rotate one proxy per attempt
       ↓
ok / 429 / 407 / block / error
       ↓
score up / backoff / burn / refill
       └───────────────────────────────↺
```

```bash
pip install "git+https://github.com/dmae97/omk-crawling.git#egg=omk-crawl[proxy]"

omk-crawl proxy://stats                              # offline pool snapshot
omk-crawl proxy://refresh                            # one harvest + validation pass
omk-crawl 'proxy://loop?cycles=5&interval=30'        # bounded loop
omk-crawl 'proxy://loop?cycles=0'                    # infinite until Ctrl+C
omk-crawl https://public.example --proxy-pool        # rotate per request
```

Feedback rules are explicit:

- `200` or another successful result: proxy health goes up.
- `429`: per-proxy exponential backoff.
- `407`: proxy is burned and persisted across restarts.
- blocked or transport error: failure streak and long cooldown.
- pool exhaustion: bounded synchronous refill, then a typed failure.
- source re-serves a proxy: `harvested_at` is refreshed, not duplicated.
- stale nodes are demoted by `STALE_SECONDS` and can be excluded with `fresh_only`.

Free proxies are untrusted third parties. Use this lane for public, authorized content only. Never send credentials, cookies, private data, or destructive requests through an unknown proxy.

## Quick start

### Install the OMK skill

```bash
git clone https://github.com/dmae97/omk-crawling.git
cd omk-crawling
./install.sh              # symlink, development mode
./install.sh --copy       # stable copy
./install.sh --uninstall  # remove the skill
```

The skill entrypoint is `SKILL.md`. The repository is also a self-contained OMK skill directory, so the router docs, tool references, examples, and guardrails stay next to the code.

### Install the package

```bash
pip install "git+https://github.com/dmae97/omk-crawling.git#egg=omk-crawl"
pip install "git+https://github.com/dmae97/omk-crawling.git#egg=omk-crawl[curl]"       # TLS-aware light fetch
pip install "git+https://github.com/dmae97/omk-crawling.git#egg=omk-crawl[crawl4ai]"   # browser rendering and Markdown
pip install "git+https://github.com/dmae97/omk-crawling.git#egg=omk-crawl[proxy]"      # free proxy harvest and SOCKS validation
pip install "git+https://github.com/dmae97/omk-crawling.git#egg=omk-crawl[all]"        # everything
```

The core router has no mandatory runtime dependency. Adapters are optional on purpose.

## CLI

```bash
# One URL, automatic escalation
omk-crawl https://example.com

# Diagnostics without fetching
omk-crawl --diagnose https://example.com
omk-crawl https://example.com --tools

# Bounded site traversal
omk-crawl https://docs.example.com --deep --max-pages 50 --depth 3
omk-crawl https://docs.example.com --deep --sitemap-seed
omk-crawl https://docs.example.com --deep --include '/docs/' -o docs.md
omk-crawl https://docs.example.com --deep --json > crawl.json

# Explicit adapter or output
omk-crawl https://example.com --tool curl_cffi -o page.md
omk-crawl https://example.com --json
omk-crawl https://example.com -v

# Public target adapters
omk-crawl reddit://r/programming
omk-crawl 'appstore://search?q=요기요'
omk-crawl 'baemin://shops?lat=36.83&lng=127.13&limit=40'

# Offline capture and mobile surfaces
omk-crawl capture.har --json
omk-crawl app.apk -o apk.md
omk-crawl app.ipa --json
omk-crawl android://
```

### Deep crawl controls

| Control | Meaning |
| --- | --- |
| `--max-pages N` | hard fetch budget, default `20` |
| `--depth N` | maximum link depth from the seed, default `2` |
| `--sitemap-seed` | seed the frontier from robots.txt sitemaps |
| `--same-domain` | allow subdomains using a zero-dependency domain heuristic |
| `--include REGEX` | only fetch matching URLs, repeatable |
| `--exclude REGEX` | skip matching URLs, repeatable |
| `--json` | emit page content, statuses, stats, and stop reason |

A deep crawl ends as `frontier_empty` or `max_pages`, not as an ambiguous timeout.

## Python API

```python
from omk_crawl import crawl
from omk_crawl.deep import DeepCrawlConfig, deep_crawl
from omk_crawl.rotate_crawl import RotationCrawler

single = crawl("https://example.com")

site = deep_crawl(
    "https://docs.example.com",
    DeepCrawlConfig(max_pages=50, max_depth=3, use_sitemap=True),
)
for page in site.pages:
    print(page.url, page.status.value, page.tool)
print(site.stop_reason)

rotated = RotationCrawler(attempts_per_url=4).crawl("https://public.example")
print(rotated.metadata.get("rotation"))
```

## Architecture

```text
                         ┌──────────────────┐
                         │   SmartRouter    │
                         │ detect → route   │
                         │ observe → learn  │
                         └────────┬─────────┘
                                  │
       ┌──────────────────────────┼──────────────────────────┐
       ▼                          ▼                          ▼
  single URL                 DeepCrawler                target adapters
  escalation                 BFS + sitemap              Reddit / Baemin /
  CrawlResult                bounded frontier           Android / iOS / HAR
                                  │
                                  ▼
                         ┌──────────────────┐
                         │ FreshnessEngine │
                         │ harvest / rank  │
                         │ rotate / refill │
                         └────────┬─────────┘
                                  ▼
                         RotationCrawler
                         proxy + TLS profile
```

### Adapter map

| Problem | First choice | Why |
| --- | --- | --- |
| ordinary static page | `curl_cffi` | fast, low-cost, TLS-aware |
| browser-rendered page | `crawl4ai` | JavaScript, Markdown, deep crawl |
| selector-heavy stealth fetch | `scrapling` | extraction and browser fallback |
| anti-detect Firefox | `camoufox` | browser identity control |
| patched Playwright | `patchright` | Playwright-compatible fallback |
| CDP-native browser | `nodriver` | browser automation surface |
| agent-driven interaction | `browser-use` | last-resort public workflow |
| many pages | `omk-crawl --deep` | bounded BFS and sitemap seeding |
| public proxy rotation | `--proxy-pool` | harvest, score, rotate, refill |
| files and captures | `markitdown`, `har` | local conversion and inspection |

See the detailed router at [references/routing.md](references/routing.md) and adapter notes in [references/tools](references/tools).

## Evidence, not theatre

The repository ships offline tests and explicit verification boundaries:

- `715 passed, 11 deselected` in the current release test suite.
- synthetic/offline checks are not live WAF defeat evidence.
- the evasion self-check measures consistency of the local plan; it does not prove a commercial bot vendor will accept it.
- free-proxy health is measured per node; a harvested address is not automatically a working or authorized route.
- a `blocked`, `refused`, or `robots_denied` result remains a result.

### Is this number one?

It has a serious claim to being **top-tier in architecture**: adaptive escalation, bounded deep crawl, source freshness, feedback-driven proxy rotation, typed outcomes, local captures, mobile surfaces, and an OMK-native skill in one package.

A literal global number-one claim needs a frozen, independent benchmark across comparable crawlers, sites, languages, regions, latency budgets, and authorization models. This repository does not pretend that a local test count is that benchmark.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,proxy]'
.venv/bin/ruff check omk_crawl tests
.venv/bin/ruff format --check omk_crawl tests
.venv/bin/python -m pytest tests/ -q
python -m build
```

Useful references:

- [deep crawl](references/deep-crawl.md)
- [breakthrough and evasion](references/breakthrough.md)
- [proxy rotation](SKILL.md#프록시-풀-회전-크롤링-v216-무료-프록시---무한의-굴레)
- [verification engine](references/verification-engine.md)
- [security policy](SECURITY.md)
- [contributing](CONTRIBUTING.md)

## Responsible use

- Crawl only content you are authorized to access.
- Respect robots.txt, terms of service, rate limits, and applicable law.
- Do not bypass authentication, paywalls, CAPTCHA challenges, or access controls without explicit authorization.
- Treat HTML, Markdown, HAR data, proxy responses, and browser content as untrusted input.
- Do not place credentials or personal data on free proxies.
- Use browser and session tooling only with an owned or consented session.
- Report a stop reason instead of manufacturing a successful result.

## License

Apache-2.0. See [LICENSE.txt](LICENSE.txt), [NOTICE.md](NOTICE.md), and [SECURITY.md](SECURITY.md).
