"""Offline tests for the Agent Reach subprocess adapter."""

from __future__ import annotations

import itertools
import json
import math
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from omk_crawl.result import CrawlStatus
from omk_crawl.tools.agent_reach_tool import (
    MAX_OUTPUT_BYTES,
    AgentReachTool,
    route_for_target,
)

POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="executable success requires POSIX")
SECRET = "DO-NOT-LEAK"
NORMALIZED_FIELDS = ("title", "url", "author", "published_at", "text")


def nested_secrets() -> dict[str, Any]:
    return {
        "authorization": f"Bearer {SECRET}",
        "cookie": f"session={SECRET}",
        "x-api-key": SECRET,
        "nested": {"token": SECRET, "password": SECRET, "secret": SECRET},
        "url": f"https://user:{SECRET}@example.com/path?token={SECRET}&safe=visible",
    }


class FakeReach:
    """A local executable that records argv and emits configured bytes."""

    def __init__(self, path: Path, argv_path: Path, calls_path: Path, supervised_path: Path):
        self.path = str(path)
        self._argv_path = argv_path
        self._calls_path = calls_path
        self._supervised_path = supervised_path

    @property
    def argv(self) -> list[str]:
        return json.loads(self._argv_path.read_text(encoding="utf-8"))

    @property
    def call_count(self) -> int:
        if not self._calls_path.exists():
            return 0
        return len(self._calls_path.read_text(encoding="utf-8").splitlines())

    @property
    def supervised(self) -> str:
        return self._supervised_path.read_text(encoding="utf-8")


def _write_executable(path: Path, body: str) -> str:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _write_sleeper(path: Path, pid_path: Path) -> None:
    path.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "import time\n"
        f"Path({str(pid_path)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )


