# Contributing to omk-crawling

## Setup

```bash
git clone https://github.com/dmae97/omk-crawling.git
cd omk-crawling
pip install -e ".[all,dev]"
```

## Running tests

```bash
pytest tests/ -v
ruff check omk_crawl/ tests/
```

## Code style

- Python 3.10+ syntax, `from __future__ import annotations` in every module
- Type hints on all public APIs
- `ruff` with `select = ["E", "F", "I", "W", "UP"]`, line-length 100
- Docstrings on all public classes and functions
- Tests for every new feature and bug fix

## Adding a tool adapter

1. Create `omk_crawl/tools/<name>_tool.py` extending `BaseTool`
2. Set `name`, `pip_package`, `layer`, `needs_browser`, `needs_llm`
3. Implement `fetch()` — must not raise, return `CrawlResult` with error
4. Register in `omk_crawl/tools/__init__.py` (`ALL_TOOLS`, optionally `ESCALATION_CHAIN`)
5. Add the pip package to `pyproject.toml` extras
6. Add tests in `tests/`

## Pull requests

- One logical change per PR
- Include tests
- Keep commits atomic with clear messages
- CI must pass (lint + test + build)

## Shoutouts

This project went from 63/100 to 90+ through iterative code review.
Huge thanks to everyone who contributed feedback and tooling:

- **dmae97** — project author, all commits, driving the review-fix cycles
- **OMK (Open Multi-agent Kit)** — coding agent harness that coordinated the implementation
- **Claude (Anthropic)** — AI pair programmer for all 4 review-fix rounds
- **ruff** — linting that caught unused imports, import ordering, and style issues
- **pytest** — 71 tests keeping everything honest through every refactor
- **The code reviewers** — 4 rounds of brutally specific feedback (CF 503 escalation halt, silent verbose, markdown pollution, dead code, robots.txt wiring, thread safety, timeout honesty) that turned a working prototype into production-quality code
