"""Read-only Agent Reach adapter with bounded, shell-free process execution.

POSIX is required so every invocation has an owned process group that can be
killed and reaped safely; other platforms fail closed through ``available()``.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.tools.base import BaseTool

MAX_OUTPUT_BYTES = 1024 * 1024

# Real channel/action pairs from Agent Reach. The boolean records whether the
# upstream action supports its optional --limit argument.
_CHANNEL_ACTIONS: dict[str, dict[str, bool]] = {
    "github": {"search": True, "read": False},
    "twitter": {"search": True, "profile": False, "tweets": True, "thread": False},
    "youtube": {"read": False, "search": True, "subtitle": False, "transcribe": False},
    "facebook": {"search": True, "profile": False},
    "instagram": {"search": True, "profile": False, "posts": True},
    "bilibili": {"search": True, "read": False, "hot": True, "subtitle": False},
    "xiaohongshu": {"search": True, "read": False, "comments": True},
    "linkedin": {"jobs": True, "read": False},
    "boss": {"search": True, "read": False},
    "v2ex": {"hot": True, "node": True, "read": False, "user": False},
    "xueqiu": {"quote": False, "search": True, "hot_posts": True, "hot_stocks": True},
    "xiaoyuzhou": {"transcribe": False},
    "web": {"search": True, "read": False},
}
_NO_POSITIONAL = frozenset(
    {("bilibili", "hot"), ("v2ex", "hot"), ("xueqiu", "hot_posts"), ("xueqiu", "hot_stocks")}
)
_NORMALIZED_FIELDS = ("title", "url", "author", "published_at", "text")
_HOST_CHANNELS = {
    "github.com": "github",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "bilibili.com": "bilibili",
    "b23.tv": "bilibili",
    "v2ex.com": "v2ex",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "xiaohongshu.com": "xiaohongshu",
    "xhslink.com": "xiaohongshu",
    "linkedin.com": "linkedin",
    "zhipin.com": "boss",
    "xueqiu.com": "xueqiu",
    "xiaoyuzhoufm.com": "xiaoyuzhou",
}
_STOP_REASONS = {
    "NEED_LOGIN": "auth_required",
    "RATE_LIMITED": "rate_limited",
    "PLATFORM_BLOCKED": "platform_blocked",
    "NEED_SETUP": "needs_setup",
    "NOT_FOUND": "not_found",
    "BAD_INPUT": "invalid_target",
    "UPSTREAM_BROKEN": "upstream_broken",
    "TIMEOUT": "deadline_exceeded",
    "BUSY": "busy",
}


def _invalid_target() -> ValueError:
    return ValueError("invalid_target")


def _channel_for_host(hostname: str) -> str:
    hostname = hostname.lower().rstrip(".")
    for domain, channel in _HOST_CHANNELS.items():
        if hostname == domain or hostname.endswith(f".{domain}"):
            return channel
    return "web"


def _has_control_chars(value: str) -> bool:
    return any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)


def route_for_target(target: str) -> tuple[str, str, list[str]]:
    """Map a target to a real channel/action and its exact upstream argv."""
    try:
        if not isinstance(target, str) or _has_control_chars(target):
            raise _invalid_target()
        parsed = urlsplit(target)
        if parsed.scheme == "https":
            port = parsed.port
            if port is not None and not 1 <= port <= 65535:
                raise _invalid_target()
    except (TypeError, ValueError) as exc:
        raise _invalid_target() from exc

    if parsed.scheme == "reach":
        channel = parsed.netloc.lower()
        action = parsed.path.removeprefix("/")
        actions = _CHANNEL_ACTIONS.get(channel)
        if actions is None or action not in actions or "/" in action:
            raise _invalid_target()
        try:
            query_values = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=8)
        except ValueError as exc:
            raise _invalid_target() from exc

        no_positional = (channel, action) in _NO_POSITIONAL
        allowed_keys = {"limit"} if no_positional else {"q", "url", "limit"}
        if any(key not in allowed_keys for key in query_values):
            raise _invalid_target()
        args = [channel, action]
        if no_positional:
            if "q" in query_values or "url" in query_values:
                raise _invalid_target()
        else:
            keys = [key for key in ("q", "url") if key in query_values]
            if len(keys) != 1 or len(query_values[keys[0]]) != 1:
                raise _invalid_target()
            query = query_values[keys[0]][0]
            if _has_control_chars(query):
                raise _invalid_target()
            args.append(query)

        if "limit" in query_values:
            if not actions[action] or len(query_values["limit"]) != 1:
                raise _invalid_target()
            raw_limit = query_values["limit"][0]
            if _has_control_chars(raw_limit):
                raise _invalid_target()
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError) as exc:
                raise _invalid_target() from exc
            args.extend(["--limit", str(limit)])
        return channel, action, args

    if parsed.scheme == "https" and parsed.netloc and parsed.hostname:
        return _channel_for_host(parsed.hostname), "read", ["read", target]

    raise _invalid_target()


_SENSITIVE_NAMES = (
    "authorization",
    "cookies?",
    "x[-_]?api[-_]?key",
    "api[-_]?key",
    "(?:access|refresh)[-_]?token",
    "token",
    "password",
    "secret",
)
_SENSITIVE_NAME_PATTERN = "(?:" + "|".join(_SENSITIVE_NAMES) + ")"
_DIAGNOSTIC_FIELD_RE = re.compile(
    rf"(?i)(?P<prefix>[\"']?{_SENSITIVE_NAME_PATTERN}[\"']?\s*[:=]\s*[\"']?)"
    rf"(?:Bearer\s+)?[^\s,;&}}\"']+"
)
_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@:]+(?::[^\s/@]*)?@")
_URL_QUERY_SECRET_RE = re.compile(
    rf"(?i)([?&]{_SENSITIVE_NAME_PATTERN}=)([^&#\s]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_REDACTED = "[REDACTED]"


def _sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return normalized in {
        "authorization",
        "cookie",
        "setcookie",
        "xapikey",
        "apikey",
        "token",
        "password",
        "secret",
    } or normalized.endswith(("token", "password", "secret", "apikey", "cookie"))


def _redact_text(value: str) -> str:
    value = _DIAGNOSTIC_FIELD_RE.sub(lambda match: match.group("prefix") + _REDACTED, value)
    value = _URL_USERINFO_RE.sub(lambda match: match.group(1) + _REDACTED + "@", value)
    value = _URL_QUERY_SECRET_RE.sub(lambda match: match.group(1) + _REDACTED, value)
    return _BEARER_RE.sub("Bearer " + _REDACTED, value)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _REDACTED if _sensitive_key(key) else _redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _diagnostic_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _redact_text(value)
    try:
        rendered = json.dumps(_redact_value(value), ensure_ascii=False, default=str)
    except (TypeError, ValueError, RecursionError):
        rendered = str(value)
    return _redact_text(rendered)


def _public_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    include_raw = os.environ.get("OMK_AGENT_REACH_RAW") == "1"
    normalized = []
    for item in items:
        public = {key: item[key] for key in _NORMALIZED_FIELDS if key in item}
        if include_raw and "raw" in item:
            public["raw"] = item["raw"]
        normalized.append(public)
    return normalized


def _markdown(items: list[Any]) -> str:
    """Render item fields as inert Markdown without interpreting their content."""
    blocks: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        lines: list[str] = []
        if item.get("title") is not None:
            lines.append(f"## {item['title']}")
        if item.get("url") is not None:
            lines.append(str(item["url"]))
        details = [
            str(item[key])
            for key in ("author", "published_at")
            if item.get(key) is not None
        ]
        if details:
            lines.append(" · ".join(details))
        if item.get("text") is not None:
            lines.append(str(item["text"]))
        if lines:
            blocks.append("\n\n".join(lines))
    return "\n\n---\n\n".join(blocks)


def _kill_and_reap(process: subprocess.Popen[bytes]) -> int:
    """Kill the owned POSIX process group, or only the direct child elsewhere."""
    if os.name == "posix":
        try:
            # start_new_session=True makes the child PID its owned PGID.
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            process.kill()
    else:
        process.kill()
    return process.wait()


class AgentReachTool(BaseTool):
    name = "agent_reach"
    pip_package = ""
    layer = 1
    needs_browser = False
    capabilities = frozenset({"timeout"})

    def __init__(self, executable: str | None = None):
        self.executable = executable or os.environ.get("OMK_AGENT_REACH_BIN", "agent-reach")

    def available(self) -> bool:
        """Require POSIX process-group ownership; never pretend direct kill is tree-safe."""
        return os.name == "posix" and shutil.which(self.executable) is not None

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        start = time.monotonic()

        def finish(result: CrawlResult) -> CrawlResult:
            result.elapsed_ms = (time.monotonic() - start) * 1000
            return result

        try:
            channel, action, args = route_for_target(url)
        except ValueError:
            return finish(
                CrawlResult(
                    url=url,
                    status=CrawlStatus.ERROR,
                    tool=self.name,
                    error="invalid_target",
                    metadata={
                        "untrusted_content": True,
                        "stop_reason": "invalid_target",
                    },
                )
            )

        base_metadata: dict[str, Any] = {
            "channel": channel,
            "action": action,
            "untrusted_content": True,
        }
        if not self.available():
            result = self._missing(url)
            if os.name != "posix":
                result.error = (
                    "Agent Reach requires POSIX process group cleanup; "
                    "this platform is unsupported"
                )
            result.metadata = {**base_metadata, "stop_reason": "tool_missing"}
            return finish(result)

        try:
            timeout = float(kwargs.get("timeout", self.default_timeout))
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("timeout must be finite and positive")
        except (TypeError, ValueError, OverflowError):
            return finish(
                CrawlResult(
                    url=url,
                    status=CrawlStatus.ERROR,
                    tool=self.name,
                    error="invalid timeout",
                    metadata={**base_metadata, "stop_reason": "invalid_target"},
                )
            )

        timed_out = False
        overflowed = False
        try:
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                popen_kwargs: dict[str, Any] = {}
                if os.name == "posix":
                    popen_kwargs["start_new_session"] = True
                try:
                    process = subprocess.Popen(
                        [self.executable, *args],
                        stdin=subprocess.DEVNULL,
                        stdout=stdout_file,
                        stderr=stderr_file,
                        shell=False,
                        env={**os.environ, "REACH_API_SUPERVISED": "1"},
                        **popen_kwargs,
                    )
                except ValueError:
                    return finish(
                        CrawlResult(
                            url=url,
                            status=CrawlStatus.ERROR,
                            tool=self.name,
                            error="invalid_target",
                            metadata={**base_metadata, "stop_reason": "invalid_target"},
                        )
                    )
                except OSError as exc:
                    return finish(
                        CrawlResult(
                            url=url,
                            status=CrawlStatus.TOOL_MISSING,
                            tool=self.name,
                            error=f"Agent Reach executable failed to start: {exc}",
                            metadata={**base_metadata, "stop_reason": "tool_missing"},
                        )
                    )

                deadline = time.monotonic() + timeout
                try:
                    while True:
                        stdout_size = os.fstat(stdout_file.fileno()).st_size
                        stderr_size = os.fstat(stderr_file.fileno()).st_size
                        returncode = process.poll()
                        if max(stdout_size, stderr_size) > MAX_OUTPUT_BYTES:
                            overflowed = True
                            returncode = _kill_and_reap(process)
                            break
                        if returncode is not None:
                            break
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            timed_out = True
                            returncode = _kill_and_reap(process)
                            break
                        time.sleep(min(0.01, remaining))
                finally:
                    # Reap the whole owned group even when the direct child exited normally.
                    returncode = _kill_and_reap(process)

                stdout_file.seek(0)
                stderr_file.seek(0)
                stdout = stdout_file.read(MAX_OUTPUT_BYTES + 1)
                stderr = stderr_file.read(MAX_OUTPUT_BYTES + 1)
                if not overflowed and (
                    len(stdout) > MAX_OUTPUT_BYTES or len(stderr) > MAX_OUTPUT_BYTES
                ):
                    overflowed = True
                    returncode = _kill_and_reap(process)
        except OSError as exc:
            return finish(
                CrawlResult(
                    url=url,
                    status=CrawlStatus.ERROR,
                    tool=self.name,
                    error=f"Agent Reach process failed: {exc}",
                    metadata={**base_metadata, "stop_reason": "hard_error"},
                )
            )

        process_metadata: dict[str, Any] = {
            **base_metadata,
            "returncode": returncode,
        }
        if overflowed:
            return finish(
                CrawlResult(
                    url=url,
                    status=CrawlStatus.ERROR,
                    tool=self.name,
                    error="Agent Reach output exceeded MAX_OUTPUT_BYTES",
                    metadata={**process_metadata, "stop_reason": "output_too_large"},
                )
            )

        payload: dict[str, Any] | None = None
        try:
            decoded = json.loads(stdout)
            if isinstance(decoded, dict):
                payload = decoded
        except (TypeError, ValueError, RecursionError):
            pass
        if payload is not None and os.environ.get("OMK_AGENT_REACH_RAW") == "1":
            process_metadata["raw_response"] = payload

        if timed_out:
            return finish(
                CrawlResult(
                    url=url,
                    status=CrawlStatus.ERROR,
                    tool=self.name,
                    error="Agent Reach deadline exceeded",
                    metadata={**process_metadata, "stop_reason": "deadline_exceeded"},
                )
            )

        # The real CLI emits a valid ok:false envelope on stdout and exits 1.
        # Preserve that structured outcome instead of replacing it with a generic exit error.
        if payload is not None and payload.get("ok") is False:
            error = payload.get("error")
            error = error if isinstance(error, dict) else {}
            code = error.get("code")
            code_label = _redact_text(code) if isinstance(code, str) and code else "UNKNOWN_ERROR"
            diagnostic_value = error.get("message")
            if diagnostic_value is None or diagnostic_value == "":
                diagnostic_value = error.get("hint")
            diagnostic = _diagnostic_text(diagnostic_value)
            message = f"{code_label}: {diagnostic}" if diagnostic else code_label
            stop_reason = (
                _STOP_REASONS.get(code, "hard_error")
                if isinstance(code, str)
                else "hard_error"
            )
            metadata = {**process_metadata, "stop_reason": stop_reason}
            if "retry_after_seconds" in error:
                metadata["retry_after_seconds"] = _redact_value(error["retry_after_seconds"])
            elif "retry_after_seconds" in payload:
                metadata["retry_after_seconds"] = _redact_value(payload["retry_after_seconds"])
            status = (
                CrawlStatus.BLOCKED
                if stop_reason in {"auth_required", "rate_limited", "platform_blocked"}
                else CrawlStatus.ERROR
            )
            return finish(
                CrawlResult(
                    url=url,
                    status=status,
                    tool=self.name,
                    error=message,
                    metadata=metadata,
                )
            )

        if returncode != 0:
            return finish(
                CrawlResult(
                    url=url,
                    status=CrawlStatus.ERROR,
                    tool=self.name,
                    error=f"Agent Reach exited with status {returncode}",
                    metadata={**process_metadata, "stop_reason": "hard_error"},
                )
            )

        if payload is None or payload.get("ok") is not True:
            return finish(
                CrawlResult(
                    url=url,
                    status=CrawlStatus.ERROR,
                    tool=self.name,
                    error="Agent Reach returned malformed or non-object JSON",
                    metadata={**process_metadata, "stop_reason": "hard_error"},
                )
            )

        items = payload.get("items")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            return finish(
                CrawlResult(
                    url=url,
                    status=CrawlStatus.ERROR,
                    tool=self.name,
                    error="Agent Reach success response requires an items list of objects",
                    metadata={**process_metadata, "stop_reason": "hard_error"},
                )
            )

        return finish(
            CrawlResult(
                url=url,
                status=CrawlStatus.OK,
                status_code=200,
                tool=self.name,
                markdown=_markdown(items),
                extracted=_public_items(items),
                metadata={**process_metadata, "stop_reason": "success"},
            )
        )
