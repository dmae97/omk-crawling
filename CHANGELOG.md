# Changelog

## [2.13.0] — 2026-09-12

### Added

- Session-based X search (`XSearchTool`, `x_search`) and guest-token X trends
  (`get_trends`, `trend_to_tweets`, `trending_with_content`). These reuse the caller's
  own warmed session cookies; no API keys or OAuth secrets are stored.
- Shared target routing for CLI, sync/async public APIs, and dry-run plans.
  Native URIs and local files bypass the web chain; service hosts use hostname matching.
- Per-crawl execution traces, capability filtering, adapter exception isolation,
  `max_fetches`, and cooperative `total_timeout` (default 120 seconds). The default
  adapter cap is now 8. Browser disabling includes optional internal rendering;
  LLM adapters require explicit opt-in.
- Offline HAR inspection through `analyze_har()`, `--tool har`, and `.har` CLI routing.
  Metadata-only defaults omit headers, cookies, request bodies, and URL credentials,
  query strings, and fragments. JSON response bodies require explicit opt-in.
  File, entry, and decoded-body limits bound processing; malformed entries have indexed diagnostics.
- Router HTTP recovery for 408/429/502/503/504, bounded backoff, and `Retry-After`
  seconds/date support. Explicit terminal reasons and per-crawl attempt counts.

### Fixed

- InsaneSearch shares its timeout across profiles and browser work, preserves HTTP
  status/headers, and stops on authentication or server backpressure. SDK absence is
  reported instead of escaping as an import error; browser close runs in `finally`.
- Invalid HTTP SDK impersonation profiles are rejected before creating a session.
  CI explicitly installs the existing `curl` extra required by transport tests.
- Async rate-limit waits no longer block the event loop or hold a global lock while
  sleeping. Robots checks run off-loop, and async convenience arguments configure the router.
- Auth/rate-limit/unsafe-method stops return the terminal response rather than a
  larger earlier failure. Pending retries remain cancellable; unsafe methods are not retried.
- CLI version output uses the package's `__version__` instead of a stale literal.

## [2.12.1] — 2026-08-25

### Changed — dependency bump

- **browser-use 0.13.6 → 0.13.8** (SKILL.md pin, NOTICE.md, check-versions.sh,
  `references/tools/browser-use.md`). pyproject range `>=0.13,<1` unchanged.
  Upstream: 0.13.7 ships CLI 3.x fixes (file:// URL handling, React controlled-input
  clearing, extraction no longer auto-inherits output_model_schema per page);
  0.13.8 recovers Anthropic tool arguments serialized as text, updates Cerebras model
  IDs, guards `last_action()` on empty action lists, `_inject_budget_warning` against
  ZeroDivisionError, returns Path from `validate_user_data_dir`, writes agent files as
  UTF-8. Adapter (`omk_crawl/tools/browser_use_tool.py`) is API-compatible — no code change.

## [2.12.0] — 2026-08-20

**Breakthrough layer** — grounded in the 2024–2026 arXiv finding that evasive bots are
caught by *cross-layer and over-time fingerprint inconsistency*, not by any single
attribute (arXiv:2406.07647, 2606.30119, 2602.09606, 2606.14525, 2502.01608).

### Added — breakthrough layer

- **`fingerprint.py`** — cross-layer fingerprint coherence engine. One `FingerprintProfile`
  pins TLS impersonation + User-Agent + Client Hints + Accept-Language + locale/timezone +
  viewport so every layer tells the same story. `coherence_issues()` audits 5 mismatch
  rules; `profile_for(url)` keeps the identity deterministic per site (over-time
  consistency); `match_impersonate()` maps an existing curl_cffi target to a coherent
  profile. 5 built-in profiles (chrome-win/mac/linux, firefox-win, safari-mac).
- **`behavior.py`** — `BehaviorClock`: seed-deterministic human timings (lognormal
  think/inter-request, content-scaled dwell) and interaction plans (Bezier mouse paths,
  chunked scroll plans). Same seed → same sequence (replayable, offline-testable).
