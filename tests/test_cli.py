"""Tests for CLI argument parsing."""

from __future__ import annotations

import pytest

from omk_crawl.cli import main


class TestCLI:
    def test_tools_flag(self, capsys):
        main(["--tools"])
        out = capsys.readouterr().out
        assert "Installed tools" in out or "Missing tools" in out

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0

    def test_no_url_shows_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 1

    def test_diagnose(self, capsys):
        main(["--diagnose", "https://example.com"])
        out = capsys.readouterr().out
        assert "available_tools" in out
