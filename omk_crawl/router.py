"""SmartRouter — auto-detect blocking, escalate across tools until success.

The core innovation: you give it a URL, it figures out what's needed.

    curl_cffi (0ms browser) → crawl4ai (render) → scrapling (stealth) → browser-use (LLM)

Each step:
  1. Fetch with current tool
  2. Analyze response (detect.py)
  3. If OK → return
  4. If blocked → escalate to next tool
  5. If all fail → return best attempt + diagnosis
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from omk_crawl.detect import detect_block, missing_tools
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.tools import ESCALATION_CHAIN, get_tool
from omk_crawl.tools.base import BaseTool

logger = logging.getLogger("omk_crawl")


@dataclass
class RouteDecision:
    """Why the router picked a tool."""

    tool: str
    reason: str
    attempt: int
    detection: str = ""


@dataclass
class SmartRouter:
    """Auto-routing crawl engine with escalation.

    Usage:
        router = SmartRouter()
        result = router.crawl("https://example.com")
        print(result.summary())
    """

    # Tools to try, in escalation order. None = auto (all available).
    tools: list[str] | None = None
    # Stop escalating after this many attempts
    max_attempts: int = 4
    # Verbose logging
    verbose: bool = False
    # Extra kwargs passed to each tool
    tool_kwargs: dict[str, Any] = field(default_factory=dict)
    # History of attempts
    history: list[CrawlResult] = field(default_factory=list)
    decisions: list[RouteDecision] = field(default_factory=list)

    def _get_chain(self) -> list[BaseTool]:
        if self.tools is not None:
            try:
                return [get_tool(name) for name in self.tools]
            except ValueError as exc:
                logger.warning("%s", exc)
                return []
        chain = []
        for cls in ESCALATION_CHAIN:
            t = cls()
            if t.available():
                chain.append(t)
        return chain

    def crawl(self, url: str, **kwargs: Any) -> CrawlResult:
        """Synchronous crawl with auto-escalation."""
        merged = {**self.tool_kwargs, **kwargs}
        chain = self._get_chain()

        if not chain:
            missing = missing_tools()
            return CrawlResult(
                url=url,
                status=CrawlStatus.TOOL_MISSING,
                error=(
                    "No crawling tools installed."
                    f" Try: pip install curl_cffi  (missing: {missing})"
                ),
            )

        best: CrawlResult | None = None

        for i, tool in enumerate(chain[: self.max_attempts]):
            self._log(f"[{i + 1}/{len(chain)}] Trying {tool.name}...")
            result = tool.fetch(url, **merged)
            self.history.append(result)

            # Run detection on the result for routing decisions
            det = detect_block(result.html, result.status_code)
            result.metadata.setdefault("detection", det.detail)
            result.metadata["block_type"] = det.block.name

            self.decisions.append(
                RouteDecision(
                    tool=tool.name,
                    reason=self._escalation_reason(result),
                    attempt=i + 1,
                    detection=det.detail,
                )
            )

            if result.ok:
                self._log(f"  ✓ {tool.name} succeeded ({result.elapsed_ms:.0f}ms)")
                self._ensure_markdown(result)
                return result

            detail = result.error or det.detail
            self._log(
                f"  ✗ {tool.name}: {result.status.value} — {detail}"
            )

            # Keep best attempt for fallback
            if best is None or self._score(result) > self._score(best):
                best = result

            # Don't escalate if it's a hard error (not a block)
            if result.status is CrawlStatus.ERROR and not result.blocked:
                self._log("  Hard error, stopping escalation.")
                break

        # All tools failed — return best attempt
        if best:
            best.metadata["escalation_exhausted"] = True
            best.metadata["attempts"] = len(self.history)
            self._ensure_markdown(best)
            return best

        return CrawlResult(url=url, status=CrawlStatus.ERROR, error="No tools available")

    async def crawl_async(self, url: str, **kwargs: Any) -> CrawlResult:
        """Async crawl with auto-escalation."""
        merged = {**self.tool_kwargs, **kwargs}
        chain = self._get_chain()

        if not chain:
            return CrawlResult(
                url=url,
                status=CrawlStatus.TOOL_MISSING,
                error="No crawling tools installed. pip install curl_cffi",
            )

        best: CrawlResult | None = None

        for i, tool in enumerate(chain[: self.max_attempts]):
            result = await tool.fetch_async(url, **merged)
            self.history.append(result)

            det = detect_block(result.html, result.status_code)
            result.metadata.setdefault("detection", det.detail)
            result.metadata["block_type"] = det.block.name

            if result.ok:
                self._ensure_markdown(result)
                return result

            if best is None or self._score(result) > self._score(best):
                best = result

            if result.status is CrawlStatus.ERROR and not result.blocked:
                break

        if best:
            best.metadata["escalation_exhausted"] = True
            self._ensure_markdown(best)
            return best

        return CrawlResult(url=url, status=CrawlStatus.ERROR, error="No tools available")

    def diagnose(self, url: str) -> dict[str, Any]:
        """Dry-run: check what tools are available and what we'd try."""
        chain = self._get_chain()
        return {
            "url": url,
            "available_tools": [t.name for t in chain],
            "missing_tools": missing_tools(),
            "escalation_order": [t.name for t in chain[: self.max_attempts]],
            "install_hint": "pip install omk-crawl[all]",
        }

    @staticmethod
    def _score(r: CrawlResult) -> float:
        """Score a result for 'best attempt' selection."""
        s = 0.0
        if r.html:
            s += len(r.html)
        if r.markdown:
            s += len(r.markdown) * 2
        if r.status_code and 200 <= r.status_code < 300:
            s += 10000
        return s

    @staticmethod
    def _escalation_reason(r: CrawlResult) -> str:
        if r.ok:
            return "success"
        if r.status is CrawlStatus.TLS_BLOCKED:
            return "TLS fingerprint blocked → need browser/stealth"
        if r.status is CrawlStatus.BLOCKED:
            return "blocked (WAF/anti-bot) → escalate"
        if r.status is CrawlStatus.JS_REQUIRED:
            return "JS rendering needed → escalate to browser"
        return r.error or "failed"

    @staticmethod
    def _ensure_markdown(r: CrawlResult) -> None:
        """Convert HTML to markdown if the tool didn't provide it.

        Strips <script>/<style> blocks and unescapes HTML entities.
        If markitdown is available, uses it for proper conversion.
        Otherwise falls back to tag-stripping. If the result is trivial
        (empty after stripping), leaves markdown as None so that
        Pipeline.to_markdown() can handle it later.
        """
        if r.markdown or not r.html:
            return
        import html as html_mod
        import re

        text = re.sub(r"<script[^>]*>.*?</script>", "", r.html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html_mod.unescape(text).strip()
        if text:
            r.markdown = text
            r.metadata.setdefault("markdown_fallback", "tag-strip")

    def _log(self, msg: str) -> None:
        if self.verbose:
            logger.info(msg)


# --- Module-level convenience ---

def crawl(
    url: str, *, tool: str | None = None, verbose: bool = False, **kwargs: Any,
) -> CrawlResult:
    """One-liner crawl with auto-escalation.

    >>> from omk_crawl import crawl
    >>> r = crawl("https://example.com")
    >>> print(r.markdown)
    """
    router = SmartRouter(
        tools=[tool] if tool else None,
        verbose=verbose,
    )
    return router.crawl(url, **kwargs)


async def crawl_async(url: str, *, tool: str | None = None, **kwargs: Any) -> CrawlResult:
    """Async one-liner."""
    router = SmartRouter(tools=[tool] if tool else None)
    return await router.crawl_async(url, **kwargs)