- **`warmup.py`** — session warm-up & clearance reuse. `SessionWarmup.acquire()` drives a
  real browser (nodriver → patchright → camoufox) through the site entry like a human and
  harvests clearance cookies (`cf_clearance`, `datadome`, `_abck`, `aws-waf-token`, …);
  `WarmSession.curl_kwargs()` replays the session with the *same* TLS profile + headers +
  cookies; `warm_crawl(url)` = cache → acquire → replay → invalidate → router fallback.
  Persisted at `~/.cache/omk-crawl/warm-sessions.json` (atomic, 0600, TTL).

### Added — anti-detect browser adapters (escalation chain ④–⑥)

- **`camoufox`** — anti-detect Firefox with C++-level fingerprint injection, `humanize`,
  `geoip` proxy pinning. Sync + async adapters.
- **`nodriver`** — CDP-native Chrome (undetected-chromedriver successor). No WebDriver /
  `navigator.webdriver` surface — the strongest option against DataDome/Kasada/PerimeterX.
  Sync fetch refuses a running event loop with an explicit error (fail-closed).
- **`patchright`** — patched, undetected Playwright drop-in. Browser context is built from
  the site's coherent fingerprint profile.
- Escalation chain is now 8-stage: `insane_search → curl_cffi → crawl4ai → scrapling →
  camoufox → patchright → nodriver → browser_use`.

### Added — detection & routing

- New `BlockType`s: **KASADA** (kpsdk/x-kpsdk), **PERIMETERX/HUMAN** (px-captcha/_pxhd),
  **AWS_WAF** (aws-waf-token). Per-vendor route tables: DataDome/Kasada/PerimeterX
  front-load `nodriver`; Cloudflare/Akamai/Imperva front-load `insane_search`/`camoufox`.
  `AUTH_REQUIRED` remains top priority with an empty route (never bypass, P3).

### Security

- Removed a hardcoded Firebase API key from `proxy_engine_v2.py` — proxy validation now
  reads `OMK_PROXY_VALIDATE_KEY` from the environment and fails closed at `validate_batch`
  entry when unset. **If you used that key, rotate it** (it lived in plaintext on disk).
- Quarantined scanner-triggering gitignored artifacts (third-party APK SDK tokens, local
  session state, public page captures) out of the repo tree to
  `~/.cache/omk-crawling-quarantine/` (restore steps in `QUARANTINE.md`).
- `.gitleaksignore` now documents justified suppressions.
- `aiohttp`/`aiohttp_socks` are lazy imports in `proxy_engine_v2`/`freshness_engine` —
  the core stays zero-dep (P4).

### Fixed

- Pre-existing ruff/type errors in `backtrack_guard`, `stealth_decision`,
  `proxy_engine_v2`, `freshness_engine`, `curl_cffi_tool` (ProxySpec/headers typing).

### Internal

- `tests/test_breakthrough.py` — 47 new offline-deterministic tests (fingerprint coherence,
  behavior determinism, warm sessions, new WAF detection, routing tables, adapter contract).
- spec-kit docs: `.specify/memory/constitution.md` + `specs/001-smart-escalation-core`
  (as-built) + `specs/002-unblockable-breakthrough` (this release).
- Suite: 251 passed, 2 deselected (live) · `ruff` 0 errors · `gitleaks dir .` 0 findings.

## [2.11.0] — 2026-07-27

### Added — one-keypress GitHub star button

- After the 3rd successful interactive run, `omk-crawl` asks once whether you want to
  star the repo. `[enter]` stars it in place through an authenticated `gh` CLI,
  `[b]` opens the browser, `[n]` never asks again.
- `omk-crawl --star` — star on demand, no waiting.
- Never touches stdout (`> out.md` / `--json | jq` stay byte-clean), never speaks on a
  non-TTY, never in CI or under pytest, never writes state unless the session is interactive.
- Opt out permanently: `OMK_CRAWL_NO_STAR=1` or the vendor-neutral `DO_NOT_TRACK=1`.
- State: `$XDG_STATE_HOME/omk-crawl/star.json` (`%LOCALAPPDATA%` on Windows).
- 27 tests in `tests/test_star.py`, plus a real-PTY verification of the prompt path.

