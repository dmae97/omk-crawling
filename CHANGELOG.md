# Changelog

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
