#!/usr/bin/env python3
"""
Freshness Engine — 자가치유 프록시 풀 with adaptive harvesting.

풀 고갈 → 즉시 보충 → 검증 → 전투 재개.
"후레쉬한 놈들만 골라서 박고, 뻑나면 칼같이 갈아치운다."

Architecture:
  FreshnessEngine
    ├── ProxyEngineV2 (harvest + validate + schedule)
    ├── SourceRanker (adaptive source quality scoring)
    ├── BackgroundHarvester (continuous async daemon)
    └── EmergencyTrigger (on-burn → immediate refill)

Usage:
  engine = FreshnessEngine()
  await engine.start()                          # background harvester
  proxy = await engine.next()                   # next available proxy
  engine.mark_dead(proxy)                        # burn → trigger emergency refill
  await engine.stop()                           # cleanup
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from omk_crawl.tools.proxy_engine_v2 import (
    CACHE_DIR,
    PROXY_SOURCES,
    TLS_PROFILES,
    ProxyEngineV2,
    ProxyNode,
)

logger = logging.getLogger("omk_crawl.freshness")

# ── Source quality tracking ──
RANKER_CACHE = CACHE_DIR / "source_ranks.json"

# ── Tunables ──
HEALTHY_POOL_MIN = 80  # Trigger passive harvest below this
CRITICAL_POOL_MIN = 15  # Trigger emergency harvest below this
HARVEST_INTERVAL_SEC = 45  # Passive harvest interval
EMERGENCY_INTERVAL_SEC = 8  # Emergency harvest interval
RANK_DECAY = 0.95  # Per-cycle decay factor for source scores
MAX_SOURCE_FAILS = 5  # Consecutive fails before source deprioritized
SCORE_FLOOR = -10.0


@dataclass
class SourceRank:
    """Tracks source quality over time."""

    url: str
    score: float = 0.0
    total_harvests: int = 0
    total_yielded: int = 0
    consecutive_fails: int = 0
    last_yielded: float = 0.0
    avg_yield: float = 0.0
    last_error: str = ""

    def record_yield(self, count: int, latency_ms: float = 0):
        self.total_harvests += 1
        self.total_yielded += count
        self.consecutive_fails = 0
        self.last_yielded = time.time()
        # Exponential moving average of yield
        alpha = 0.3
        self.avg_yield = alpha * count + (1 - alpha) * self.avg_yield
        # Boost score: more yield + fast response = higher score
        latency_bonus = 1.0 if latency_ms < 2000 else 0.0
        self.score += (count * 0.15) + latency_bonus

    def record_failure(self, error: str = ""):
        self.total_harvests += 1
        self.consecutive_fails += 1
        self.last_error = error[:120]
        self.score -= 2.0
        if self.consecutive_fails >= MAX_SOURCE_FAILS:
            self.score -= 5.0  # heavy penalty for consistently dead source

    def decay(self):
        """Gradually pull scores toward zero so old data fades."""
        self.score *= RANK_DECAY
        self.score = max(self.score, SCORE_FLOOR)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "score": round(self.score, 2),
            "total_harvests": self.total_harvests,
            "total_yielded": self.total_yielded,
            "consecutive_fails": self.consecutive_fails,
            "last_yielded": self.last_yielded,
            "avg_yield": round(self.avg_yield, 2),
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SourceRank:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class SourceRanker:
    """Adaptive source quality scoring."""

    def __init__(self):
        self._ranks: dict[str, SourceRank] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if RANKER_CACHE.exists():
            # Corrupt rank cache → start empty rather than crash.
            with suppress(Exception):
                data = json.loads(RANKER_CACHE.read_text())
                with self._lock:
                    for d in data:
                        sr = SourceRank.from_dict(d)
                        self._ranks[sr.url] = sr
                logger.info(f"Loaded {len(self._ranks)} source ranks from cache")

    def _save(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with self._lock:
            try:
                data = [
                    sr.to_dict()
                    for sr in sorted(self._ranks.values(), key=lambda x: x.score, reverse=True)
                ]
                RANKER_CACHE.write_text(json.dumps(data, indent=2))
            except Exception as e:
                logger.warning(f"Ranker cache save failed: {e}")

    def get_top_sources(self, n: int = 8) -> list[str]:
        """Get top-N sources by score, ensuring minimum coverage."""
        with self._lock:
            # Ensure all known sources are tracked
            for src in PROXY_SOURCES:
                url = src.replace("{page}", "1")
                base = url.rsplit("&page=", 1)[0] if "&page=" in url else url.rsplit("?page=", 1)[0]
                if base not in self._ranks and url not in self._ranks:
                    self._ranks[url] = SourceRank(url=url)

            ranked = sorted(self._ranks.values(), key=lambda x: x.score, reverse=True)

            # Always include at least 2 untried sources for diversity
            selected: list[str] = []
            tried = [r for r in ranked if r.total_harvests > 0]
            untried = [r for r in ranked if r.total_harvests == 0]

            selected.extend(r.url for r in tried[: max(n - 2, n // 2)])
            selected.extend(r.url for r in untried[: max(2, n - len(selected))])

            # Fill remaining slots from best available
            for r in ranked:
                if len(selected) >= n:
                    break
                if r.url not in selected:
                    selected.append(r.url)

            return selected[:n]

    def mark_yield(self, source_url: str, count: int, latency_ms: float = 0):
        with self._lock:
            sr = self._ranks.get(source_url)
            if sr is None:
                sr = SourceRank(url=source_url)
                self._ranks[source_url] = sr
            sr.record_yield(count, latency_ms)
        self._save()

    def mark_failure(self, source_url: str, error: str = ""):
        with self._lock:
            sr = self._ranks.get(source_url)
            if sr is None:
                sr = SourceRank(url=source_url)
                self._ranks[source_url] = sr
            sr.record_failure(error)
        self._save()

    def decay_all(self):
        with self._lock:
            for sr in self._ranks.values():
                sr.decay()
        self._save()


class FreshnessEngine:
    """Self-healing proxy pool — 사망한 프록시 즉시 교체."""

    def __init__(self, session_seed: str | None = None, min_pool: int = HEALTHY_POOL_MIN):
        self._engine = ProxyEngineV2(session_seed=session_seed, min_pool_size=min_pool)
        self._ranker = SourceRanker()
        self._min_pool = min_pool
        self._running = False
        self._harvest_task: asyncio.Task | None = None
        self._emergency_event = asyncio.Event()
        self._emergency_count = 0
        self._death_count = 0
        self._total_harvested = 0
        self._start_time: float = 0
        self._critical_sources: list[str] = []

    # ── Lifecycle ──────────────────────────────────────────

    async def start(self, initial_harvest: int = 200):
        """Start background harvester and initial pool fill."""
        self._running = True
        self._start_time = time.time()

        # Initial harvest
        await self._engine.harvest(target_count=initial_harvest)
        await self._engine.validate_batch(batch_size=50)

        # Background daemon
        self._harvest_task = asyncio.create_task(self._harvest_loop())
        logger.info(f"FreshnessEngine started — pool: {self._engine.stats['available']} available")

    async def stop(self):
        """Graceful shutdown."""
        self._running = False
        if self._harvest_task:
            self._harvest_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._harvest_task
        logger.info(f"FreshnessEngine stopped — {self._total_harvested} total harvested")

    # ── Proxy interface ────────────────────────────────────

    def next(self, exclude: set[str] | None = None) -> ProxyNode | None:
        """Get next available proxy. Triggers emergency if pool low."""
        node = self._engine.next(exclude=exclude)
        if node is None:
            # Pool fully exhausted — emergency harvest
            self._emergency_count += 1
            self._emergency_event.set()
            logger.warning(f"Pool exhausted — emergency refill #{self._emergency_count}")
        else:
            # Check if pool is getting low
            if self._engine.stats["available"] < CRITICAL_POOL_MIN:
                self._emergency_event.set()
        return node

    def mark_dead(self, node: ProxyNode):
        """Burn a dead proxy and trigger refill if needed."""
        self._death_count += 1
        self._engine.burn(node)
        if self._engine.stats["available"] < CRITICAL_POOL_MIN:
            self._emergency_event.set()

    def mark_success(self, node: ProxyNode, latency_ms: float = 0):
        node.mark_success(latency_ms)

    def mark_rate_limited(self, node: ProxyNode):
        node.mark_failure(is_rate_limit=True)

    # ── Background harvester ───────────────────────────────

    async def _harvest_loop(self):
        """Daemon loop: keep pool healthy."""
        passive_interval = HARVEST_INTERVAL_SEC
        emergency_interval = EMERGENCY_INTERVAL_SEC

        while self._running:
            try:
                # Wait for next cycle or emergency trigger
                if self._emergency_event.is_set():
                    timeout = emergency_interval
                    logger.info("⚠ Emergency harvest mode activated")
                else:
                    timeout = passive_interval

                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._emergency_event.wait(), timeout=timeout)

                self._emergency_event.clear()

                # Decay source ranks
                self._ranker.decay_all()

                # Assess pool health
                stats = self._engine.stats
                available = stats["available"]
                pool_size = stats["pool_size"]

                if available < CRITICAL_POOL_MIN:
                    # ── EMERGENCY: harvest heavy ──
                    await self._emergency_harvest()
                elif available < self._min_pool:
                    # ── Passive: top-up ──
                    target = max(self._min_pool - available + 50, 100)
                    await self._adaptive_harvest(target)
                elif pool_size == 0:
                    # ── Cold start ──
                    await self._adaptive_harvest(300)
                else:
                    # ── Maintenance: light top-up ──
                    await self._adaptive_harvest(60)

                # Validate fresh proxies
                if available < self._min_pool:
                    batch = min(40, max(20, self._min_pool - available))
                    await self._engine.validate_batch(batch_size=batch)

            except Exception:
                logger.exception("Harvest loop error — continuing")

    async def _adaptive_harvest(self, target: int):
        """Harvest using best-ranked sources."""
        sources = self._ranker.get_top_sources(10)
        logger.debug(f"Adaptive harvest: {target} from {len(sources)} sources")

        t0 = time.time()
        added = await self._harvest_from_sources(sources, target)
        elapsed = (time.time() - t0) * 1000

        if added > 0:
            self._total_harvested += added
        logger.info(
            f"Harvested {added} proxies in {elapsed:.0f}ms"
            f" (pool: {self._engine.stats['available']} avail)"
        )

    async def _emergency_harvest(self):
        """Aggressive harvest from all known good sources."""
        # Get both top-ranked AND fresh untried sources
        top_sources = self._ranker.get_top_sources(5)

        # Also try GitHub raw sources that haven't been tried recently
        all_sources = list(set(PROXY_SOURCES))
        random.shuffle(all_sources)
        fresh_sources = [s for s in all_sources if s not in top_sources][:5]

        all_targets = top_sources + fresh_sources
        logger.warning(f"⚡ Emergency harvest: {len(all_targets)} sources")

        added = await self._harvest_from_sources(all_targets, 400)
        self._total_harvested += added

        if added > 0:
            validated = await self._engine.validate_batch(batch_size=min(60, added))
            logger.warning(f"Emergency harvest: +{added} raw, +{validated} valid")
        else:
            # Desperate: scrape known proxy sites via insane-search
            logger.error("Emergency harvest returned 0 proxies — falling back to crawl")
            await self._crawl_proxy_sites()

    async def _harvest_from_sources(self, sources: list[str], target: int) -> int:
        """Harvest from specific sources with ranking feedback."""
        import aiohttp  # pyright: ignore[reportMissingImports]  # lazy: optional dep, live harvest only

        connector = aiohttp.TCPConnector(limit=20, force_close=True)
        total_added = 0

        async with aiohttp.ClientSession(
            connector=connector, timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            tasks = []
            source_map: dict[str, str] = {}
            for src in sources[:12]:
                url = src.replace("{page}", str(random.randint(1, 5)))
                source_map[url] = src
                tasks.append(self._fetch_and_rank(session, url))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                src_url = list(source_map.keys())[i] if i < len(source_map) else ""
                base_url = source_map.get(src_url, src_url)

                if isinstance(result, Exception):
                    self._ranker.mark_failure(base_url, str(result))
                    continue

                if isinstance(result, tuple):
                    count, latency = result
                    if count > 0:
                        self._ranker.mark_yield(base_url, count, latency)
                        total_added += count
                    else:
                        self._ranker.mark_failure(base_url, "0 proxies yielded")

        return total_added

    async def _fetch_and_rank(self, session, url: str) -> tuple[int, float]:
        """Fetch a source and return (proxy_count, latency_ms)."""

        t0 = time.time()
        try:
            async with session.get(url) as resp:
                text = await resp.text()
                nodes = self._engine._parse_proxies(text, url)
                latency = (time.time() - t0) * 1000

                # Add to engine pool
                added = 0
                with self._engine._lock:
                    existing = {n.url for n in self._engine._pool}
                    for node in nodes:
                        h = self._engine._hash(node.url)
                        if node.url not in existing and h not in self._engine._burned:
                            node.tls_profile = self._engine._rng.choice(TLS_PROFILES)
                            self._engine._pool.append(node)
                            existing.add(node.url)
                            added += 1

                return (added, latency)
        except Exception:
            latency = (time.time() - t0) * 1000
            raise

    async def _crawl_proxy_sites(self):
        """Fallback: scrape proxy listing sites via HTTP."""
        crawl_targets = [
            "https://www.freeproxy.world/",
            "https://www.proxy-list.download/",
            "https://proxy-list.org/english/index.php",
            "https://spys.one/en/",
        ]
        import aiohttp  # pyright: ignore[reportMissingImports]  # lazy: optional dep, live crawl only

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            for url in crawl_targets[:3]:
                try:
                    async with session.get(url) as resp:
                        text = await resp.text()
                        nodes = self._engine._parse_proxies(text, url)
                        with self._engine._lock:
                            existing = {n.url for n in self._engine._pool}
                            for node in nodes:
                                h = self._engine._hash(node.url)
                                if node.url not in existing and h not in self._engine._burned:
                                    self._engine._pool.append(node)
                                    existing.add(node.url)
                        if nodes:
                            logger.info(f"Crawl fallback: +{len(nodes)} from {url[:40]}")
                except Exception:
                    continue

    # ── Stats ───────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        engine_stats = self._engine.stats
        top_sources = self._ranker.get_top_sources(5)
        return {
            **engine_stats,
            "freshness": {
                "deaths": self._death_count,
                "emergencies": self._emergency_count,
                "total_harvested": self._total_harvested,
                "uptime_sec": time.time() - self._start_time if self._start_time else 0,
                "harvests_per_min": (
                    self._total_harvested / max((time.time() - self._start_time) / 60, 0.1)
                    if self._start_time
                    else 0
                ),
            },
            "best_sources": [s[:60] + "..." if len(s) > 60 else s for s in top_sources],
        }

    @property
    def engine(self) -> ProxyEngineV2:
        return self._engine