## [2.10.0] — 2026-07-26

Ship train since 2.6.0: **mobile layer**, **Baemin live shops**, **iOS App Store meta**, **Reddit JSON bypass**.

### Added — Reddit target client

- **`RedditClient`**: old.reddit session warmup → `.json` listings
- Bypasses www "Please wait for verification" challenge (no browser JS)
- Methods: `subreddit`, `search`, `user`, `comments`
- CLI: `reddit://r/programming`, `reddit://search?q=…`, plain reddit.com URLs
- Tool registry: `reddit`
- Docs: `references/tools/reddit.md`
- Live verified: r/programming, r/korea JSON 200

## [2.9.0] — 2026-07-26

### Added — Mobile multi-APK crawl + iOS App Store surface

- **APK analyzer v2**: full multi-dex string scan, binary AXML string-pool package/perm hints,
  real DNS `hosts[]`, ranked API URLs (Baemin `com.sampleapp` → 400 URLs / 140+ hosts)
- **IPA analyzer v2**: frameworks, extensions, ATS exception domains, query schemes, host list
- **`AppStoreClient`** — public iTunes lookup/search (no IPA / DRM bypass)
- **CLI**: `appstore://search?q=…`, `appstore://TRACK_ID`, `ios://Freeform`
- Tool registry: `appstore` / `ios`
- Artifacts: `re_artifacts/mobile_crawl_20260726/` (5 APKs + demo IPA + 39 KR App Store apps)

## [2.8.0] — 2026-07-26

### Added — Baemin live shop list (food-shop-list)

- **`BaeminClient.list_shops` / `collect_shops`** — geo shop cards without app login
  - Host: `food-shop-list.baemin.com`
  - Group: `FOOD_CATEGORY` / category `FOOD_CATEGORY_ALL`
  - Headers: `X-BAEMIN-LATITUDE`, `X-BAEMIN-LONGITUDE`, `X-BAEMIN-DEVICE-ID`
- **`BaeminShop`**, `normalize_shop`, `rank_shops`, `shops_to_markdown`
- **CLI / adapter**: `baemin://lat,lng`, `baemin://shops?lat=&lng=&limit=`
- Tool registry: `baemin` (needs `curl_cffi`)
- Docs: `references/tools/baemin.md`
- Tests: `tests/test_baemin.py`

## [2.7.0] — 2026-07-26

### Added — mobile / native layer (Android + iOS)

- **`omk_crawl.mobile`** package:
  - `analyze_apk()` — zip/aapt/androguard static surface (URLs, perms, dex, SSL-pin hints)
  - `analyze_ipa()` — Info.plist + URL schemes + ATS + string URLs
  - `list_adb_devices()` / `adb_shell()` — host ADB bridge
- **Adapters** registered in `ALL_TOOLS`:
  - `apk` — `*.apk` / `apk://`
  - `ipa` — `*.ipa` / `ipa://`
  - `scrcpy` / `android` — `android://` device list, packages, dumpsys, screenshot
- **CLI auto-route** by suffix/scheme (not in web escalation chain)
- Extra: `pip install omk-crawl[mobile]` → optional `androguard`
- Docs: `references/tools/mobile.md`
- Tests: `tests/test_mobile.py`

## [2.6.0] — 2026-07-24

### Added — insane-search adapter (first-line breaker)

- **`InsaneSearchTool`** — hyper-aggressive single-URL unblocker:
  - 8 TLS impersonation profiles (Chrome 124/120/116/110, Safari 17/15,
    Edge 101, Firefox 133) rotated per attempt
  - Real browser header spoofing (Sec-CH-UA, Sec-Fetch-*, Accept, Referer)
  - Playwright stealth browser last-resort with anti-webdriver injection
  - Block-page detection in 2xx responses (captcha/WAF markers)
- Placed first in ``ESCALATION_CHAIN`` (position ⓪)
- Registered as ``insane_search`` in ``ALL_TOOLS`` (7 total)

