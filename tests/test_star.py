"""Tests for the GitHub star nudge (no network, no real TTY, no home-dir writes)."""

from __future__ import annotations

import json

import pytest

from omk_crawl import star


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("OMK_CRAWL_STATE_DIR", str(tmp_path))
    for var in ("OMK_CRAWL_NO_STAR", "DO_NOT_TRACK", *star._CI_ENV_VARS):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


class TestState:
    def test_state_path_honours_override(self, tmp_path):
        assert star.state_path() == tmp_path / "star.json"

    def test_load_missing_returns_empty(self):
        assert star.load_state() == {}

    def test_roundtrip(self, tmp_path):
        star.save_state({"runs": 2})
        assert star.load_state() == {"runs": 2}
        assert json.loads((tmp_path / "star.json").read_text())["runs"] == 2

    def test_corrupt_state_is_ignored(self, tmp_path):
        (tmp_path / "star.json").write_text("{not json")
        assert star.load_state() == {}

    def test_unwritable_state_dir_does_not_raise(self, monkeypatch, tmp_path):
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file, not a dir")
        monkeypatch.setenv("OMK_CRAWL_STATE_DIR", str(blocked / "sub"))
        star.save_state({"runs": 1})  # must not raise


class TestGating:
    @pytest.mark.parametrize("var", ["OMK_CRAWL_NO_STAR", "DO_NOT_TRACK", "CI", "GITHUB_ACTIONS"])
    def test_opt_out_and_ci(self, monkeypatch, var):
        monkeypatch.setenv(var, "1")
        assert star.is_disabled() is True

    def test_enabled_with_a_clean_environment(self, monkeypatch):
        monkeypatch.setattr(star.os, "environ", {})
        assert star.is_disabled() is False

    def test_disabled_under_pytest(self):
        assert star.is_disabled() is True  # PYTEST_CURRENT_TEST is always set here

    def test_not_interactive_without_tty(self):
        assert star.is_interactive() is False  # pytest capture is not a TTY

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ({}, False),
            ({"runs": star.PROMPT_AFTER_RUNS - 1}, False),
            ({"runs": star.PROMPT_AFTER_RUNS}, True),
            ({"runs": 99, "starred": True}, False),
            ({"runs": 99, "declined": True}, False),
            ({"runs": 99, "prompted": True}, False),
            ({"runs": "garbage"}, False),
        ],
    )
    def test_should_prompt(self, state, expected):
        assert star.should_prompt(state) is expected


