"""One-time GitHub star nudge shown after a successful interactive run.

Rules this module never breaks:

* stdout is sacred — every byte goes to **stderr**, so `omk-crawl url > out.md`
  and `omk-crawl url --json | jq` stay clean.
* Only ever speaks on a real TTY (stdin *and* stderr), never in CI, never under
  pytest, never when `OMK_CRAWL_NO_STAR=1` or `DO_NOT_TRACK=1` is set.
* Asks at most once. `[n]` means never again.
* No disk writes at all unless the session is interactive.
* Never raises — a broken nudge must not fail a crawl.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any, Final

REPO: Final[str] = "dmae97/omk-crawling"
REPO_URL: Final[str] = f"https://github.com/{REPO}"

PROMPT_AFTER_RUNS: Final[int] = 3
_TIMEOUT_S: Final[int] = 15
_CI_ENV_VARS: Final[tuple[str, ...]] = (
    "CI",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "BUILDKITE",
    "CIRCLECI",
    "JENKINS_URL",
    "TF_BUILD",
    "PYTEST_CURRENT_TEST",
)


def state_path() -> Path:
    """Where the run counter / opt-out flag lives (XDG on POSIX, LOCALAPPDATA on Windows)."""
    override = os.environ.get("OMK_CRAWL_STATE_DIR")
    if override:
        return Path(override) / "star.json"
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        root = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(root) / "omk-crawl" / "star.json"


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict[str, Any]) -> None:
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass  # a nudge is never worth failing a crawl over


def is_disabled() -> bool:
    env = os.environ
    if env.get("OMK_CRAWL_NO_STAR") or env.get("DO_NOT_TRACK"):
        return True
    return any(env.get(var) for var in _CI_ENV_VARS)


def is_interactive() -> bool:
    try:
        return bool(sys.stdin.isatty() and sys.stderr.isatty())
    except (AttributeError, ValueError):
        return False


def should_prompt(state: dict[str, Any]) -> bool:
    """Pure decision: ask only once, and only after the tool has proven useful."""
    if state.get("starred") or state.get("declined") or state.get("prompted"):
        return False
    try:
        runs = int(state.get("runs", 0))
    except (TypeError, ValueError):
        return False
    return runs >= PROMPT_AFTER_RUNS


def _star_via_gh() -> bool:
    """Star through an authenticated `gh` CLI — the actual one-keypress button."""
    gh = shutil.which("gh")
    if not gh:
        return False
    try:
        proc = subprocess.run(
            [gh, "api", "--silent", "-X", "PUT", f"user/starred/{REPO}"],
            capture_output=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _open_browser() -> bool:
    try:
        return bool(webbrowser.open(REPO_URL))
    except (webbrowser.Error, OSError):
        return False


def _say(text: str) -> None:
    try:
        print(text, file=sys.stderr)
    except (OSError, UnicodeError):
        pass


def star_now(*, allow_browser: bool = True) -> str:
    """Star the repo. Returns 'gh', 'browser', or 'manual'."""
    if _star_via_gh():
        _say(f"  ⭐ Starred {REPO} — thank you!")
        return "gh"
    if allow_browser and _open_browser():
        _say(f"  → Opened {REPO_URL} — hit the Star button up top 🙏")
        return "browser"
    _say(f"  → Star here, it really helps: {REPO_URL}")
    return "manual"


def _ask() -> str:
    _say("")
    _say(f"  ⭐ Enjoying omk-crawl? A star on {REPO} keeps it alive.")
    _say("     [enter] star it   [b] open in browser   [n] never ask again")
    try:
        return input("     > ").strip().lower()
    except (EOFError, KeyboardInterrupt, OSError):
        return "skip"


def prompt(state: dict[str, Any]) -> dict[str, Any]:
    """Run the interactive nudge and return the updated state."""
    answer = _ask()
    state["prompted"] = True
    if answer in {"n", "no", "never"}:
        state["declined"] = True
        _say("  ok, never asking again (OMK_CRAWL_NO_STAR=1 also works).")
    elif answer == "skip":
        state["prompted"] = False  # interrupted — do not burn the single ask
    elif answer in {"", "y", "yes", "s", "star"}:
        state["starred"] = star_now(allow_browser=True) != "manual"
    elif answer in {"b", "browser", "open"}:
        _open_browser()
        _say(f"  → {REPO_URL}")
    else:
        _say(f"  later then — {REPO_URL}")
    return state


def after_run(*, success: bool = True) -> None:
    """Called by the CLI after a run. Silent no-op unless a human is watching."""
    try:
        if not success or is_disabled() or not is_interactive():
            return
        state = load_state()
        if state.get("starred") or state.get("declined"):
            return
        try:
            state["runs"] = int(state.get("runs", 0)) + 1
        except (TypeError, ValueError):
            state["runs"] = 1
        if should_prompt(state):
            state = prompt(state)
        save_state(state)
    except Exception:  # noqa: BLE001 - a nudge must never break the CLI
        return
