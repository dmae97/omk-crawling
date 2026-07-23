"""Tool registry — all adapters in one place."""

from __future__ import annotations

from omk_crawl.tools.autoscraper_tool import AutoscraperTool
from omk_crawl.tools.base import BaseTool
from omk_crawl.tools.browser_use_tool import BrowserUseTool
from omk_crawl.tools.crawl4ai_tool import Crawl4aiTool
from omk_crawl.tools.curl_cffi_tool import CurlCffiTool
from omk_crawl.tools.markitdown_tool import MarkitdownTool
from omk_crawl.tools.scrapling_tool import ScraplingTool

# Escalation order: lightest → heaviest
ESCALATION_CHAIN: list[type[BaseTool]] = [
    CurlCffiTool,   # ① TLS fingerprint, no browser, instant
    Crawl4aiTool,   # ② Browser render + Markdown
    ScraplingTool,  # ③ Stealth browser + anti-bot bypass
    BrowserUseTool, # ④ LLM agent drives browser (last resort)
]

ALL_TOOLS: dict[str, type[BaseTool]] = {
    "curl_cffi": CurlCffiTool,
    "crawl4ai": Crawl4aiTool,
    "scrapling": ScraplingTool,
    "browser_use": BrowserUseTool,
    "autoscraper": AutoscraperTool,
    "markitdown": MarkitdownTool,
}


def get_tool(name: str, **kwargs) -> BaseTool:
    """Instantiate a tool by name."""
    cls = ALL_TOOLS.get(name)
    if cls is None:
        raise ValueError(f"Unknown tool: {name}. Available: {list(ALL_TOOLS)}")
    return cls(**kwargs)


def escalation_tools(**kwargs) -> list[BaseTool]:
    """Instantiate the escalation chain (only available tools)."""
    tools = []
    for cls in ESCALATION_CHAIN:
        t = cls()
        if t.available():
            tools.append(t)
    return tools
