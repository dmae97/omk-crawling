# Agent Reach Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute Agent Reach through `omk_crawl`'s actual adapter registry and return `CrawlResult` without automatic installation, login, or alternate-backend retries.

**Architecture:** Add one focused `AgentReachTool` that uses argument arrays and bounded temporary-file process output. Register it as a target-specific adapter, not a generic web escalation adapter, so existing crawlers and specialized Reddit handling remain unchanged.

**Tech Stack:** Python 3.10 stdlib, existing `BaseTool`/`CrawlResult`, pytest.

## Global Constraints

- No live platform calls during implementation tests.
- No new Python dependency.
- No `shell=True`, automatic installation, channel addition, update, login, or hidden alternate-backend retry.
- Preserve public content and raw result data as untrusted data, never instructions.
- Do not commit or deploy without separate approval.

---

### Task 1: AgentReachTool and bounded process execution

**Files:**
- Create: `omk_crawl/tools/agent_reach_tool.py`
- Create: `tests/test_agent_reach_tool.py`

**Interfaces:**
- Consumes: `omk_crawl.tools.base.BaseTool`, `omk_crawl.result.CrawlResult`, `CrawlStatus`.
- Produces: `AgentReachTool(name="agent_reach")`, `route_for_target(str) -> tuple[str, str, list[str]]`, `AgentReachTool.fetch(str, **kwargs) -> CrawlResult`.

- [ ] **Step 1: Write failing adapter tests**

Create tests that instantiate `AgentReachTool(executable=fake_path)` and cover:

```python
def test_success_result():
    result = AgentReachTool(executable=fake).fetch("https://github.com/org/repo/issues/1")
    assert result.ok
    assert result.tool == "agent_reach"
    assert result.metadata["channel"] == "github"
    assert result.metadata["action"] == "read"
    assert result.metadata["untrusted_content"] is True
    assert result.extracted[0]["title"] == "Example"

def test_reach_scheme_preserves_query_shell_characters():
    result = AgentReachTool(executable=fake).fetch("reach://github/search?q=a;b$(touch SHOULDER-NOT-RUN")
    assert fake.argv == ["github", "search", "a;b$(touch SHOULDER-NOT-RUN"]

def test_auth_and_rate_limit_do_not_retry():
    for code, stop in (("NEED_LOGIN", "auth_required"), ("RATE_LIMITED", "rate_limited"), ("PLATFORM_BLOCKED", "platform_blocked")):
        result = AgentReachTool(executable=fake_error(code)).fetch("reach://twitter/thread?url=https://x.com/u/status/1")
        assert not result.ok
        assert result.metadata["stop_reason"] == stop
        assert fake_error(code).call_count == 1

def test_missing_malformed_and_oversized_output():
    assert AgentReachTool(executable=missing).available() is False
    assert AgentReachTool(executable=malformed).fetch("reach://web/read?url=https://example.com").metadata["stop_reason"] == "hard_error"
    assert AgentReachTool(executable=oversized).fetch("reach://web/read?url=https://example.com").metadata["stop_reason"] == "output_too_large"
```

The fake executable writes its argv to `fake.argv` and prints a JSON object. Error fakes print `{"ok":false,"error":{"code":code,"hint":"stop"}}`; malformed prints `not-json`; oversized prints more than `MAX_OUTPUT_BYTES`.

- [ ] **Step 2: Run tests to verify RED**

Run: `pytest tests/test_agent_reach_tool.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'omk_crawl.tools.agent_reach_tool'`.

- [ ] **Step 3: Implement the adapter**

Implement only the required public surface:

```python
class AgentReachTool(BaseTool):
    name = "agent_reach"
    pip_package = ""
    layer = 1
    needs_browser = False
    capabilities = frozenset({"timeout"})

    def __init__(self, executable: str | None = None):
        self.executable = executable or os.environ.get("OMK_AGENT_REACH_BIN", "agent-reach")

    def available(self) -> bool:
        return shutil.which(self.executable) is not None
```

Use `subprocess.Popen(argv, stdout=TemporaryFile(), stderr=TemporaryFile())`; never interpolate a shell string. On timeout, kill and wait. Read at most `MAX_OUTPUT_BYTES + 1` from each temporary file and fail closed when exceeded. Keep stdout/stderr separate and preserve the return code.

Implement `route_for_target`:

- `reach://<channel>/<action>?q=...` → `[agent-reach, channel, action, query]`.
- HTTPS target → map host to `github`, `youtube`, `bilibili`, `v2ex`, `twitter`, `facebook`, `instagram`, `xiaohongshu`, `linkedin`, `boss`, `xueqiu`, or `web`; invoke `[agent-reach, "read", original_url]`.
- Allow only documented read actions: `read`, `search`, `subtitle`, `transcribe`, `thread`, `hot`, `user`, `jobs`, `posts`, `profile`, `tweets`, `comments`, `node`, `quote`, `hot_posts`, `hot_stocks`.
- Reject unknown channels/actions with `invalid_target`; never expose setup/update/login as fetch actions.

Success conversion:

- Require `ok is true` and `items` to be a list.
- Set `status=CrawlStatus.OK`, `status_code=200`, `tool=self.name`.
- Set `extracted=items`.
- Build Markdown from item `title`, `url`, `author`, `published_at`, and `text`; do not execute item content.
- Set metadata `channel`, `action`, `untrusted_content=True`, `stop_reason="success"`, and `raw_response` to the parsed object.

