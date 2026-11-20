# Agent Reach engine adapter design

Date: 2026-11-03
Status: Approved direction, pending implementation-plan review

## Goal

Integrate the installed Agent Reach 1.6 command into `omk_crawl`'s real router and `CrawlResult` contract instead of leaving it as documentation-only routing. Do not create a second orchestration platform or add dependencies.

## Components

1. `omk_crawl.agent_reach`
   - Discover `agent-reach` with `shutil.which`.
   - Execute argument arrays with `shell=False`, bounded wall time, and bounded captured output.
   - Parse one JSON response and preserve raw data without treating it as instructions.
   - Convert results to `CrawlResult` with tool, status, content, extracted items, and metadata.
2. Router registration and target recognition
   - Support explicit `reach://<channel>/<action>` targets.
   - Recognize official GitHub, YouTube, Bilibili, V2EX, Twitter, Reddit, and other documented channel domains.
   - Preserve existing specialized adapters and only use Agent Reach where it is eligible and available.
3. Failure mapping
   - Preserve Agent Reach `error.code`, `error.hint`, and `retry_after_seconds` in metadata.
   - Map authentication-required to `auth_required`, rate limiting to `rate_limited`, missing executable to `tool_missing`, malformed output to `hard_error`, and other failures to existing compatible stop reasons.
   - Never automatically install, update, add channels, or log in.
4. Documentation
   - Explain direct `reach://` use, domain mapping, output metadata, and limitations.
   - Regenerate and validate the skill graph.

## Safety and compatibility

- Public content first; no automatic installation or authentication.
- No shell command interpolation, credential output, or hidden fallback to another account/backend.
- Do not treat page shells, counts, or track listings as full text, comments, or subtitles.
- Preserve existing SmartRouter budgets, target filters, and specialized adapters.
- No live platform calls during implementation tests.

## Tests

Use a fake `agent-reach` executable and no network:

- Registration and target/domain mapping.
- Successful JSON to `CrawlResult` conversion.
- `NEED_LOGIN`, `RATE_LIMITED`, `PLATFORM_BLOCKED`, `NEED_SETUP`, `NOT_FOUND`, and `BAD_INPUT` mappings.
- Missing executable, malformed JSON, non-object JSON, excess output, timeout, and process failure.
- Argument-array integrity for URLs and queries containing shell metacharacters.
- No implicit installation, channel addition, update, login, or alternate-backend retry.
- Existing non-live tests remain green.

## Implementation scope

Minimum files: adapter module, router registration/detection changes, focused tests, and the Agent Reach reference. Avoid changing unrelated crawler subsystems. Do not commit, deploy, or perform live checks without separate scoped approval.
