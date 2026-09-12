"""Bounded adapter behavior behind the first engine route; offline fixtures only."""

from __future__ import annotations

import builtins
import json
import sys
from importlib.machinery import ModuleSpec
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from omk_crawl.result import CrawlStatus
from omk_crawl.tools import get_tool


@pytest.fixture(autouse=True)
def offline_http_sdk(monkeypatch):
    module = ModuleType("curl_cffi")
    module.__spec__ = ModuleSpec("curl_cffi", loader=None)
    module.__dict__["requests"] = SimpleNamespace(get=MagicMock())
    monkeypatch.setitem(sys.modules, "curl_cffi", module)


@pytest.mark.parametrize("code", [401, 429, 404, 503])
def test_http_stops_preserve_status_and_retry_headers(monkeypatch, code):
    from curl_cffi import requests

    calls = []

    def fetch(*_args, **_kwargs):
        calls.append(1)
        return SimpleNamespace(
            status_code=code, text="Request not completed", headers={"Retry-After": "5"}
        )

    monkeypatch.setattr(requests, "get", fetch)
    result = get_tool("insane_search").fetch("https://fixture.example.test", stealth=False)
    assert len(calls) == 1
    assert result.status_code == code
    assert result.headers["Retry-After"] == "5"
    assert not result.ok


def test_missing_sdk_returns_tool_missing_not_exception(monkeypatch):
    original = builtins.__import__

    def imported(name, *args, **kwargs):
        if name == "curl_cffi":
            raise ModuleNotFoundError("curl_cffi")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", imported)
    result = get_tool("insane_search").fetch("https://fixture.example.test", stealth=False)
    assert result.status is CrawlStatus.TOOL_MISSING


def test_timeout_budget_is_not_reset_per_profile(monkeypatch):
    from curl_cffi import requests

    clock = [100.0]
    monkeypatch.setattr("omk_crawl.stability.time.monotonic", lambda: clock[0])
    calls = []

    def fetch(*_args, **kwargs):
        calls.append(kwargs["timeout"])
        clock[0] += 3
        raise TimeoutError("private proxy credentials")

    monkeypatch.setattr(requests, "get", fetch)
    result = get_tool("insane_search").fetch(
        "https://fixture.example.test", timeout=5, stealth=False
    )
    assert calls == [5, 2]
    assert not result.ok
    assert "private proxy credentials" not in json.dumps(result.metadata)
    assert result.metadata["adapter_stop"] == "deadline_exceeded"


def test_auth_wall_200_is_not_retried_as_a_challenge(monkeypatch):
    from curl_cffi import requests

    get = MagicMock(
        return_value=SimpleNamespace(
            status_code=200,
            text='<main><h1>Sign in</h1><form><input type="password">Login required</form></main>',
            headers={},
        )
    )
    monkeypatch.setattr(requests, "get", get)
    result = get_tool("insane_search").fetch("https://fixture.example.test", stealth=False)
    assert not result.ok
    assert get.call_count == 1
    assert result.metadata["block_type"] == "AUTH_REQUIRED"


def test_browser_failure_always_closes_browser(monkeypatch):
    from curl_cffi import requests

    monkeypatch.setattr(requests, "get", MagicMock(side_effect=ConnectionError("offline")))
    playwright = MagicMock()
    browser = playwright.__enter__.return_value.chromium.launch.return_value
    browser.new_context.return_value.new_page.return_value.goto.side_effect = RuntimeError(
        "private detail"
    )
    monkeypatch.setitem(
        sys.modules, "playwright.sync_api", SimpleNamespace(sync_playwright=lambda: playwright)
    )
    result = get_tool("insane_search").fetch("https://fixture.example.test", stealth=True)
    assert not result.ok
    browser.close.assert_called_once()
    assert "private detail" not in (result.error or "")


def test_browser_http_error_is_not_reported_as_200(monkeypatch):
    from curl_cffi import requests

    monkeypatch.setattr(requests, "get", MagicMock(side_effect=ConnectionError("offline")))
    playwright = MagicMock()
    browser = playwright.__enter__.return_value.chromium.launch.return_value
    page = browser.new_context.return_value.new_page.return_value
    page.goto.return_value.status = 401
    page.goto.return_value.all_headers.return_value = {"www-authenticate": "Basic"}
    page.content.return_value = "Login required"
    monkeypatch.setitem(
        sys.modules, "playwright.sync_api", SimpleNamespace(sync_playwright=lambda: playwright)
    )
    result = get_tool("insane_search").fetch("https://fixture.example.test", stealth=True)
    assert result.status_code == 401
    assert not result.ok
    browser.close.assert_called_once()


def test_availability_does_not_confuse_builtin_adapter_with_sdk(monkeypatch):
    from omk_crawl import detect

    monkeypatch.setattr(
        detect.importlib.util,
        "find_spec",
        lambda name: (SimpleNamespace() if name.startswith("omk_crawl") else None),
    )
    assert not detect.tool_available("insane_search")
    assert not get_tool("insane_search").available()


def test_expired_budget_never_launches_browser_after_sdk_import(monkeypatch):
    from curl_cffi import requests

    clock = [100.0]
    monkeypatch.setattr("omk_crawl.stability.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(requests, "get", MagicMock(side_effect=ConnectionError("offline")))
    playwright = MagicMock()
    original = builtins.__import__

    def imported(name, *args, **kwargs):
        if name == "playwright.sync_api":
            clock[0] += 6
            return SimpleNamespace(sync_playwright=lambda: playwright)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", imported)
    result = get_tool("insane_search").fetch("https://fixture.example.test", timeout=5)
    assert not result.ok
    playwright.__enter__.return_value.chromium.launch.assert_not_called()
    assert result.metadata["adapter_stop"] == "deadline_exceeded"
