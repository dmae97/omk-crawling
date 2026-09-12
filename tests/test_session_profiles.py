"""Validate dynamic profile strings against the installed HTTP SDK contract."""

from unittest.mock import MagicMock

import pytest

from omk_crawl.stability import SessionManager


def test_unknown_profile_is_rejected_before_creating_session(monkeypatch):
    requests = pytest.importorskip("curl_cffi.requests")
    factory = MagicMock()
    monkeypatch.setattr(requests, "Session", factory)
    with pytest.raises(ValueError, match="profile"):
        SessionManager(impersonate="not-a-profile").get()
    factory.assert_not_called()


def test_valid_profile_keeps_session_reuse(monkeypatch):
    requests = pytest.importorskip("curl_cffi.requests")
    factory = MagicMock()
    monkeypatch.setattr(requests, "Session", factory)
    manager = SessionManager(impersonate="chrome124")
    first = manager.get()
    assert manager.get() is first
    factory.assert_called_once_with(impersonate="chrome124")