### Enhanced — WAF/CDN detection

- **Akamai**: Bot Manager / mPulse markers (``_abck``, ``bm_sz``, ``ak-bmsc``)
- **DataDome**: CAPTCHA markers (``datadome-client``, ``dd-bypass``)
- **Imperva/Incapsula**: (``visid_incap``, ``incap_ses``, ``reese84``)
- Expanded Cloudflare markers (``cf-ray``, ``__cf_bm``, ``cf_clearance``)
- Expanded generic WAF markers (``ddos-guard``, ``perimeterx``, security checks)

### Enhanced — routing table

- All 7 block types have preferred tool chains
- Akamai/DataDome/Imperva route to ``insane_search`` → ``scrapling`` → ``crawl4ai``
- ``DEFAULT_ORDER`` now includes ``insane_search`` as first rung

## [2.5.0] — 2026-07-24

Quality release driven by external code review — shifts weight from docs/demo
to routing quality, adapter contract, and a real benchmark (the three levers
the review identified for moving 78 → 90).

### Added — detection-aware routing (Phase 2)

- **`routing.py`** — `ROUTE_TABLE` maps each `BlockType` to a preferred tool
  order (TLS→curl_cffi, JS→crawl4ai, CF/WAF→scrapling). `preferred_order()`
  reorders the remaining chain after a detected block; `reorder_tools()` is
  stable for duplicate names. AUTH_REQUIRED returns `[]` — we never escalate
  into an auth bypass.
- `SmartRouter.crawl()` / `crawl_async()` now reroute mid-escalation based on
  the live detection (once, above a 0.5 confidence threshold), skip auth
  blocks, and record `rerouted_to` in result metadata.
- `diagnose()` now emits the routing table so callers see *why* a block type
  would reorder the chain.
- 17 routing tests (table, permutation, stability, router reroute integration).

### Added — unified adapter contract (Phase 1)

- `BaseTool.capabilities` (frozenset) + `COMMON_KWARGS` (timeout/proxy/headers/
  cookies/session). `supports()`, `unsupported_features()`, `contract_metadata()`
  report requested-but-unsupported features explicitly instead of silent no-ops.
- curl_cffi: proxy normalization + cookies; crawl4ai: headers; scrapling: proxy.
  All four core adapters declare capabilities.
- 15 contract tests.

### Added — browser-use cost guards (Phase 5)

- `max_steps`, `max_cost_usd`, `deadline_s` caps; excluded from the chain when
  no LLM key is configured; failure taxonomy (nav/login/timeout/model/unknown);
  `dry_run` mode reports guardrails without spending.

### Added — benchmark harness (Phase 3)

- `benchmarks/sites.yaml` — 20 sites across static / JS / soft-wall / hard-wall.
- `scripts/bench.py` — mock (CI) + live modes; success@1/final, p50/p95 latency,
  bytes, tool path, cost proxy → `benchmarks/latest.json` + Markdown table.
- README benchmark table from a polite live run (7/7 success@1).

### Changed — consistency (Phase 0)

- Tool-count framing unified: 6 runtime adapters (4 core escalation + 2 aux),
  skill catalog references 10. Version aligned across `pyproject`/`__init__`/tag.
- GitHub topics added (web-scraping, crawler, anti-bot, markdown, python, …).

## [2.4.0] — 2026-07-24

Large capability + stability release. All new modules keep the zero-dependency
core: heavy deps (curl_cffi, beautifulsoup4, playwright, charset-normalizer) are
imported lazily inside functions, so `import omk_crawl` still works with no
extras installed. Install `omk-crawl[targets]` for the Naver/Baemin clients.

### Added

- **`resilience.py`** — `TokenBucket` (sync + `acquire_async`), `RetryPolicy`/
  `retry` with exponential backoff, `ResponseCache`, `HeaderStore`,
  `ImpersonateRotator` (8 TLS fingerprints, auto-excludes failed profiles),
  `EndpointChain` fallback, `ensure_playwright()` auto-installer.