class TestAfterRun:
    def test_no_disk_write_when_not_interactive(self, tmp_path):
        star.after_run(success=True)
        assert not (tmp_path / "star.json").exists()

    def test_no_disk_write_when_opted_out(self, monkeypatch, tmp_path):
        monkeypatch.setattr(star, "is_interactive", lambda: True)
        monkeypatch.setenv("OMK_CRAWL_NO_STAR", "1")
        star.after_run(success=True)
        assert not (tmp_path / "star.json").exists()

    def test_no_disk_write_on_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(star, "is_disabled", lambda: False)
        monkeypatch.setattr(star, "is_interactive", lambda: True)
        star.after_run(success=False)
        assert not (tmp_path / "star.json").exists()

    def test_counts_runs_without_prompting(self, monkeypatch):
        monkeypatch.setattr(star, "is_disabled", lambda: False)
        monkeypatch.setattr(star, "is_interactive", lambda: True)
        monkeypatch.setattr(star, "prompt", lambda state: pytest.fail("prompted too early"))
        star.after_run()
        star.after_run()
        assert star.load_state()["runs"] == 2

    def test_prompts_on_the_nth_run(self, monkeypatch):
        monkeypatch.setattr(star, "is_disabled", lambda: False)
        monkeypatch.setattr(star, "is_interactive", lambda: True)
        seen: list[dict] = []

        def _fake_prompt(state: dict) -> dict:
            seen.append(state)
            return {**state, "prompted": True}

        monkeypatch.setattr(star, "prompt", _fake_prompt)
        for _ in range(star.PROMPT_AFTER_RUNS):
            star.after_run()
        assert len(seen) == 1
        assert star.load_state()["prompted"] is True

    def test_declined_is_permanent(self, monkeypatch):
        monkeypatch.setattr(star, "is_disabled", lambda: False)
        monkeypatch.setattr(star, "is_interactive", lambda: True)
        monkeypatch.setattr(star, "prompt", lambda state: pytest.fail("asked after decline"))
        star.save_state({"runs": 99, "declined": True})
        star.after_run()
        assert star.load_state()["runs"] == 99

    def test_never_raises(self, monkeypatch):
        monkeypatch.setattr(star, "is_disabled", lambda: False)
        monkeypatch.setattr(star, "is_interactive", lambda: True)
        monkeypatch.setattr(star, "load_state", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        star.after_run()  # must swallow


class TestPromptAnswers:
    @pytest.fixture(autouse=True)
    def _no_side_effects(self, monkeypatch):
        monkeypatch.setattr(star, "_star_via_gh", lambda: False)
        monkeypatch.setattr(star, "_open_browser", lambda: False)

    def _answer(self, monkeypatch, value):
        monkeypatch.setattr(star, "_ask", lambda: value)
        return star.prompt({"runs": 3})

    def test_enter_stars(self, monkeypatch):
        monkeypatch.setattr(star, "star_now", lambda **_: "gh")
        state = self._answer(monkeypatch, "")
        assert state["starred"] is True and state["prompted"] is True

    def test_n_declines(self, monkeypatch):
        state = self._answer(monkeypatch, "n")
        assert state["declined"] is True

    def test_interrupt_does_not_burn_the_ask(self, monkeypatch):
        state = self._answer(monkeypatch, "skip")
        assert state["prompted"] is False
        assert "declined" not in state

    def test_manual_fallback_is_not_marked_starred(self, monkeypatch):
        state = self._answer(monkeypatch, "y")
        assert state["starred"] is False

    def test_browser_answer(self, monkeypatch):
        opened: list[bool] = []
        monkeypatch.setattr(star, "_open_browser", lambda: opened.append(True) or True)
        state = self._answer(monkeypatch, "b")
        assert opened == [True] and state["prompted"] is True


class TestStarNow:
    def test_prefers_gh_cli(self, monkeypatch):
        monkeypatch.setattr(star, "_star_via_gh", lambda: True)
        monkeypatch.setattr(star, "_open_browser", lambda: pytest.fail("browser used despite gh"))
        assert star.star_now() == "gh"

    def test_browser_fallback(self, monkeypatch):
        monkeypatch.setattr(star, "_star_via_gh", lambda: False)
        monkeypatch.setattr(star, "_open_browser", lambda: True)
        assert star.star_now() == "browser"

    def test_manual_fallback(self, monkeypatch, capsys):
        monkeypatch.setattr(star, "_star_via_gh", lambda: False)
        monkeypatch.setattr(star, "_open_browser", lambda: False)
        assert star.star_now() == "manual"
        assert star.REPO_URL in capsys.readouterr().err

    def test_gh_missing_is_false(self, monkeypatch):
        monkeypatch.setattr(star.shutil, "which", lambda _: None)
        assert star._star_via_gh() is False

    def test_gh_failure_is_false(self, monkeypatch):
        monkeypatch.setattr(star.shutil, "which", lambda _: "/usr/bin/gh")
        monkeypatch.setattr(
            star.subprocess, "run",
            lambda *a, **k: (_ for _ in ()).throw(OSError("no gh")),
        )
        assert star._star_via_gh() is False


class TestCliWiring:
    def test_star_flag(self, monkeypatch, tmp_path, capsys):
        from omk_crawl.cli import main

        monkeypatch.setattr(star, "_star_via_gh", lambda: True)
        main(["--star"])
        assert star.load_state()["starred"] is True
        assert star.REPO in capsys.readouterr().err

    def test_nudge_never_touches_stdout(self, monkeypatch, capsys):
        from omk_crawl.cli import main

        monkeypatch.setattr(star, "is_disabled", lambda: False)
        monkeypatch.setattr(star, "is_interactive", lambda: True)
        monkeypatch.setattr(star, "_ask", lambda: "n")
        star.save_state({"runs": star.PROMPT_AFTER_RUNS})
        main(["--tools"])
        captured = capsys.readouterr()
        assert "star" not in captured.out.lower()
        assert star.load_state()["declined"] is True
