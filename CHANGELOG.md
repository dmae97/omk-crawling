# Changelog

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
