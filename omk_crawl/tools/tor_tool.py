"""tor adapter — .onion targets through a local Tor SOCKS bridge.

A .onion host is routed here by `resolve_target`, and this tool pins one
transport: curl_cffi over a SOCKS5 Tor bridge. `socks5h` (not `socks5`)
pushes name resolution to the Tor exit — a local resolver can never resolve
.onion, and leaking the lookup is the point Tor exists to avoid.

No escalation happens inside this tool: if the bridge is down the result is
a plain error with an install hint. The caller decides whether .onion
content is even reachable on this machine.

Tor proxy address resolution order:

1. ``tor_proxy`` kwarg (CLI ``--tor-proxy`` / ``SmartRouter.tool_kwargs``)
2. ``OMK_TOR_PROXY`` environment variable
3. ``DEFAULT_TOR_PROXY`` (``socks5h://127.0.0.1:9050`` — stock tor daemon;
   use ``9051`` for the Tor Browser bundle)
"""

from __future__ import annotations

import os
from typing import Any

from omk_crawl.result import CrawlResult
from omk_crawl.tools.curl_cffi_tool import CurlCffiTool

DEFAULT_TOR_PROXY = "socks5h://127.0.0.1:9050"
ENV_TOR_PROXY = "OMK_TOR_PROXY"

_TOR_HINT = (
    "start the tor daemon (`tor` or Tor Browser, SOCKS on 9050/9150) "
    "or pass --tor-proxy / OMK_TOR_PROXY"
)


class TorTool(CurlCffiTool):
    name = "tor"
    # Available whenever curl_cffi is; the Tor daemon itself is probed at
    # fetch time (it may start between diagnose() and crawl()).
    pip_package = "curl_cffi"

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        proxy = kwargs.pop("tor_proxy", None) or os.environ.get(ENV_TOR_PROXY) or DEFAULT_TOR_PROXY
        kwargs["proxy"] = proxy
        # .onion circuits are slow; don't let the shared default cut them off.
        kwargs.setdefault("timeout", 60)
        result = super().fetch(url, **kwargs)
        result.metadata["tor_proxy"] = proxy
        if not result.ok and not result.error:
            result.error = f"fetch via Tor bridge {proxy} failed; {_TOR_HINT}"
        elif not result.ok and result.status_code is None:
            result.error = f"{result.error} ({_TOR_HINT})"
        return result
