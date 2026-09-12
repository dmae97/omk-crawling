"""Exercise the real curl adapter against an owned loopback HTTP server."""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from omk_crawl.router import SmartRouter


@pytest.mark.parametrize("code", [429, 503])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_http_retry_with_real_adapter(monkeypatch, code, mode):
    pytest.importorskip("curl_cffi")
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            status = code if len(calls) == 1 else 200
            body = b"<article><h1>Recovery verified</h1><p>Public fixture content.</p></article>"
            self.send_response(status)
            self.send_header("Retry-After", "0")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/fixture"
        router = SmartRouter(
            tools=["curl_cffi"],
            respect_robots=False,
            min_delay=0,
            retry_delay=0,
            learn_sites=False,
        )
        if mode == "sync":
            result = router.crawl(url, timeout=2)
        else:
            result = asyncio.run(router.crawl_async(url, timeout=2))
        assert result.ok, result.error
        assert result.tool == "curl_cffi"
        assert result.markdown is not None
        assert "Recovery verified" in result.markdown
        assert result.metadata["retry_count"] == 1
        assert calls == ["/fixture", "/fixture"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        assert not worker.is_alive()
