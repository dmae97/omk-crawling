"""
Proxy Engine v2 — 고도화된 프록시 로테이션 엔진.

v1 대비 개선:
  1. 검증 타겟: Vercel site → Firebase identitytoolkit API (실제 공격 대상)
  2. SOCKS5 지원: aiohttp_socks + PySocks
  3. TLS fingerprint rotation: curl_cffi impersonate × 8 profiles
  4. Per-proxy backoff: 프록시별 독립 rate limit 추적
  5. Auto-refresh: 풀 고갈 시 자동 보충
  6. Concurrent workers: 워커별 프록시 affinity
  7. IPv6 fallback: IPv6 사용 가능 시 자동 전환
  8. Session rotation: 요청마다 새 TLS 세션

Architecture:
  ProxyEngine
    ├── ProxyHarvester (11 sources, async)
    ├── ProxyValidator (Firebase API target, concurrent)
    ├── ProxyScheduler (score-based, per-proxy backoff)
    └── TLSRotator (curl_cffi impersonate profiles)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import socket
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    import aiohttp  # pyright: ignore[reportMissingImports]  # noqa: F401 — annotations only

logger = logging.getLogger("omk_crawl.proxy_engine_v2")

# ── Paths ──
# Project-relative by default (the historical absolute path belonged to another
# machine); OMK_CACHE_DIR still wins.
CACHE_DIR = Path(os.environ.get("OMK_CACHE_DIR", ".crawl_cache"))
POOL_CACHE = CACHE_DIR / "proxy_pool_v2.json"
BURNED_CACHE = CACHE_DIR / "burned_proxies_v2.json"

# ── Freshness ("newest first") ──
# Proxies older than this lose score rank; fresh_only selection excludes them.
STALE_SECONDS = float(os.environ.get("OMK_PROXY_STALE_SECONDS", "3600"))

# ── Validation target: Firebase Auth API (actual attack surface) ──
# The web API key comes from the environment — credentials are never hardcoded.
VALIDATE_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signUp"
VALIDATE_KEY = os.environ.get("OMK_PROXY_VALIDATE_KEY", "")

# ── Proxy sources (expanded, prioritized by reliability) ──
PROXY_SOURCES = [
    # Tier 1: High-reliability API sources
    "https://proxylist.geonode.com/api/proxy-list?limit=100&page={page}&sort_by=lastChecked&sort_type=desc&filterUpTime=90&speed=fast&protocols=http,https,socks5",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=3000&country=all&ssl=all&anonymity=elite",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=3000&country=all",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks4&timeout=3000&country=all",
    # Tier 2: GitHub curated lists (updated frequently)
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks5.txt",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
    "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
    # Tier 3: API-based (rate limited but reliable)
    "https://www.proxy-list.download/api/v1/get?type=http",
    "https://www.proxy-list.download/api/v1/get?type=https",
    "https://www.proxy-list.download/api/v1/get?type=socks5",
]

# ── TLS impersonate profiles (curl_cffi) ──
TLS_PROFILES = [
    "chrome124",
    "chrome120",
    "chrome116",
    "chrome110",
    "safari17_0",
    "safari15_5",
    "edge101",
    "firefox133",
]

# ── Geo preferences (avoid KR/JP/CN for stealth) ──
PREFERRED_GEOS = {"US", "DE", "NL", "SE", "CH", "FR", "CA", "SG", "AU", "UK", "FI", "NO"}
AVOID_GEOS = {"KR", "JP", "CN", "HK", "TW", "RU", "IR", "KP", "IN"}


@dataclass
class ProxyNode:
    """A proxy endpoint with health tracking."""

    url: str
    protocol: str = "http"  # http, https, socks5, socks4
    country: str = ""
    anonymity: str = "elite"
    latency_ms: float = 0.0
    uptime_pct: float = 80.0
    last_validated: float = 0.0
    success_count: int = 0
    fail_count: int = 0
    consecutive_fails: int = 0
    last_used: float = 0.0
    use_count: int = 0
    backoff_until: float = 0.0  # per-proxy rate limit cooldown
    tls_profile: str = ""  # assigned TLS fingerprint
    harvested_at: float = 0.0  # last time a source list re-served this proxy
    source: str = ""  # which free list served it

    @property
    def is_stale(self) -> bool:
        """No source re-served it and no validation happened within the window."""
        ref = self.harvested_at or self.last_validated
        if ref <= 0:
            return True  # unknown age: treat as stale (fresh-first policy)
        return (time.time() - ref) > STALE_SECONDS

    @property
    def score(self) -> float:
        """Composite health score. Higher = better."""
        base = self.uptime_pct * 0.01
        success_bonus = min(self.success_count * 0.3, 5.0)
        fail_penalty = self.consecutive_fails * 3.0
        latency_penalty = self.latency_ms / 5000.0
        geo_bonus = 1.0 if self.country in PREFERRED_GEOS else 0.0
        geo_penalty = 2.0 if self.country in AVOID_GEOS else 0.0
        stale_penalty = 1.5 if self.is_stale else 0.0
        total = (
            base
            + success_bonus
            - fail_penalty
            - latency_penalty
            + geo_bonus
            - geo_penalty
            - stale_penalty
        )
        return max(0, total)

    @property
    def is_available(self) -> bool:
        """Not in backoff and not over-used."""
        return time.time() > self.backoff_until and self.use_count < 10

    @property
    def proxy_url(self) -> str:
        """aiohttp-compatible proxy URL."""
        if "://" in self.url:
            return self.url
        return f"{self.protocol}://{self.url}"

    def mark_success(self, latency_ms: float):
        self.success_count += 1
        self.consecutive_fails = 0
        self.latency_ms = latency_ms
        self.last_used = time.time()
        self.use_count += 1

    def mark_failure(self, is_rate_limit: bool = False):
        self.fail_count += 1
        self.consecutive_fails += 1
        self.last_used = time.time()
        self.use_count += 1
        if is_rate_limit:
            # Per-proxy backoff: exponential
            backoff = min(300, 10 * (2 ** min(self.consecutive_fails, 5)))
            self.backoff_until = time.time() + backoff
        elif self.consecutive_fails >= 5:
            # Dead proxy: long backoff
            self.backoff_until = time.time() + 600

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "protocol": self.protocol,
            "country": self.country,
            "anonymity": self.anonymity,
            "latency_ms": self.latency_ms,
            "uptime_pct": self.uptime_pct,
            "last_validated": self.last_validated,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "consecutive_fails": self.consecutive_fails,
            "use_count": self.use_count,
            "harvested_at": self.harvested_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProxyNode:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ProxyEngineV2:
    """High-performance proxy rotation engine with auto-refresh."""

    def __init__(self, session_seed: str | None = None, min_pool_size: int = 50):
        self._pool: list[ProxyNode] = []
        self._burned: set[str] = set()
        self._lock = threading.Lock()
        self._rng = random.Random(session_seed or hashlib.sha256(os.urandom(32)).hexdigest()[:16])
        self._min_pool_size = min_pool_size
        self._harvest_lock = asyncio.Lock()
        self._tls_idx = 0
        self._ipv6_checked = False
        self._ipv6_cache = False
        self._load_cache()
        logger.info(f"ProxyEngine v2 initialized: {len(self._pool)} cached")

    # ── IPv6 detection (lazy: never on the constructor's critical path) ──
    @property
    def has_ipv6(self) -> bool:
        if not self._ipv6_checked:
            self._ipv6_cache = self._check_ipv6()
            self._ipv6_checked = True
        return self._ipv6_cache

    # ── IPv6 detection ──
    @staticmethod
    def _check_ipv6() -> bool:
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
                sock.settimeout(2)
                sock.connect(("2001:4860:4860::8888", 53))  # Google DNS IPv6
            return True
        except Exception:
            return False

    # ── Cache ──
    def _load_cache(self):
        # A corrupt/partial cache must not brick startup — fall back to an
        # empty pool and let normal discovery refill it.
        if POOL_CACHE.exists():
            with suppress(Exception):
                data = json.loads(POOL_CACHE.read_text())
                self._pool = [ProxyNode.from_dict(d) for d in data]
        if BURNED_CACHE.exists():
            with suppress(Exception):
                self._burned = set(json.loads(BURNED_CACHE.read_text()))

    def _save_cache(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with self._lock:
            try:
                top = sorted(self._pool, key=lambda x: x.score, reverse=True)[:2000]
                data = [n.to_dict() for n in top]
                POOL_CACHE.write_text(json.dumps(data))
                BURNED_CACHE.write_text(json.dumps(list(self._burned)[:5000]))
            except Exception as e:
                logger.warning(f"Cache save failed: {e}")

    # ── Harvest ──
    async def harvest(self, target_count: int = 200) -> int:
        """Harvest proxies from all sources. Returns new count."""
        import aiohttp  # pyright: ignore[reportMissingImports]  # lazy: optional dep, live harvesting only

        new_nodes: list[ProxyNode] = []
        connector = aiohttp.TCPConnector(limit=20, force_close=True)
        timeout = aiohttp.ClientTimeout(total=12)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            tasks = []
            for source in self._rng.sample(PROXY_SOURCES, min(8, len(PROXY_SOURCES))):
                url = source.replace("{page}", str(self._rng.randint(1, 5)))
                tasks.append(self._fetch_source(session, url))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, list):
                    new_nodes.extend(r)

        # Deduplicate, skip burned, and bump freshness of known URLs.
        added = self._ingest(new_nodes)

        if added > 0:
            self._save_cache()
        logger.info(f"Harvested {added} new proxies (pool: {len(self._pool)})")
        return added

    def _ingest(self, nodes: list[ProxyNode]) -> int:
        """Merge harvested nodes into the pool; returns the newly added count.

        A source re-serving a known proxy is a freshness signal: bump its
        ``harvested_at`` instead of duplicating it. Burned URLs never return.
        """
        now = time.time()
        added = 0
        with self._lock:
            existing = {n.url: n for n in self._pool}
            for node in nodes:
                known = existing.get(node.url)
                if known is not None:
                    # A source re-serving this proxy is a freshness observation
                    # made now — touch it (never move backward).
                    known.harvested_at = max(known.harvested_at, now)
                    if node.source and node.source != known.source:
                        known.source = node.source
                    continue
                if self._hash(node.url) in self._burned:
                    continue
                # Assign TLS profile
                node.tls_profile = TLS_PROFILES[self._tls_idx % len(TLS_PROFILES)]
                self._tls_idx += 1
                if node.harvested_at <= 0:
                    node.harvested_at = now
                self._pool.append(node)
                existing[node.url] = node
                added += 1
            # Keep top 3000 by score
            self._pool.sort(key=lambda x: x.score, reverse=True)
            self._pool = self._pool[:3000]
        return added

    async def _fetch_source(self, session: aiohttp.ClientSession, url: str) -> list[ProxyNode]:
        """Fetch and parse a single proxy source."""
        try:
            async with session.get(url) as resp:
                text = await resp.text()
                return self._parse_proxies(text, url)
        except Exception:
            return []

    @staticmethod
    def _protocol_from_source(source_url: str) -> str:
        """List type from filename/query markers — the URL scheme (https://)
        is transport, not a proxy protocol."""
        lowered = source_url.lower()
        parts = urlsplit(lowered)
        name = parts.path.rsplit("/", 1)[-1]
        query = parts.query
        if "protocol=socks5" in query or "type=socks5" in query or "socks5" in name:
            return "socks5"
        if "protocol=socks4" in query or "type=socks4" in query or "socks4" in name:
            return "socks4"
        if "https" in name:  # e.g. HTTPS_RAW.txt, https.txt
            return "https"
        if (
            "http" in name
            or "protocol=http" in query
            or "type=http" in query
            or "protocols=http" in query
        ):
            return "http"
        if "socks5" in lowered:  # e.g. /socks5_list/ in the path
            return "socks5"
        if "socks4" in lowered:
            return "socks4"
        return "http"

    def _parse_proxies(self, text: str, source_url: str) -> list[ProxyNode]:
        """Parse proxy list text into ProxyNode objects."""
        nodes: list[ProxyNode] = []
        proto = self._protocol_from_source(source_url)
        harvested = time.time()

        for line in text.strip().splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "//", "{", "[")):
                continue
            # ip:port format
            if ":" in line and len(line) < 50:
                parts = line.split(":")
                if len(parts) == 2:
                    ip, port_str = parts
                    if ip.count(".") == 3:
                        try:
                            port = int(port_str)
                            if 1 <= port <= 65535:
                                nodes.append(
                                    ProxyNode(
                                        url=f"{ip}:{port}",
                                        protocol=proto,
                                        harvested_at=harvested,
                                        source=source_url,
                                    )
                                )
                        except ValueError:
                            continue
        return nodes

    @staticmethod
    def _hash(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:12]

    # ── Validation (against Firebase API) ──
    async def validate_batch(self, batch_size: int = 50, target: str = "firebase") -> int:
        """Validate proxies against the actual attack target."""
        if target == "firebase" and not VALIDATE_KEY:
            raise RuntimeError(
                "firebase proxy validation requires OMK_PROXY_VALIDATE_KEY in the "
                "environment (no API key is hardcoded in source)"
            )
        with self._lock:
            candidates = [
                n
                for n in self._pool
                if n.last_validated == 0 or time.time() - n.last_validated > 1800
            ]
            batch = self._rng.sample(candidates, min(batch_size, len(candidates)))

        if not batch:
            return 0

        valid = 0
        # Use SOCKS connector for socks proxies, regular for http
        tasks = []
        for node in batch:
            tasks.append(self._validate_one(node, target))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = sum(1 for r in results if r)

        self._save_cache()
        logger.info(f"Validated {valid}/{len(batch)} proxies against {target}")
        return valid

    async def _validate_one(self, node: ProxyNode, target: str) -> bool:
        """Validate a single proxy against the target API."""
        import aiohttp  # pyright: ignore[reportMissingImports]  # lazy: optional dep, live validation only

        try:
            from aiohttp_socks import ProxyConnector  # pyright: ignore[reportMissingImports]
        except ImportError:
            logger.warning("aiohttp_socks missing — SOCKS proxies cannot be validated")
            node.mark_failure()
            node.last_validated = time.time()
            return False

        ts = time.time()
        try:
            if node.protocol in ("socks5", "socks4"):
                connector = ProxyConnector.from_url(node.proxy_url)
                timeout = aiohttp.ClientTimeout(total=8)
                async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                    return await self._do_validate(session, node, ts, target)
            else:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
                    return await self._do_validate(session, node, ts, target, proxy=node.proxy_url)
        except Exception:
            node.mark_failure()
            node.last_validated = time.time()
            return False

    async def _do_validate(
        self,
        session: aiohttp.ClientSession,
        node: ProxyNode,
        ts: float,
        target: str,
        proxy: str | None = None,
    ) -> bool:
        """Execute the actual validation request."""
        if target == "firebase":
            # Validate against Firebase Auth API (the actual target)
            url = f"{VALIDATE_URL}?key={VALIDATE_KEY}"
            body = {
                "email": f"validate-{round(ts * 1000)}@gmail.com",
                "password": "ValidateTest1!@",
                "returnSecureToken": False,
            }
            kwargs: dict[str, Any] = {"json": body, "ssl": False}
            if proxy:
                kwargs["proxy"] = proxy
            async with session.post(url, **kwargs) as resp:
                latency = (time.time() - ts) * 1000
                node.last_validated = time.time()
                if resp.status == 200:
                    node.mark_success(latency)
                    return True
                elif resp.status == 400:
                    data = await resp.json()
                    msg = data.get("error", {}).get("message", "")
                    if "EMAIL_EXISTS" in msg:
                        # Proxy works! Email just already exists
                        node.mark_success(latency)
                        return True
                    elif "TOO_MANY_ATTEMPTS" in msg:
                        # Proxy works but rate limited
                        node.mark_success(latency)
                        node.backoff_until = time.time() + 30
                        return True
                    else:
                        node.mark_failure()
                        return False
                else:
                    node.mark_failure()
                    return False
        else:
            # Generic HTTP validation
            kwargs: dict[str, Any] = {"ssl": False}
            if proxy:
                kwargs["proxy"] = proxy
            async with session.get("https://httpbin.org/ip", **kwargs) as resp:
                latency = (time.time() - ts) * 1000
                node.last_validated = time.time()
                if resp.status == 200:
                    node.mark_success(latency)
                    return True
                node.mark_failure()
                return False

    # ── Scheduling ──
    def next(self, exclude: set[str] | None = None, fresh_only: bool = False) -> ProxyNode | None:
        """Get the best available proxy node.

        ``fresh_only`` restricts selection to proxies a source re-served (or a
        validation touched) within ``STALE_SECONDS`` — newest-first. Falls
        back to the whole pool when nothing is fresh.
        """
        with self._lock:
            available = [
                n
                for n in self._pool
                if n.is_available
                and self._hash(n.url) not in self._burned
                and (exclude is None or n.url not in exclude)
            ]
            if fresh_only:
                fresh = [n for n in available if not n.is_stale]
                if fresh:
                    available = fresh
            if not available:
                # Reset use counts for non-burned proxies
                for n in self._pool:
                    if self._hash(n.url) not in self._burned:
                        n.use_count = 0
                        n.backoff_until = 0
                available = [n for n in self._pool if self._hash(n.url) not in self._burned]

            if not available:
                return None

            # Weighted random selection by score
            total = sum(max(n.score, 0.1) for n in available)
            r = self._rng.uniform(0, total)
            cumulative = 0.0
            for n in available:
                cumulative += max(n.score, 0.1)
                if r <= cumulative:
                    return n
            return available[0]

    def burn(self, node: ProxyNode):
        """Permanently remove a proxy (persisted — a burned proxy must survive
        restarts, or the next cache load resurrects a known-bad endpoint)."""
        h = self._hash(node.url)
        with self._lock:
            self._burned.add(h)
            self._pool = [n for n in self._pool if self._hash(n.url) != h]
        self._save_cache()

    # ── Auto-refresh ──
    async def ensure_pool(self, min_available: int | None = None):
        """Auto-harvest if pool drops below threshold."""
        min_avail = min_available or self._min_pool_size
        with self._lock:
            available = sum(
                1 for n in self._pool if n.is_available and self._hash(n.url) not in self._burned
            )
        if available < min_avail:
            logger.info(f"Pool low ({available}/{min_avail}) — auto-harvesting...")
            await self.harvest(target_count=200)
            await self.validate_batch(batch_size=30)

    # ── Stats ──
    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            available = [
                n for n in self._pool if n.is_available and self._hash(n.url) not in self._burned
            ]
            return {
                "pool_size": len(self._pool),
                "available": len(available),
                "burned": len(self._burned),
                "avg_latency_ms": sum(n.latency_ms for n in available) / max(len(available), 1),
                "protocols": {
                    "http": sum(1 for n in available if n.protocol == "http"),
                    "https": sum(1 for n in available if n.protocol == "https"),
                    "socks5": sum(1 for n in available if n.protocol == "socks5"),
                    "socks4": sum(1 for n in available if n.protocol == "socks4"),
                },
                "ipv6": self.has_ipv6,
                "top_countries": list(set(n.country for n in available[:30] if n.country))[:10],
            }
