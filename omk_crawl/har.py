"""Bounded, offline HAR inspection for browser and authorized app captures.

Defaults to endpoint metadata. No requests are replayed. Request headers, cookies,
post data, URL userinfo, query strings, and fragments are never exported. URL paths
and explicitly included response bodies may still contain sensitive data.
"""

from __future__ import annotations

import base64
import binascii
import html
import json
import math
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from omk_crawl.result import CrawlResult, CrawlStatus, _timer


def _finite_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError("invalid_number") from exc
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def _reject_constant(_text: str) -> None:
    raise ValueError("non-JSON number")


def _load_json(text: str) -> Any:
    try:
        return json.loads(text, parse_float=_finite_float, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise ValueError("invalid_json") from exc


def _endpoint_url(url: str) -> str:
    if len(url) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise ValueError("invalid_url")
    url.encode("utf-8")
    parsed = urlsplit(url)
    host = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not host or any(c.isspace() for c in host):
        raise ValueError("invalid_url")
    authority = f"[{host}]" if ":" in host else host
    if parsed.port is not None:
        authority += f":{parsed.port}"
    return urlunsplit((parsed.scheme, authority, parsed.path, "", ""))


def _body(content: dict[str, Any], max_bytes: int) -> tuple[str, Any]:
    text = content.get("text")
    if text is None:
        return "missing", None
    mime = content.get("mimeType", "").split(";", 1)[0].strip().lower()
    if mime != "application/json" and not mime.endswith("+json"):
        return "unsupported_mime", None
    encoding = content.get("encoding")
    if encoding not in (None, "", "base64"):
        return "unsupported_encoding", None
    if not isinstance(text, str):
        return "invalid_body", None
    try:
        if encoding == "base64":
            text = "".join(text.split())
            if len(text) > 4 * ((max_bytes + 2) // 3):
                return "too_large", None
            raw = base64.b64decode(text, validate=True)
        else:
            raw = text.encode("utf-8")
        if len(raw) > max_bytes:
            return "too_large", None
        value = _load_json(raw.decode("utf-8-sig"))
        # Reject unpaired Unicode surrogates before CLI UTF-8 output.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError, binascii.Error, RecursionError):
        return "invalid_body", None
    return "included", value


def _record(entry: Any, index: int, include_bodies: bool, max_body_bytes: int) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("invalid_entry")
    request, response = entry.get("request"), entry.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise ValueError("invalid_entry")
    url, method, status = request.get("url"), request.get("method"), response.get("status")
    if not isinstance(url, str) or not isinstance(method, str):
        raise ValueError("invalid_request")
    if not method or len(method) > 32 or not method.isascii() or not method.isalpha():
        raise ValueError("invalid_method")
    if type(status) is not int or not 0 <= status <= 599:
        raise ValueError("invalid_status")
    content = response.get("content", {})
    if not isinstance(content, dict):
        raise ValueError("invalid_content")
    mime = content.get("mimeType", "")
    if not isinstance(mime, str) or len(mime) > 256:
        raise ValueError("invalid_mime")
    mime.encode("utf-8")
    elapsed = entry.get("time")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
        elapsed = None
    elif not 0 <= elapsed <= sys.float_info.max:
        elapsed = None
    record = {
        "index": index,
        "method": method.upper(),
        "url": _endpoint_url(url),
        "status": status,
        "mime_type": mime,
        "time_ms": elapsed,
        "body_state": "omitted",
    }
    if include_bodies:
        state, body = _body(content, max_body_bytes)
        record["body_state"] = state
        if state == "included":
            record["body"] = body
    return record


def analyze_har(
    path: str | Path,
    *,
    include_bodies: bool = False,
    max_bytes: int = 32 * 1024 * 1024,
    max_entries: int = 10_000,
    max_body_bytes: int = 2 * 1024 * 1024,
) -> CrawlResult:
    """Inspect a local HAR, optionally including bounded JSON response bodies.

    Invalid entries are skipped with indexed diagnostics. Invalid archives and
    exceeded file/entry limits return ERROR, never silently truncated success.
    Body failures keep the endpoint and report an explicit ``body_state``.
    """
    _, stop = _timer()
    result = CrawlResult(url=str(path), status=CrawlStatus.ERROR, tool="har")
    for name, value in (
        ("max_bytes", max_bytes),
        ("max_entries", max_entries),
        ("max_body_bytes", max_body_bytes),
    ):
        if type(value) is not int or not 0 < value < sys.maxsize:
            result.error = f"{name} must be a positive bounded integer"
            return result
    if not isinstance(include_bodies, bool):
        result.error = "include_bodies must be a boolean"
        return result
    try:
        source = Path(path)
        if not source.is_file():
            result.error = "HAR input must be an existing local regular file"
            return result
        with source.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            result.error = "HAR file exceeds max_bytes"
            return result
        document = _load_json(raw.decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeError, RecursionError):
        result.error = "Cannot read HAR as UTF-8 JSON"
        return result
    finally:
        result.elapsed_ms = stop()

    log = document.get("log") if isinstance(document, dict) else None
    entries = log.get("entries") if isinstance(log, dict) else None
    if not isinstance(entries, list):
        result.error = "HAR requires a log.entries array"
        return result
    if len(entries) > max_entries:
        result.error = "HAR entry count exceeds max_entries"
        return result

    records: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        try:
            records.append(_record(entry, index, include_bodies, max_body_bytes))
        except (ValueError, UnicodeError, OverflowError):
            # Exception strings may contain malformed URLs or captured secrets.
            issues.append({"index": index, "error": "invalid_http_entry"})
    result.extracted = records
    result.metadata = {
        "source": "har",
        "offline": True,
        "entries_total": len(entries),
        "entries_skipped": len(issues),
        "partial": bool(issues),
        "issues": issues,
        "bodies_included": include_bodies,
    }
    result.status = CrawlStatus.OK if records or not entries else CrawlStatus.ERROR
    if not result.ok:
        result.error = "No valid HTTP(S) entries in HAR"
    rows = ["# HAR endpoints", "", "| Method | Status | URL |", "| --- | --- | --- |"]
    for record in records:
        url = html.escape(record["url"]).replace("|", "&#124;").replace("`", "&#96;")
        rows.append(f"| {record['method']} | {record['status']} | {url} |")
    rows.append(f"\n{len(records)} entries; {len(issues)} skipped. Response bodies omitted here.")
    result.markdown = "\n".join(rows)
    result.elapsed_ms = stop()
    return result