Error conversion:

- `NEED_LOGIN` → `auth_required`.
- `RATE_LIMITED` → `rate_limited`, preserving `retry_after_seconds`.
- `PLATFORM_BLOCKED` → `platform_blocked`.
- `NEED_SETUP` → `needs_setup`.
- `NOT_FOUND` → `not_found`.
- `BAD_INPUT` → `invalid_target`.
- `UPSTREAM_BROKEN` → `upstream_broken`.
- `TIMEOUT` → `deadline_exceeded`.
- `BUSY` → `busy`.
- Malformed JSON, non-object JSON, nonzero process exit, and output overflow → explicit errors without silently succeeding.
- Return one result per call; never call setup, add, update, login, or another backend internally.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `pytest tests/test_agent_reach_tool.py -q`
Expected: all adapter tests PASS and no file named `SHOULDER-NOT-RUN` is created.

---

### Task 2: Registry and target classification

**Files:**
- Modify: `omk_crawl/tools/__init__.py`
- Modify: `omk_crawl/targets.py`
- Create: `tests/test_agent_reach_targets.py`

**Interfaces:**
- Consumes: Task 1's `AgentReachTool`.
- Produces: `ALL_TOOLS["agent_reach"]`, `TARGET_TOOLS` including `agent_reach`, and `resolve_target("reach://github/search?q=x")` returning `Target(value, "agent_reach", False)`.

- [ ] **Step 1: Write failing registration and classification tests**

```python
def test_registered():
    assert isinstance(get_tool("agent_reach"), AgentReachTool)
    assert "agent_reach" in TARGET_TOOLS

@pytest.mark.parametrize("url,channel", [
    ("reach://github/search?q=x", "github"),
    ("https://github.com/o/r/issues/1", "github"),
    ("https://youtu.be/abc", "youtube"),
    ("https://www.bilibili.com/video/BV1", "bilibili"),
    ("https://v2ex.com/t/1", "v2ex"),
    ("https://x.com/u/status/1", "twitter"),
])
def test_routes(url, channel):
    target = resolve_target(url)
    assert target.tool == "agent_reach"
    assert target.is_web is False
    assert route_for_target(url)[0] == channel

def test_reddit_stays_on_existing_adapter():
    assert resolve_target("https://www.reddit.com/r/python/").tool == "reddit"
```

- [ ] **Step 2: Run tests to verify RED**

Run: `pytest tests/test_agent_reach_targets.py -q`
Expected: FAIL with `ValueError: Unknown tool: agent_reach` or missing host mapping.

- [ ] **Step 3: Register and map targets**

In `omk_crawl/tools/__init__.py`:

```python
from omk_crawl.tools.agent_reach_tool import AgentReachTool

ALL_TOOLS["agent_reach"] = AgentReachTool
TARGET_TOOLS = frozenset({"baemin", "reddit", "agent_reach"})
```

Do not add `AgentReachTool` to `ESCALATION_CHAIN`.

In `omk_crawl/targets.py`:

```python
_SCHEMES["reach"] = "agent_reach"
```

Map exact official hosts and subdomains for the channels in Task 1. Check specialized adapters first; in particular `reddit.com` must continue to resolve to `reddit`. Validate `parsed.port` before selecting an adapter as the existing function already does.

- [ ] **Step 4: Run focused and related tests**

Run: `pytest tests/test_agent_reach_targets.py tests/test_reddit.py tests/test_targets.py -q`
Expected: PASS with no change to existing Reddit behavior.

---

### Task 3: Documentation and full verification

**Files:**
- Modify: `references/agent-reach.md`
- Modify: `references/engine.md`
- Regenerate: `/home/dmae97/.omk/agent/skills-graph.json`

**Interfaces:**
- Documents the Task 1/2 public contract without implying live verification or automatic login.

- [ ] **Step 1: Document actual usage**

Add only verified behavior:

```text
reach://github/search?q=<query>
reach://youtube/subtitle?url=<video-url>
reach://bilibili/read?url=<video-url>
reach://v2ex/read?url=<topic-url>
reach://twitter/thread?url=<tweet-url>
```

Document `OMK_AGENT_REACH_BIN`, the output metadata fields, `retry_after_seconds`, the allowed action set, and that `agent_reach` is not in `ESCALATION_CHAIN`. Explain that live channels still require `agent-reach doctor --text` / `agent-reach check <channel> --text` and that those checks are not run by this change.

- [ ] **Step 2: Run the non-live suite and lint**

Run: `pytest -q`
Expected: all non-live tests PASS.

Run: `ruff check omk_crawl/tools/agent_reach_tool.py tests/test_agent_reach_tool.py tests/test_agent_reach_targets.py`
Expected: no findings.

- [ ] **Step 3: Rebuild and verify the skill graph**

Run from `/home/dmae97/.omk/agent`:

```bash
node skills/skill-graph/scripts/build-skill-graph.mjs --write
node skills/skill-graph/scripts/build-skill-graph.mjs --check
```

Expected: `OK: skill graph verified` with no new dangling alias or lane.

- [ ] **Step 4: Review the final diff**

Confirm that only the adapter, registry/target mapping, focused tests, docs, and graph changed. Do not commit, push, deploy, or run live checks without separate approval.
