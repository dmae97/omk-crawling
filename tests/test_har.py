"""Offline HAR analysis through the public API, registry, and real CLI."""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
from copy import deepcopy

import pytest

import omk_crawl


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("HAR analysis must not contact captured URLs")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def entry():
    return {
        "request": {
            "url": "https://user:private-pass@api.example.test/items?token=private-query#private-fragment",
            "method": "GET",
            "headers": [{"name": "Authorization", "value": "Bearer private-header"}],
            "cookies": [{"name": "session", "value": "private-cookie"}],
            "postData": {"text": "private-request"},
        },
        "response": {
            "status": 200,
            "headers": [{"name": "Set-Cookie", "value": "private-response-cookie"}],
            "content": {"mimeType": "application/json", "text": '{"items": ["private-body"]}'},
        },
        "time": 12.5,
    }


def save_har(tmp_path, entries):
    path = tmp_path / "capture.har"
    path.write_text(json.dumps({"log": {"version": "1.2", "entries": entries}}), encoding="utf-8")
    return path


def test_metadata_only_default_omits_credentials_and_bodies(tmp_path, entry):
    result = omk_crawl.analyze_har(save_har(tmp_path, [entry]))
    assert result.ok
    assert result.tool == "har"
    assert result.extracted is not None
    assert result.markdown is not None
    assert len(result.extracted) == 1
    record = result.extracted[0]
    assert record["url"] == "https://api.example.test/items"
    assert record["method"] == "GET"
    assert record["status"] == 200
    assert record["time_ms"] == 12.5
    assert record["body_state"] == "omitted"
    assert "private-" not in json.dumps(result.extracted)
    assert "private-" not in result.markdown
    assert result.headers == {}


@pytest.mark.parametrize("encoding", [None, "base64"])
@pytest.mark.parametrize("mime", ["application/json; charset=utf-8", "application/problem+json"])
def test_opt_in_json_bodies_decode_utf8_and_base64(tmp_path, entry, encoding, mime):
    body = json.dumps({"items": [{"name": "공개 상품", "price": 42}]}, ensure_ascii=False)
    content = {"mimeType": mime, "text": body}
    if encoding:
        content["encoding"] = encoding
        content["text"] = base64.b64encode(body.encode()).decode()
    entry["response"]["content"] = content
    result = omk_crawl.analyze_har(save_har(tmp_path, [entry]), include_bodies=True)
    assert result.extracted is not None
    assert result.markdown is not None
    assert result.extracted[0]["body"] == json.loads(body)
    assert result.extracted[0]["body_state"] == "included"
    assert "공개 상품" not in result.markdown  # summaries never duplicate response bodies


@pytest.mark.parametrize(
    "content,state",
    [
        ({"mimeType": "application/json"}, "missing"),
        ({"mimeType": "image/png", "text": "data"}, "unsupported_mime"),
        (
            {"mimeType": "application/json", "encoding": "gzip", "text": "data"},
            "unsupported_encoding",
        ),
        ({"mimeType": "application/json", "encoding": "base64", "text": "%%%"}, "invalid_body"),
        ({"mimeType": "application/json", "text": "broken private-content"}, "invalid_body"),
        ({"mimeType": "application/json", "text": '{"value": NaN}'}, "invalid_body"),
        ({"mimeType": "application/json", "text": '{"value": 1e9999}'}, "invalid_body"),
        ({"mimeType": "application/json", "text": '{"value": "\\ud800"}'}, "invalid_body"),
    ],
)
def test_body_failures_are_explicit_without_losing_endpoint(tmp_path, entry, content, state):
    entry["response"]["content"] = content
    result = omk_crawl.analyze_har(save_har(tmp_path, [entry]), include_bodies=True)
    assert result.ok
    assert result.extracted is not None
    assert result.extracted[0]["body_state"] == state
    assert "body" not in result.extracted[0]
    assert "private-content" not in json.dumps(result.extracted)


@pytest.mark.parametrize("encoded", [False, True])
def test_body_limit_checks_decoded_size(tmp_path, entry, encoded):
    body = json.dumps({"payload": "x" * 100})
    content = {"mimeType": "application/json", "text": body}
    if encoded:
        content.update(encoding="base64", text=base64.b64encode(body.encode()).decode())
    entry["response"]["content"] = content
    result = omk_crawl.analyze_har(
        save_har(tmp_path, [entry]), include_bodies=True, max_body_bytes=32
    )
    assert result.extracted is not None
    assert result.extracted[0]["body_state"] == "too_large"
    assert "body" not in result.extracted[0]