def _wait_for_pid(path: Path, timeout: float = 1.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return int(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            time.sleep(0.01)
    raise AssertionError(f"PID file was not created: {path}")


def _process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if sys.platform == "linux":
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            if stat.rsplit(")", 1)[1].split()[0] == "Z":
                return False
        except (OSError, IndexError):
            pass
    return True


def _wait_until_not_running(pid: int, timeout: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_is_running(pid):
            return True
        time.sleep(0.01)
    return not _process_is_running(pid)


def _cleanup_recorded_process(pid_path: Path) -> None:
    try:
        pid = int(pid_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _wait_until_not_running(pid)


@pytest.fixture
def make_fake(tmp_path: Path):
    sequence = itertools.count()

    def make(
        payload: Any = None,
        *,
        stdout: bytes | None = None,
        stderr: bytes = b"",
        returncode: int = 0,
        delay: float = 0,
    ) -> FakeReach:
        directory = tmp_path / f"fake-{next(sequence)}"
        directory.mkdir()
        executable = directory / "agent-reach"
        argv_path = directory / "argv.json"
        calls_path = directory / "calls.jsonl"
        supervised_path = directory / "supervised.txt"
        stdout_path = directory / "stdout.bin"
        stderr_path = directory / "stderr.bin"
        if payload is None:
            payload = {"ok": True, "items": []}
        if stdout is None:
            stdout = json.dumps(payload).encode("utf-8")
        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import os\n"
            "from pathlib import Path\n"
            "import sys\n"
            "import time\n"
            f"argv_path = Path({str(argv_path)!r})\n"
            f"calls_path = Path({str(calls_path)!r})\n"
            f"supervised_path = Path({str(supervised_path)!r})\n"
            f"stdout_path = Path({str(stdout_path)!r})\n"
            f"stderr_path = Path({str(stderr_path)!r})\n"
            "argv_path.write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
            "supervised_path.write_text("
            "os.environ.get('REACH_API_SUPERVISED', ''), encoding='utf-8')\n"
            "with calls_path.open('a', encoding='utf-8') as stream:\n"
            "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            f"time.sleep({delay!r})\n"
            "sys.stdout.buffer.write(stdout_path.read_bytes())\n"
            "sys.stdout.buffer.flush()\n"
            "sys.stderr.buffer.write(stderr_path.read_bytes())\n"
            "sys.stderr.buffer.flush()\n"
            f"raise SystemExit({returncode!r})\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        return FakeReach(executable, argv_path, calls_path, supervised_path)

    return make


@POSIX_ONLY
def test_success_result(make_fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    marker = tmp_path / "SHOULDER-NOT-RUN"
    item = {
        "title": "Example",
        "url": "https://example.com/item",
        "author": "alice",
        "published_at": "2026-01-02",
        "text": "untrusted $(touch SHOULDER-NOT-RUN)",
        "extra": {"preserved": True},
    }
    payload = {"ok": True, "items": [item], "upstream": {"id": 7}}
    fake = make_fake(payload)
    monkeypatch.chdir(tmp_path)
    target = "https://github.com/org/repo/issues/1"

    result = AgentReachTool(executable=fake.path).fetch(target)

    assert result.ok
    assert result.status is CrawlStatus.OK
    assert result.status_code == 200
    assert result.tool == "agent_reach"
    assert result.metadata["channel"] == "github"
    assert result.metadata["action"] == "read"
    assert result.metadata["untrusted_content"] is True
    assert result.metadata["stop_reason"] == "success"
    assert "raw_response" not in result.metadata
    assert result.metadata["returncode"] == 0
    assert "stdout" not in result.metadata
    assert "stderr" not in result.metadata
    assert result.extracted == [{key: item[key] for key in NORMALIZED_FIELDS}]
    assert result.extracted[0]["title"] == "Example"
    assert result.markdown is not None
    for value in ("Example", item["url"], "alice", "2026-01-02", item["text"]):
        assert value in result.markdown
    assert fake.argv == ["read", target]
    assert fake.call_count == 1
    assert fake.supervised == "1"
    assert not marker.exists()


@POSIX_ONLY
def test_raw_response_and_item_raw_are_opt_in(make_fake, monkeypatch: pytest.MonkeyPatch):
    item = {"title": "public", "raw": nested_secrets()}
    payload = {"ok": True, "items": [item], "upstream": nested_secrets()}
    fake = make_fake(payload)
    monkeypatch.setenv("OMK_AGENT_REACH_RAW", "1")

    result = AgentReachTool(executable=fake.path).fetch("reach://web/read?url=https://example.com")

    assert result.ok
    assert result.metadata["raw_response"] == payload
    assert result.extracted == [{"title": "public", "raw": item["raw"]}]


@POSIX_ONLY
def test_success_omits_nested_raw_secrets_by_default(make_fake):
    item = {"title": "public", "raw": nested_secrets(), "extra": nested_secrets()}
    payload = {"ok": True, "items": [item], "upstream": nested_secrets()}
    fake = make_fake(payload)

    result = AgentReachTool(executable=fake.path).fetch("reach://web/read?url=https://example.com")

    assert result.ok
    assert result.extracted == [{"title": "public"}]
    assert "raw_response" not in result.metadata
    assert SECRET not in json.dumps(result.__dict__, default=str)


@POSIX_ONLY
def test_reach_scheme_preserves_query_shell_characters(
    make_fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fake = make_fake()
    monkeypatch.chdir(tmp_path)

    result = AgentReachTool(executable=fake.path).fetch(
        "reach://github/search?q=a;b$(touch SHOULDER-NOT-RUN"
    )

    assert result.ok
    assert fake.argv == ["github", "search", "a;b$(touch SHOULDER-NOT-RUN"]
    assert not (tmp_path / "SHOULDER-NOT-RUN").exists()


@pytest.mark.parametrize(
    ("code", "stop_reason"),
    [
        ("NEED_LOGIN", "auth_required"),
        ("RATE_LIMITED", "rate_limited"),
        ("PLATFORM_BLOCKED", "platform_blocked"),
        ("NEED_SETUP", "needs_setup"),
        ("NOT_FOUND", "not_found"),
        ("BAD_INPUT", "invalid_target"),
        ("UPSTREAM_BROKEN", "upstream_broken"),
        ("TIMEOUT", "deadline_exceeded"),
        ("BUSY", "busy"),
    ],
)
@POSIX_ONLY
def test_errors_do_not_retry(make_fake, code: str, stop_reason: str):
    payload = {"ok": False, "error": {"code": code, "message": "stop"}}
    fake = make_fake(payload, returncode=1)

    result = AgentReachTool(executable=fake.path).fetch(
        "reach://twitter/thread?url=https://x.com/u/status/1"
    )

    assert not result.ok
    assert result.metadata["stop_reason"] == stop_reason
    assert result.metadata["returncode"] == 1
    assert "raw_response" not in result.metadata
    assert result.metadata["untrusted_content"] is True
    assert fake.call_count == 1


@POSIX_ONLY
def test_rate_limit_preserves_retry_after_seconds(make_fake):
    payload = {
        "ok": False,
        "error": {"code": "RATE_LIMITED", "message": "wait", "retry_after_seconds": 12},
    }
    fake = make_fake(payload, returncode=1)

    result = AgentReachTool(executable=fake.path).fetch("reach://web/read?url=https://example.com")

    assert result.metadata["stop_reason"] == "rate_limited"
    assert result.metadata["retry_after_seconds"] == 12
    assert result.metadata["returncode"] == 1


@POSIX_ONLY
def test_nonzero_error_envelope_prefers_message_and_sanitizes_diagnostics(make_fake):
    diagnostic = nested_secrets()
    diagnostic["visible"] = "login required"
    payload = {
        "ok": False,
        "error": {
            "code": "NEED_LOGIN",
            "message": diagnostic,
            "hint": "HINT-MUST-NOT-WIN",
            "details": nested_secrets(),
            "retry_after_seconds": nested_secrets(),
        },
        "upstream": nested_secrets(),
    }
    fake = make_fake(payload, stderr=f"Authorization: Bearer {SECRET}".encode(), returncode=1)

    result = AgentReachTool(executable=fake.path).fetch("reach://web/read?url=https://example.com")

    assert not result.ok
    assert result.metadata["stop_reason"] == "auth_required"
    assert result.metadata["returncode"] == 1
    assert result.metadata["untrusted_content"] is True
    assert "raw_response" not in result.metadata
    assert result.error is not None and result.error.startswith("NEED_LOGIN:")
    assert "login required" in result.error
    assert "safe=visible" in result.error
    assert "HINT-MUST-NOT-WIN" not in result.error
    assert SECRET not in json.dumps(result.__dict__, default=str)


@POSIX_ONLY
def test_error_message_wins_over_hint_with_string_diagnostics(make_fake):
    payload = {
        "ok": False,
        "error": {
            "code": "UPSTREAM_BROKEN",
            "message": (
                f"failed Bearer {SECRET}; authorization={SECRET} cookie={SECRET} "
                f"x-api-key={SECRET} token={SECRET} password={SECRET} secret={SECRET}; "
                f"https://user:{SECRET}@example.com/?password={SECRET}&safe=yes"
            ),
            "hint": "HINT-MUST-NOT-WIN",
        }
    }
    fake = make_fake(payload, returncode=1)

    result = AgentReachTool(executable=fake.path).fetch("reach://web/read?url=https://example.com")

    assert result.error is not None and result.error.startswith("UPSTREAM_BROKEN: failed")
    assert "safe=yes" in result.error
    assert "HINT-MUST-NOT-WIN" not in result.error
    assert SECRET not in result.error


def test_missing_tool_is_portable(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    assert AgentReachTool(executable=str(missing)).available() is False


@POSIX_ONLY
def test_fake_executable_is_available(make_fake):
    fake = make_fake()
    assert AgentReachTool(executable=fake.path).available() is True


def test_non_posix_fails_closed_before_spawn(make_fake, monkeypatch: pytest.MonkeyPatch):
    fake = make_fake()
    monkeypatch.setattr(os, "name", "nt")
    tool = AgentReachTool(executable=fake.path)

    assert tool.available() is False
    result = tool.fetch("reach://web/read?url=https://example.com")

    assert result.status is CrawlStatus.TOOL_MISSING
    assert result.metadata["stop_reason"] == "tool_missing"
    assert "POSIX" in (result.error or "")
    assert "process group" in (result.error or "")
    assert fake.call_count == 0


@POSIX_ONLY
@pytest.mark.parametrize("stdout", [b"not-json", b"[]", b"null", b'{"ok":true,"items":{}}'])
def test_malformed_success_output_is_hard_error(make_fake, stdout: bytes):
    fake = make_fake(stdout=stdout, stderr=b"diagnostic")

    result = AgentReachTool(executable=fake.path).fetch("reach://web/read?url=https://example.com")

    assert not result.ok
    assert result.metadata["stop_reason"] == "hard_error"
    assert result.metadata["returncode"] == 0
    assert "stdout" not in result.metadata
    assert "stderr" not in result.metadata


@POSIX_ONLY
@pytest.mark.parametrize("overflow_stream", ["stdout", "stderr"])
def test_oversized_output_fails_closed(make_fake, overflow_stream: str):
    oversized = b"x" * (MAX_OUTPUT_BYTES + 1)
    streams = {"stdout": b'{"ok":true,"items":[]}', "stderr": b""}
    streams[overflow_stream] = oversized
    fake = make_fake(stdout=streams["stdout"], stderr=streams["stderr"])

    result = AgentReachTool(executable=fake.path).fetch("reach://web/read?url=https://example.com")

    assert not result.ok
    assert result.metadata["stop_reason"] == "output_too_large"
    assert result.metadata["returncode"] is not None
    assert "stdout" not in result.metadata
    assert "stderr" not in result.metadata


@POSIX_ONLY
def test_nonzero_exit_does_not_retain_raw_or_stderr_secrets(make_fake):
    payload = {"ok": True, "items": [], "nested": nested_secrets()}
    fake = make_fake(payload, stderr=f"token={SECRET}".encode(), returncode=7)

    result = AgentReachTool(executable=fake.path).fetch("reach://web/read?url=https://example.com")

    assert not result.ok
    assert result.metadata["stop_reason"] == "hard_error"
    assert result.metadata["returncode"] == 7
    assert "raw_response" not in result.metadata
    assert "stdout" not in result.metadata
    assert "stderr" not in result.metadata
    assert SECRET not in json.dumps(result.__dict__, default=str)


@POSIX_ONLY
def test_local_timeout_kills_and_waits(make_fake):
    fake = make_fake(delay=5)

    result = AgentReachTool(executable=fake.path).fetch(
        "reach://web/read?url=https://example.com", timeout=0.05
    )

    assert not result.ok
    assert result.metadata["stop_reason"] == "deadline_exceeded"
    assert result.metadata["returncode"] is not None
    assert result.metadata["returncode"] != 0
    assert fake.call_count == 1


@POSIX_ONLY
def test_recursive_json_returns_hard_error(make_fake):
    nested = b"[" * 20000 + b"0" + b"]" * 20000
    fake = make_fake(stdout=nested)

    result = AgentReachTool(executable=fake.path).fetch("reach://web/read?url=https://example.com")

    assert not result.ok
    assert result.metadata["stop_reason"] == "hard_error"
    assert "raw_response" not in result.metadata
    assert "stdout" not in result.metadata
    assert "stderr" not in result.metadata


@POSIX_ONLY
@pytest.mark.parametrize("code", [["NEED_LOGIN"], {"nested": "NEED_LOGIN"}])
def test_unhashable_error_code_returns_structured_hard_error(make_fake, code: Any):
    payload = {"ok": False, "error": {"code": code, "message": "stop"}}
    fake = make_fake(payload, returncode=1)

    result = AgentReachTool(executable=fake.path).fetch("reach://web/read?url=https://example.com")

    assert not result.ok
    assert result.metadata["stop_reason"] == "hard_error"
    assert "raw_response" not in result.metadata
    assert "stdout" not in result.metadata
    assert "stderr" not in result.metadata


@POSIX_ONLY
@pytest.mark.parametrize("items", [["not-an-object"], [None], [{"title": "valid"}, 42]])
def test_non_dict_items_are_rejected(make_fake, items: list[Any]):
    payload = {"ok": True, "items": items}
    fake = make_fake(payload)

    result = AgentReachTool(executable=fake.path).fetch("reach://web/read?url=https://example.com")

    assert not result.ok
    assert result.metadata["stop_reason"] == "hard_error"
    assert "raw_response" not in result.metadata
    assert result.extracted is None


@pytest.mark.parametrize(
    "timeout",
    [0, -1, math.nan, math.inf, -math.inf, "nan", "inf", pytest.param(10**10000, id="huge-int")],
)
def test_timeout_must_be_finite_and_positive(
    make_fake, timeout: Any, monkeypatch: pytest.MonkeyPatch
):
    fake = make_fake()
    tool = AgentReachTool(executable=fake.path)
    monkeypatch.setattr(tool, "available", lambda: True)

    result = tool.fetch("reach://web/read?url=https://example.com", timeout=timeout)

    assert not result.ok
    assert result.metadata["stop_reason"] == "invalid_target"
    assert "invalid timeout" in (result.error or "")
    assert fake.call_count == 0


def test_process_creation_value_error_is_invalid_target_without_spawning(
    make_fake, monkeypatch: pytest.MonkeyPatch
):
    fake = make_fake()
    tool = AgentReachTool(executable=fake.path)
    monkeypatch.setattr(tool, "available", lambda: True)
    monkeypatch.setattr(
        "omk_crawl.tools.agent_reach_tool.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("embedded null byte")),
    )

    result = tool.fetch("reach://web/read?url=https://example.com")

    assert result.status is CrawlStatus.ERROR
    assert result.metadata["stop_reason"] == "invalid_target"
    assert result.error == "invalid_target"
    assert fake.call_count == 0


@pytest.mark.skipif(os.name != "posix", reason="owned process groups are POSIX-specific")
def test_timeout_kills_owned_process_group(tmp_path: Path):
    parent_pid_path = tmp_path / "parent.pid"
    child_pid_path = tmp_path / "child.pid"
    supervised_path = tmp_path / "timeout-supervised.txt"
    sleeper = tmp_path / "sleeper.py"
    _write_sleeper(sleeper, child_pid_path)
    executable = _write_executable(
        tmp_path / "agent-reach",
        "import os\n"
        "from pathlib import Path\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        f"Path({str(parent_pid_path)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
        f"Path({str(supervised_path)!r}).write_text("
        "os.environ.get('REACH_API_SUPERVISED', ''), encoding='utf-8')\n"
        f"subprocess.Popen([sys.executable, {str(sleeper)!r}], "
        "start_new_session=os.environ.get('REACH_API_SUPERVISED') != '1')\n"
        "time.sleep(60)\n",
    )

    try:
        result = AgentReachTool(executable=executable).fetch(
            "reach://web/read?url=https://example.com", timeout=0.5
        )

        assert result.metadata["stop_reason"] == "deadline_exceeded"
        assert supervised_path.read_text(encoding="utf-8") == "1"
        child_pid = _wait_for_pid(child_pid_path)
        assert _wait_until_not_running(child_pid)
    finally:
        _cleanup_recorded_process(parent_pid_path)
        _cleanup_recorded_process(child_pid_path)


@pytest.mark.skipif(os.name != "posix", reason="owned process groups are POSIX-specific")
def test_streaming_overflow_kills_early_and_cleans_descendants(tmp_path: Path):
    parent_pid_path = tmp_path / "stream-parent.pid"
    child_pid_path = tmp_path / "stream-child.pid"
    finished_path = tmp_path / "stream-finished"
    sleeper = tmp_path / "stream-sleeper.py"
    _write_sleeper(sleeper, child_pid_path)
    executable = _write_executable(
        tmp_path / "streaming-agent-reach",
        "import os\n"
        "from pathlib import Path\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        f"Path({str(parent_pid_path)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
        f"subprocess.Popen([sys.executable, {str(sleeper)!r}])\n"
        "for _ in range(64):\n"
        "    os.write(1, b'x' * 65536)\n"
        "    time.sleep(0.02)\n"
        f"Path({str(finished_path)!r}).write_text('finished', encoding='utf-8')\n",
    )

    try:
        result = AgentReachTool(executable=executable).fetch(
            "reach://web/read?url=https://example.com", timeout=10
        )

        assert result.metadata["stop_reason"] == "output_too_large"
        assert result.metadata["returncode"] != 0
        assert result.elapsed_ms < 1000
        assert not finished_path.exists()
        assert "stdout" not in result.metadata
        assert "stderr" not in result.metadata
        child_pid = _wait_for_pid(child_pid_path)
        assert _wait_until_not_running(child_pid)
    finally:
        _cleanup_recorded_process(parent_pid_path)
        _cleanup_recorded_process(child_pid_path)


@pytest.mark.skipif(os.name != "posix", reason="owned process groups are POSIX-specific")
def test_normal_completion_kills_descendants_and_stops_output_growth(tmp_path: Path):
    parent_pid_path = tmp_path / "success-parent.pid"
    child_pid_path = tmp_path / "success-child.pid"
    supervised_path = tmp_path / "success-supervised.txt"
    growth_path = tmp_path / "descendant-output.bin"
    payload = json.dumps({"ok": True, "items": []}).encode("utf-8")
    writer = tmp_path / "success-writer.py"
    writer.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "import time\n"
        f"Path({str(child_pid_path)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
        f"with open({str(growth_path)!r}, 'ab', buffering=0) as stream:\n"
        "    while True:\n"
        "        stream.write(b'x' * 4096)\n"
        "        time.sleep(0.01)\n",
        encoding="utf-8",
    )
    executable = _write_executable(
        tmp_path / "successful-agent-reach",
        "import os\n"
        "from pathlib import Path\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        f"Path({str(parent_pid_path)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
        f"Path({str(supervised_path)!r}).write_text("
        "os.environ.get('REACH_API_SUPERVISED', ''), encoding='utf-8')\n"
        f"subprocess.Popen([sys.executable, {str(writer)!r}], "
        "start_new_session=os.environ.get('REACH_API_SUPERVISED') != '1')\n"
        "for _ in range(200):\n"
        f"    path = Path({str(growth_path)!r})\n"
        "    if path.exists() and path.stat().st_size > 0:\n"
        "        break\n"
        "    time.sleep(0.01)\n"
        f"os.write(1, {payload!r})\n",
    )

    try:
        result = AgentReachTool(executable=executable).fetch(
            "reach://web/read?url=https://example.com", timeout=2
        )

        assert result.ok
        assert result.metadata["stop_reason"] == "success"
        assert supervised_path.read_text(encoding="utf-8") == "1"
        child_pid = _wait_for_pid(child_pid_path)
        descendant_dead = _wait_until_not_running(child_pid)
        size_after_reap = growth_path.stat().st_size
        time.sleep(0.1)
        assert descendant_dead
        assert growth_path.stat().st_size == size_after_reap
    finally:
        _cleanup_recorded_process(parent_pid_path)
        _cleanup_recorded_process(child_pid_path)


@pytest.mark.parametrize(
    ("host", "channel"),
    [
        ("github.com", "github"),
        ("www.youtube.com", "youtube"),
        ("youtu.be", "youtube"),
        ("bilibili.com", "bilibili"),
        ("v2ex.com", "v2ex"),
        ("x.com", "twitter"),
        ("twitter.com", "twitter"),
        ("facebook.com", "facebook"),
        ("instagram.com", "instagram"),
        ("xiaohongshu.com", "xiaohongshu"),
        ("linkedin.com", "linkedin"),
        ("zhipin.com", "boss"),
        ("xueqiu.com", "xueqiu"),
        ("xiaoyuzhoufm.com", "xiaoyuzhou"),
        ("example.com", "web"),
    ],
)
def test_https_routing(host: str, channel: str):
    target = f"https://{host}/a?x=1&y=2"

    assert route_for_target(target) == (channel, "read", ["read", target])


def test_reach_routing_decodes_query_value():
    assert route_for_target("reach://github/search?q=hello%20world%2Bagent") == (
        "github",
        "search",
        ["github", "search", "hello world+agent"],
    )
    assert route_for_target("reach://web/read?url=https%3A%2F%2Fexample.com%2Fa%3Fx%3D1") == (
        "web",
        "read",
        ["web", "read", "https://example.com/a?x=1"],
    )


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("reach://github/search?q=python", ["github", "search", "python"]),
        ("reach://github/search?q=python&limit=2", ["github", "search", "python", "--limit", "2"]),
        ("reach://github/read?url=repo", ["github", "read", "repo"]),
        ("reach://twitter/search?q=python", ["twitter", "search", "python"]),
        ("reach://twitter/profile?q=user", ["twitter", "profile", "user"]),
        ("reach://twitter/tweets?q=user", ["twitter", "tweets", "user"]),
        ("reach://twitter/thread?url=tweet", ["twitter", "thread", "tweet"]),
        ("reach://youtube/read?url=video", ["youtube", "read", "video"]),
        ("reach://youtube/search?q=python", ["youtube", "search", "python"]),
        ("reach://youtube/subtitle?url=video", ["youtube", "subtitle", "video"]),
        ("reach://youtube/transcribe?url=video", ["youtube", "transcribe", "video"]),
        ("reach://facebook/search?q=python", ["facebook", "search", "python"]),
        ("reach://facebook/profile?q=user", ["facebook", "profile", "user"]),
        ("reach://instagram/search?q=python", ["instagram", "search", "python"]),
        ("reach://instagram/profile?q=user", ["instagram", "profile", "user"]),
        ("reach://instagram/posts?q=user", ["instagram", "posts", "user"]),
        ("reach://bilibili/search?q=python", ["bilibili", "search", "python"]),
        ("reach://bilibili/read?url=video", ["bilibili", "read", "video"]),
        ("reach://bilibili/hot", ["bilibili", "hot"]),
        ("reach://bilibili/hot?limit=3", ["bilibili", "hot", "--limit", "3"]),
        ("reach://bilibili/subtitle?url=video", ["bilibili", "subtitle", "video"]),
        ("reach://xiaohongshu/search?q=python", ["xiaohongshu", "search", "python"]),
        ("reach://xiaohongshu/read?url=note", ["xiaohongshu", "read", "note"]),
        (
            "reach://xiaohongshu/comments?url=note&limit=4",
            ["xiaohongshu", "comments", "note", "--limit", "4"],
        ),
        ("reach://linkedin/jobs?q=python", ["linkedin", "jobs", "python"]),
        ("reach://linkedin/read?url=job", ["linkedin", "read", "job"]),
        ("reach://boss/search?q=python", ["boss", "search", "python"]),
        ("reach://boss/read?url=job", ["boss", "read", "job"]),
        ("reach://v2ex/hot", ["v2ex", "hot"]),
        ("reach://v2ex/hot?limit=3", ["v2ex", "hot", "--limit", "3"]),
        ("reach://v2ex/node?q=python", ["v2ex", "node", "python"]),
        ("reach://v2ex/read?url=topic", ["v2ex", "read", "topic"]),
        ("reach://v2ex/user?q=user", ["v2ex", "user", "user"]),
        ("reach://xueqiu/quote?q=AAPL", ["xueqiu", "quote", "AAPL"]),
        ("reach://xueqiu/search?q=apple", ["xueqiu", "search", "apple"]),
        ("reach://xueqiu/hot_posts", ["xueqiu", "hot_posts"]),
        ("reach://xueqiu/hot_posts?limit=3", ["xueqiu", "hot_posts", "--limit", "3"]),
        ("reach://xueqiu/hot_stocks", ["xueqiu", "hot_stocks"]),
        ("reach://xueqiu/hot_stocks?limit=3", ["xueqiu", "hot_stocks", "--limit", "3"]),
        ("reach://xiaoyuzhou/transcribe?url=episode", ["xiaoyuzhou", "transcribe", "episode"]),
        ("reach://web/search?q=python", ["web", "search", "python"]),
        ("reach://web/read?url=https%3A%2F%2Fexample.com", ["web", "read", "https://example.com"]),
    ],
)
def test_real_channel_action_tuples_produce_exact_argv(target: str, expected: list[str]):
    channel, action, args = route_for_target(target)
    assert args == expected
    assert (channel, action) == tuple(expected[:2])


@pytest.mark.parametrize(
    "target",
    [
        "reach://unknown/read?q=x",
        "reach://github/setup?q=x",
        "reach://github/add?q=x",
        "reach://github/update?q=x",
        "reach://github/login?q=x",
        "reach://github/read/extra?q=x",
        "reach://github/read",
        "reach://github/hot?q=x",
        "reach://web/hot?q=x",
        "reach://web/transcribe?url=x",
        "reach://bilibili/hot?q=x",
        "reach://v2ex/hot?url=x",
        "reach://xueqiu/hot_posts?q=x",
        "reach://bilibili/hot?limit=not-an-int",
        "reach://github/search?q=x&limit=1&limit=2",
        "https://github.com:not-a-port/o/r",
        "https://github.com:0/o/r",
        "https://github.com:65536/o/r",
        "http://github.com/org/repo",
        "github.com/org/repo",
        "",
    ],
)
def test_invalid_targets_are_rejected(target: str):
    with pytest.raises(ValueError, match="invalid_target"):
        route_for_target(target)


@pytest.mark.parametrize(
    "target",
    [
        "reach://github/search?q=bad\x00value",
        "reach://github/search?q=bad\nvalue",
        "reach://github/search?q=bad%00value",
        "reach://github/search?q=bad%0Avalue",
        "reach://github/search?q=bad%7Fvalue",
        "reach://github/search?q=bad%C2%85value",
        "https://github.com/org/repo\x7f",
    ],
)
def test_original_and_decoded_control_characters_are_rejected(target: str):
    with pytest.raises(ValueError, match="invalid_target"):
        route_for_target(target)


def test_invalid_target_does_not_spawn_process(make_fake):
    fake = make_fake()

    result = AgentReachTool(executable=fake.path).fetch("reach://github/login?q=admin")

    assert not result.ok
    assert result.metadata["stop_reason"] == "invalid_target"
    assert fake.call_count == 0


def test_decoded_control_character_does_not_spawn_process(make_fake):
    fake = make_fake()

    result = AgentReachTool(executable=fake.path).fetch("reach://github/search?q=bad%00value")

    assert not result.ok
    assert result.error == "invalid_target"
    assert result.metadata["stop_reason"] == "invalid_target"
    assert fake.call_count == 0
