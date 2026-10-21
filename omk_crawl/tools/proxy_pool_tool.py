"""proxy:// scheme — inspect and drive the free-proxy pool from the CLI.

omk-crawl proxy://stats                 pool snapshot (offline)
omk-crawl proxy://refresh               one bounded harvest+validate cycle
omk-crawl 'proxy://loop?cycles=3&interval=10'
omk-crawl 'proxy://loop?cycles=0'       무한의 굴레 — until Ctrl+C
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.tools.base import BaseTool


def _run_async(coro: Any) -> Any:
    """Run a coroutine from sync code, even under a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class ProxyPoolTool(BaseTool):
    """Drive the self-healing free-proxy pool. Not a web fetcher."""

    name = "proxy_pool"
    pip_package = ""
    layer = 0
    needs_browser = False
    capabilities = frozenset()

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        parts = urlsplit(url)
        action = (parts.netloc or parts.path).strip("/").lower() or "stats"
        params = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        try:
            if action == "stats":
                return self._stats(url)
            if action == "refresh":
                return self._refresh(url, params)
            if action == "loop":
                return self._loop(url, params)
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                error=f"unknown proxy:// action {action!r} (stats | refresh | loop)",
            )
        except ImportError as error:
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                error=f"optional dependency missing: {error}",
                metadata={"install_hint": "pip install 'omk-crawl[proxy]'"},
            )

    # ── actions ──
    def _stats(self, url: str) -> CrawlResult:
        from omk_crawl.tools.freshness_engine import FreshnessEngine

        engine = FreshnessEngine()
        report = {
            "pool": engine.stats,
            "source_ranks": len(engine._ranker._ranks),
        }
        return CrawlResult(
            url=url,
            status=CrawlStatus.OK,
            markdown=json.dumps(report, indent=2, ensure_ascii=False),
            metadata={"action": "stats"},
        )

    def _refresh(self, url: str, params: dict[str, str]) -> CrawlResult:
        from omk_crawl.rotate_crawl import pool_cycles
        from omk_crawl.tools.proxy_engine_v2 import ProxyEngineV2

        try:
            target = self._int_param(params, "harvest", 200)
        except ValueError as error:
            return self._bad_param(url, error)
        engine = ProxyEngineV2()
        reports = _run_async(pool_cycles(engine, cycles=1, harvest_target=target, interval=0))
        r = reports[0]
        body = (
            f"harvest: +{r.added} proxies\n"
            f"validated: {r.validated}\n"
            f"pool: {json.dumps(r.pool, ensure_ascii=False)}"
        )
        return CrawlResult(
            url=url,
            status=CrawlStatus.OK,
            markdown=body,
            metadata={"action": "refresh", "report": r.__dict__ | {"pool": r.pool}},
        )

    def _loop(self, url: str, params: dict[str, str]) -> CrawlResult:
        from omk_crawl.rotate_crawl import pool_cycles
        from omk_crawl.tools.proxy_engine_v2 import ProxyEngineV2

        try:
            cycles = self._int_param(params, "cycles", 1)  # 0 = 무한의 굴레 (until Ctrl+C)
            interval = self._float_param(params, "interval", 60.0)
            target = self._int_param(params, "harvest", 200)
        except ValueError as error:
            return self._bad_param(url, error)
        engine = ProxyEngineV2()
        reports = _run_async(
            pool_cycles(engine, cycles=cycles, interval=interval, harvest_target=target)
        )
        lines = [f"cycle {r.cycle}: +{r.added} harvested, {r.validated} validated" for r in reports]
        if reports:
            lines.append(f"pool: {json.dumps(reports[-1].pool, ensure_ascii=False)}")
        note = " (stopped by cancellation)" if cycles == 0 else ""
        return CrawlResult(
            url=url,
            status=CrawlStatus.OK,
            markdown="\n".join(lines) + note,
            metadata={"action": "loop", "cycles": len(reports)},
        )

    # ── param helpers (fetch must not raise — BaseTool contract) ──
    @staticmethod
    def _int_param(params: dict[str, str], key: str, default: int) -> int:
        value = params.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"{key}={value!r} is not an integer") from None

    @staticmethod
    def _float_param(params: dict[str, str], key: str, default: float) -> float:
        value = params.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            raise ValueError(f"{key}={value!r} is not a number") from None

    @staticmethod
    def _bad_param(url: str, error: ValueError) -> CrawlResult:
        return CrawlResult(url=url, status=CrawlStatus.ERROR, error=str(error))
