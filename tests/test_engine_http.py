"""Real default-engine CLI against an owned loopback server, without browsers."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest


def test_default_engine_cli_recovers_without_profile_amplification(tmp_path):
    pytest.importorskip("curl_cffi")
    calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(503 if len(calls) == 1 else 200)
            body = (
                b"<article><h1>Engine recovered</h1><p>Owned public fixture content.</p></article>"
            )
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Retry-After", "0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "omk_crawl.cli",
                f"http://127.0.0.1:{server.server_port}/engine",
                "--json",
                "--no-browser",
                "--no-robots",
                "--min-delay",
                "0",
                "--max-fetches",
                "2",
                "--total-timeout",
                "5",
                "--timeout",
                "2",
            ],
            env={
                **os.environ,
                "NO_PROXY": "127.0.0.1",
                "CI": "1",
                "OMK_CRAWL_NO_STAR": "1",
                "OMK_CRAWL_SITE_MEMORY": str(tmp_path / "sites.json"),
            },
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        assert result["status"] == "ok" and result["tool"] == "insane_search"
        assert "Engine recovered" in result["markdown"]
        assert result["metadata"]["execution"]["fetches"] == 2
        assert [event["status_code"] for event in result["metadata"]["execution"]["trace"]] == [
            503,
            200,
        ]
        assert calls == ["/engine", "/engine"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        assert not worker.is_alive()
