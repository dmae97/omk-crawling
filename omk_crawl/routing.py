"""Detection-aware routing — pick the next tool from *what* is blocking us.

The default escalation chain is a fixed ladder (lightest → heaviest). Once we
detect the blocking mechanism, we can reorder the remaining rungs so the tool
most likely to succeed is tried next — instead of climbing one rung at a time.
That is the difference between a ladder and a router.

The router calls :func:`preferred_order` after a failed attempt. Tools whose
names appear in the block-type preference list move to the front (in that
order); every other available tool keeps its relative position behind them.
The function is therefore safe for arbitrary tool names (including test
mocks): unknown tools simply retain their original order, and no tool is ever
dropped.
"""

from __future__ import annotations

from omk_crawl.detect import BlockType

# Default ladder, lightest → heaviest.
DEFAULT_ORDER: list[str] = ["curl_cffi", "crawl4ai", "scrapling", "browser_use"]

# BlockType → preferred tool order. Rationale:
#   TLS_FINGERPRINT → a different curl_cffi impersonation often passes; real
#                     browsers (scrapling / crawl4ai) always clear TLS.
#   JS_REQUIRED     → the cheapest renderer (crawl4ai) executes JavaScript.
#   CLOUDFLARE/WAF  → scrapling's stealth browser is built for anti-bot walls;
#                     fall back to crawl4ai, then the LLM agent.
#   RATE_LIMIT      → not a tool problem; keep the ladder (caller backs off).
#   AUTH_REQUIRED   → we do NOT bypass auth; empty list = no escalation.
ROUTE_TABLE: dict[BlockType, list[str]] = {
    BlockType.TLS_FINGERPRINT: ["curl_cffi", "scrapling", "crawl4ai", "browser_use"],
    BlockType.JS_REQUIRED: ["crawl4ai", "scrapling", "browser_use"],
    BlockType.CLOUDFLARE: ["scrapling", "crawl4ai", "browser_use"],
    BlockType.WAF: ["scrapling", "crawl4ai", "browser_use"],
    BlockType.RATE_LIMIT: DEFAULT_ORDER,
    BlockType.AUTH_REQUIRED: [],
}

# Priority for resolving combined (flag-OR'd) block types. AUTH first so we
# never escalate into an auth bypass; then the hardest anti-bot signals.
_PRIORITY: tuple[BlockType, ...] = (
    BlockType.AUTH_REQUIRED,
    BlockType.CLOUDFLARE,
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