- **`stability.py`** — `CircuitBreaker` (per-host, closed/open/half-open),
  `BreakerRegistry`, `SessionManager` (connection reuse + cookies),
  `TimeoutBudget`, structured `get_logger`.
- **`adaptive.py`** — `AdaptiveFetcher` with a 3-tier strategy ladder:
  DIRECT (curl_cffi TLS impersonation) → SESSION → RENDER (Playwright with
  automatic XHR/fetch interception). Discovers a site's real APIs without
  prior knowledge. `CapturedCall.decoded_text()` auto-detects legacy Korean
  encodings (EUC-KR/CP949) via Hangul-scored candidate selection.
- **`async_batch.py`** — `AsyncBatchFetcher`: concurrent crawling bounded by a
  shared token bucket + per-host circuit breakers (~5× throughput on multi-page
  fetches).
- **`baemin.py`** — `BaeminClient` built on an APK-verified endpoint registry
  (DEX string extraction of `com.sampleapp` v16.15.0), replacing guessed URLs.
- **`naver.py`** — `NaverLandClient` (public real-estate markers) and a fully
  rewritten `NaverCafeClient`: no-login REST article list / notices / popular /
  menus / multi-page crawl, plus full article body via browser-decoded frame
  text (sidesteps the legacy EUC-KR `yortapaper` iframe). Session methods
  `check_login()` / `can_access()` validate YOUR OWN session for content you
  are authorized to see.
- **`cookies.py`** — `CookieManager`: import your own browser session from
  JSON (EditThisCookie/Cookie-Editor), Netscape cookies.txt, or a `Cookie:`
  header; filters expired cookies; injects into curl_cffi + Playwright.
- **`examples/`** — `baemin_reviews.py`, `baemin_mitm_capture.py`,
  `naver_cafe_public.py`, `naver_land_public.py`,
  `naver_private_cafe_own_session.py`.
- **`scripts/verify_endpoints.py`** — 17-check endpoint + component suite.

### Changed

- `__init__.py` exports expanded to 38 symbols; `__version__` → 2.4.0.
- `pyproject.toml`: new `targets` optional-extra; `all` updated; description
  refreshed.

### Legal scope

This release does **not** bypass, forge, or defeat authentication. Accessing
private/login-gated content is supported only via the user's own legitimate
session (`CookieManager`), i.e. automating access the account is already
authorized for. Unauthorized access is out of scope (정보통신망법 §48).

## [2.0.0] — 2026-07-24

### Added

- **scrapling** as 10th tool with dedicated `references/tools/scrapling.md`
- **insane-search** as 11th referenced tool (OMK internal sibling)
- Full shoutout section in NOTICE.md for all 11 upstream projects
- `scripts/check-versions.sh` — upstream version drift checker
- `CHANGELOG.md` — this file
- Repo-level `README.md` with quick-start and tool router table
- `.gitignore` for Python/Node artifacts

### Changed

- SKILL.md frontmatter description compressed (~800 → ~400 chars) for faster skill routing
- SKILL.md metadata.tools now includes scrapling + insane-search (10 entries)
- NOTICE.md restructured: license summary table, per-project shoutout with descriptions
- routing.md updated with scrapling reference link (was "형제 스킬" text-only)
- All references synced from v1 skill at `~/.omk/agent/skills/omk-crawling/`

### License verification

All 11 upstream licenses verified via GitHub API on 2026-07-24:

- Apache-2.0: crawl4ai, crawlee, scrcpy
- MIT: browser-use, curl-impersonate, curl_cffi, autoscraper, markitdown
- BSD-3-Clause: scrapy, scrapling
- OMK internal: insane-search

## [1.0.0] — 2026-07-23

### Added

- Initial skill: 8 tools (crawl4ai, scrapy, crawlee, browser-use, curl-impersonate, autoscraper, markitdown, scrcpy)
- 6-layer routing architecture
- 7 runnable examples
- 13 reference documents
- NOTICE.md with license table
- LICENSE.txt (Apache-2.0 + crawl4ai attribution addendum)