@pytest.mark.parametrize("bad", [None, {}, {"request": []}, {"request": {}, "response": []}])
def test_malformed_entries_report_partial_results(tmp_path, entry, bad):
    result = omk_crawl.analyze_har(save_har(tmp_path, [bad, entry]))
    assert result.ok
    assert result.metadata["entries_total"] == 2
    assert result.metadata["entries_skipped"] == 1
    assert result.metadata["partial"]
    assert result.metadata["issues"][0]["index"] == 0
    assert result.extracted is not None
    assert result.extracted[0]["index"] == 1


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "javascript:alert(1)", "https://[bad", "https://x:bad/"]
)
def test_invalid_or_non_http_urls_are_not_exported(tmp_path, entry, url):
    entry["request"]["url"] = url
    result = omk_crawl.analyze_har(save_har(tmp_path, [entry]))
    assert not result.ok
    assert result.extracted == []
    assert result.metadata["entries_skipped"] == 1


def test_ipv6_port_and_failed_request_survive(tmp_path, entry):
    entry["request"]["url"] = "https://[::1]:8443/items?token=private-query"
    entry["response"]["status"] = 0
    entry["time"] = -1
    result = omk_crawl.analyze_har(save_har(tmp_path, [entry]))
    assert result.ok
    assert result.extracted is not None
    assert result.extracted[0]["url"] == "https://[::1]:8443/items"
    assert result.extracted[0]["status"] == 0
    assert result.extracted[0]["time_ms"] is None


@pytest.mark.parametrize(
    "raw", ["not JSON", "[]", "{}", '{"log": {"entries": {}}}', '{"log": null}']
)
def test_invalid_archive_returns_typed_error(tmp_path, raw):
    path = tmp_path / "broken.har"
    path.write_text(raw)
    result = omk_crawl.analyze_har(path)
    assert not result.ok
    assert result.error


def test_file_and_entry_limits_are_not_silent_truncation(tmp_path, entry):
    path = save_har(tmp_path, [entry, deepcopy(entry)])
    oversized = omk_crawl.analyze_har(path, max_bytes=8)
    too_many = omk_crawl.analyze_har(path, max_entries=1)
    assert not oversized.ok
    assert oversized.error is not None and "max_bytes" in oversized.error
    assert not too_many.ok
    assert too_many.error is not None and "max_entries" in too_many.error


def test_empty_archive_and_missing_file(tmp_path):
    result = omk_crawl.analyze_har(save_har(tmp_path, []))
    assert result.ok
    assert result.extracted == []
    assert not omk_crawl.analyze_har(tmp_path / "missing.har").ok


def test_registry_exposes_har_but_not_in_web_escalation(tmp_path, entry):
    from omk_crawl.detect import available_tools
    from omk_crawl.tools import ESCALATION_CHAIN, get_tool

    tool = get_tool("har")
    assert tool.available()
    assert tool.fetch(str(save_har(tmp_path, [entry]))).ok
    assert "har" in available_tools()
    assert all(candidate.name != "har" for candidate in ESCALATION_CHAIN)


@pytest.mark.parametrize("include_bodies", [False, True])
def test_real_cli_har_to_json(tmp_path, entry, include_bodies):
    path = save_har(tmp_path, [entry])
    output = tmp_path / "out.json"
    command = [sys.executable, "-m", "omk_crawl.cli", str(path), "--json", "-o", str(output)]
    if include_bodies:
        command.append("--har-bodies")
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "OMK_CRAWL_NO_STAR": "1", "CI": "1"},
    )
    assert completed.returncode == 0, completed.stderr
    data = json.loads(output.read_text())
    assert data["tool"] == "har"
    assert ("body" in data["extracted"][0]) == include_bodies
    assert "private-header" not in output.read_text()


def test_missing_har_cli_returns_nonzero_without_traceback(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-m", "omk_crawl.cli", str(tmp_path / "missing.har"), "--json"],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "OMK_CRAWL_NO_STAR": "1", "CI": "1"},
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["status"] == "error"
    assert "Traceback" not in completed.stderr


def test_unrepresentable_timing_preserves_endpoint(tmp_path, entry):
    entry["time"] = 10**400
    result = omk_crawl.analyze_har(save_har(tmp_path, [entry]))
    assert result.ok
    assert result.extracted is not None
    assert result.extracted[0]["time_ms"] is None
