"""browser-use adapter — LLM agent drives a real browser (last resort).

Cost guardrails (reviewer Phase 5): the LLM path is the most expensive tool, so
it is bounded by ``max_steps``, ``deadline_s`` and ``max_cost_usd``, excluded
from the chain entirely when no LLM key is configured, and reports a failure
taxonomy (nav / login / timeout / model / unknown) instead of a bare exception.
"""

from __future__ import annotations

import os
from typing import Any

from omk_crawl.result import CrawlResult, CrawlStatus, _timer
from omk_crawl.tools.base import BaseTool

# LLM providers browser-use can drive. If none is configured the tool cannot
# run, so we exclude it from the escalation chain rather than burn the budget.
_LLM_KEY_VARS = (
    "BROWSER_USE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
)


class BrowserUseTool(BaseTool):
    name = "browser_use"
    pip_package = "browser-use"
    layer = 2
    needs_browser = True
    needs_llm = True
    capabilities = frozenset({"js_render", "markdown"})

    def __init__(
        self,
        model: str = "bu-2-0",
        max_steps: int = 25,
        max_cost_usd: float | None = None,
        deadline_s: float = 120.0,
    ) -> None:
        self.model = model
        self.max_steps = max_steps
        self.max_cost_usd = max_cost_usd
        self.deadline_s = deadline_s

    # --- availability gate ---------------------------------------------------
    def _has_llm_key(self) -> bool:
        return any(os.environ.get(v) for v in _LLM_KEY_VARS)

    def available(self) -> bool:
        """Importable AND an LLM key is configured (else it can never succeed)."""
        return super().available() and self._has_llm_key()

    # --- failure taxonomy ----------------------------------------------------
    @staticmethod
    def _classify_failure(exc: Exception) -> str:
        msg = f"{type(exc).__name__}: {exc}".lower()
        if "timeout" in msg or "timed out" in msg or "deadline" in msg:
            return "timeout"
        if "login" in msg or "auth" in msg or "sign in" in msg:
            return "login"
        if "navigate" in msg or "net::" in msg or "dns" in msg or "connection" in msg:
            return "nav"
        if "model" in msg or "llm" in msg or "rate" in msg or "token" in msg:
            return "model"
        return "unknown"

    # --- fetch ---------------------------------------------------------------
    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        if not self.available():
            r = self._missing(url)
            if super().available() and not self._has_llm_key():
                r.error = "browser_use: no LLM API key configured (excluded)"
            return r

        # Dry-run: report the guardrails without spending anything.
        if kwargs.get("dry_run"):
            return CrawlResult(
                url=url, status=CrawlStatus.ERROR, tool=self.name,
                error="dry-run: browser-use not executed",
                metadata={
                    "dry_run": True, "model": self.model,
                    "max_steps": self.max_steps, "max_cost_usd": self.max_cost_usd,
                    "deadline_s": self.deadline_s,
                },
            )

        import asyncio

        try:
            return asyncio.run(self.fetch_async(url, **kwargs))
        except RuntimeError:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(
                    lambda: asyncio.run(self.fetch_async(url, **kwargs)),
                ).result()

    async def fetch_async(self, url: str, **kwargs: Any) -> CrawlResult:
        if not self.available():
            return self._missing(url)
        _, stop = _timer()
        import asyncio

        try:
            from browser_use import Agent, ChatBrowserUse

            task = kwargs.get(
                "task",
                f"Navigate to {url} and extract the full page content as text.",
            )
            agent = Agent(task=task, llm=ChatBrowserUse(model=self.model))

            # Time cap: abort the whole agent run past the deadline.
            coro = agent.run(max_steps=kwargs.get("max_steps", self.max_steps))
            history = await asyncio.wait_for(
                coro, timeout=kwargs.get("deadline_s", self.deadline_s),
            )

            content = history.final_result() or ""

            # Cost cap: browser-use tracks cumulative cost on the history.
            cost = getattr(history, "total_cost", None)
            if (
                self.max_cost_usd is not None and cost is not None
                and cost > self.max_cost_usd
            ):
                return CrawlResult(
                    url=url, status=CrawlStatus.ERROR, tool=self.name,
                    elapsed_ms=stop(),
                    error=f"cost cap exceeded: ${cost:.4f} > ${self.max_cost_usd}",
                    metadata={"failure_class": "model", "cost_usd": cost},
                )

            meta = {
                "model": self.model, "agent": True,
                "max_steps": self.max_steps, "deadline_s": self.deadline_s,
            }
            if cost is not None:
                meta["cost_usd"] = cost
            meta.update(self.contract_metadata(kwargs))
            return CrawlResult(
                url=url,
                status=CrawlStatus.OK if content else CrawlStatus.ERROR,
                tool=self.name,
                markdown=content,
                elapsed_ms=stop(),
                metadata=meta,
            )
        except Exception as exc:
            r = self._error(url, exc)
            r.elapsed_ms = stop()
            r.metadata["failure_class"] = self._classify_failure(exc)
            return r
