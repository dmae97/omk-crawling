"""Detection-aware tool ordering and persistent site outcomes.

Unknown tools retain their relative order and no tool is dropped.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from omk_crawl.detect import BlockType

logger = logging.getLogger("omk_crawl")
_memory_locks: dict[Path, threading.RLock] = {}
_memory_locks_guard = threading.Lock()


def _memory_lock(path: Path) -> threading.RLock:
    key = path.absolute()
    with _memory_locks_guard:
        return _memory_locks.setdefault(key, threading.RLock())


@contextmanager
def _process_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        if hasattr(os, "fchmod"):
            os.fchmod(stream.fileno(), 0o600)
        if os.name == "posix":
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX)
        yield


# Default ladder, lightest → heaviest.
DEFAULT_ORDER: list[str] = [
    "insane_search",
    "curl_cffi",
    "crawl4ai",
    "scrapling",
    "camoufox",
    "patchright",
    "nodriver",
    "browser_use",
]

# BlockType → preferred tool order. Rationale:
#   TLS_FINGERPRINT → different curl_cffi impersonation often passes; insane_search
#                     rotates 8 profiles; real browsers always clear TLS.
#   JS_REQUIRED     → the cheapest renderer (crawl4ai) executes JavaScript.
#   CLOUDFLARE/AKAMAI/IMPERVA/WAF → camoufox (C++-level FP injection) or
#                     scrapling stealth first; insane_search as the cheap breaker.
#   DATADOME/KASADA/PERIMETERX → behavioral vendors that hunt Playwright artifacts:
#                     CDP-native nodriver first, then camoufox/patchright.
#   AWS_WAF         → token challenge; anti-detect browsers handle it best.
#   RATE_LIMIT      → not a tool problem; keep the ladder (caller backs off).
#   AUTH_REQUIRED   → we do NOT bypass auth; empty list = no escalation.
ROUTE_TABLE: dict[BlockType, list[str]] = {
    BlockType.TLS_FINGERPRINT: [
        "insane_search",
        "curl_cffi",
        "camoufox",
        "nodriver",
        "scrapling",
        "patchright",
        "crawl4ai",
        "browser_use",
    ],
    BlockType.JS_REQUIRED: [
        "crawl4ai",
        "scrapling",
        "camoufox",
        "patchright",
        "nodriver",
        "browser_use",
    ],
    BlockType.CLOUDFLARE: [
        "insane_search",
        "camoufox",
        "scrapling",
        "nodriver",
        "patchright",
        "crawl4ai",
        "browser_use",
    ],
    BlockType.AKAMAI: [
        "insane_search",
        "camoufox",
        "scrapling",
        "nodriver",
        "patchright",
        "crawl4ai",
        "browser_use",
    ],
    BlockType.DATADOME: [
        "nodriver",
        "camoufox",
        "scrapling",
        "patchright",
        "insane_search",
        "crawl4ai",
        "browser_use",
    ],
    BlockType.KASADA: [
        "nodriver",
        "camoufox",
        "patchright",
        "scrapling",
        "crawl4ai",
        "insane_search",
        "browser_use",
    ],
    BlockType.PERIMETERX: [
        "nodriver",
        "camoufox",
        "patchright",
        "scrapling",
        "crawl4ai",
        "insane_search",
        "browser_use",
    ],
    BlockType.IMPERVA: [
        "insane_search",
        "camoufox",
        "scrapling",
        "nodriver",
        "patchright",
        "crawl4ai",
        "browser_use",
    ],
    BlockType.AWS_WAF: [
        "camoufox",
        "nodriver",
        "patchright",
        "scrapling",
        "crawl4ai",
        "insane_search",
        "browser_use",
    ],
    BlockType.WAF: [
        "insane_search",
        "camoufox",
        "scrapling",
        "nodriver",
        "patchright",
        "crawl4ai",
        "browser_use",
    ],
    BlockType.RATE_LIMIT: DEFAULT_ORDER,
    BlockType.AUTH_REQUIRED: [],
}

# Priority for resolving combined (flag-OR'd) block types. AUTH first so we
# never escalate into an auth bypass; then the hardest anti-bot signals.
_PRIORITY: tuple[BlockType, ...] = (
    BlockType.AUTH_REQUIRED,
    BlockType.CLOUDFLARE,
    BlockType.AKAMAI,
    BlockType.DATADOME,
    BlockType.KASADA,
    BlockType.PERIMETERX,
    BlockType.IMPERVA,
    BlockType.AWS_WAF,
    BlockType.WAF,
    BlockType.TLS_FINGERPRINT,
    BlockType.JS_REQUIRED,
    BlockType.RATE_LIMIT,
)

# Below this confidence we trust the default ladder over the detection.
CONFIDENCE_THRESHOLD = 0.5


def is_auth_block(block: BlockType) -> bool:
    """True when the block is authentication — we stop, never bypass."""
    return BlockType.AUTH_REQUIRED in block


def _preference_for(block: BlockType) -> list[str]:
    """Resolve the preference list for a (possibly combined) block type."""
    for bt in _PRIORITY:
        if bt in block:
            return ROUTE_TABLE.get(bt, DEFAULT_ORDER)
    return DEFAULT_ORDER


def preferred_order(
    block: BlockType,
    confidence: float,
    available: list[str],
    default: list[str] | None = None,  # noqa: ARG001 — kept for API symmetry
) -> list[str]:
    """Reorder ``available`` tool names for a detected block type.

    - AUTH_REQUIRED → ``[]`` (never escalate to bypass authentication).
    - confidence < CONFIDENCE_THRESHOLD or no block → ``available`` unchanged.
    - Otherwise → tools named in the block's preference list move to the front
      (in preference order); all other available tools follow in their original
      relative order. Returns a permutation of ``available`` (nothing dropped).
    """
    if is_auth_block(block):
        return []
    if confidence < CONFIDENCE_THRESHOLD or block == BlockType.NONE:
        return list(available)

    pref = _preference_for(block)
    if not pref:
        return list(available)

    avail_set = set(available)
    front = [t for t in pref if t in avail_set]
    front_set = set(front)
    rest = [t for t in available if t not in front_set]
    return front + rest


def reorder_tools(tools: list, name_order: list[str]) -> list:
    """Reorder tool *objects* so their ``.name`` follows ``name_order``.

    Stable for duplicate names (tests reuse names): objects sharing a name keep
    their relative order. Any object whose name is absent from ``name_order``
    is appended at the end so nothing is lost.
    """
    from collections import defaultdict, deque

    by_name: dict[str, deque] = defaultdict(deque)
    for t in tools:
        by_name[t.name].append(t)

    result = []
    for name in name_order:
        dq = by_name.get(name)
        if dq:
            result.append(dq.popleft())
    for dq in by_name.values():
        result.extend(dq)
    return result


class SiteMemoryStore(Protocol):
    """Orders available tools and records observed crawl outcomes."""

    def order(self, url: str, tools: list[str]) -> list[str]: ...

    def record(self, url: str, tool: str, success: bool, latency_ms: float) -> None: ...


@dataclass(frozen=True, slots=True)
class ToolStats:
    """Bayesian success counters for one tool on one site."""

    successes: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0

    @property
    def score(self) -> float:
        return (self.successes + 1) / (self.successes + self.failures + 2)

    def with_outcome(self, success: bool, latency_ms: float) -> ToolStats:
        return ToolStats(
            successes=self.successes + (1 if success else 0),
            failures=self.failures + (0 if success else 1),
            total_latency_ms=self.total_latency_ms + max(latency_ms, 0.0),
        )


class SiteMemory:
    """Thread-safe site memory persisted with atomic local JSON writes."""

    def __init__(self, path: Path | None = None) -> None:
        configured = os.environ.get("OMK_CRAWL_SITE_MEMORY")
        self._path = path or Path(configured or "~/.cache/omk-crawl/site-memory.json").expanduser()
        self._lock = _memory_lock(self._path)
        self._domains = self._load()

    def order(self, url: str, tools: list[str]) -> list[str]:
        domain = self._domain(url)
        if domain is None:
            return list(tools)
        with self._lock:
            self._domains = self._load()
            stats = self._domains.get(domain, {})
            ranked = sorted(
                enumerate(tools),
                key=lambda item: (-(stats.get(item[1], ToolStats()).score), item[0]),
            )
        return [tool for _, tool in ranked]

    def record(self, url: str, tool: str, success: bool, latency_ms: float) -> None:
        domain = self._domain(url)
        if domain is None or not tool:
            return
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with _process_lock(self._path):
                    self._domains = self._load()
                    tools = self._domains.setdefault(domain, {})
                    tools[tool] = tools.get(tool, ToolStats()).with_outcome(success, latency_ms)
                    self._save()
            except OSError as exc:
                logger.warning("Could not persist site memory %s: %s", self._path, exc)

    @staticmethod
    def _domain(url: str) -> str | None:
        try:
            parsed = urlparse(url if "://" in url else f"//{url}")
            return parsed.hostname.lower() if parsed.hostname else None
        except ValueError:
            return None

    def _load(self) -> dict[str, dict[str, ToolStats]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable site memory %s: %s", self._path, exc)
            return {}

        if not isinstance(raw, dict) or not isinstance(raw.get("domains"), dict):
            return {}

        domains: dict[str, dict[str, ToolStats]] = {}
        for domain, raw_tools in raw["domains"].items():
            if not isinstance(domain, str) or not isinstance(raw_tools, dict):
                continue
            tools: dict[str, ToolStats] = {}
            for name, values in raw_tools.items():
                if not isinstance(name, str) or not isinstance(values, dict):
                    continue
                successes = values.get("successes")
                failures = values.get("failures")
                latency = values.get("total_latency_ms")
                if (
                    isinstance(successes, int)
                    and isinstance(failures, int)
                    and isinstance(latency, (int, float))
                    and successes >= 0
                    and failures >= 0
                ):
                    tools[name] = ToolStats(
                        successes=successes,
                        failures=failures,
                        total_latency_ms=latency,
                    )
            if tools:
                domains[domain] = tools
        return domains

    def _save(self) -> None:
        payload = {
            "version": 1,
            "domains": {
                domain: {
                    name: {
                        "successes": stats.successes,
                        "failures": stats.failures,
                        "total_latency_ms": stats.total_latency_ms,
                    }
                    for name, stats in tools.items()
                }
                for domain, tools in self._domains.items()
            },
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.",
            dir=self._path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_error:
                logger.debug("Could not remove %s: %s", temporary, cleanup_error)
            raise
