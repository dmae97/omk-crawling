"""Pipeline — compose fetch → extract → convert steps."""

from __future__ import annotations

import html as html_module
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from omk_crawl.detect import BlockType, Detection, detect_block, detection_to_status
from omk_crawl.result import CrawlResult, CrawlStatus

if TYPE_CHECKING:
    from omk_crawl.router import SmartRouter


_HIDDEN_HTML = re.compile(
    r"<(?:script|style|noscript|svg)\b[^>]*>.*?</(?:script|style|noscript|svg)>",
    re.IGNORECASE | re.DOTALL,
)
_TAG = re.compile(r"<[^>]+>")
_PASSWORD_INPUT = re.compile(
    r"<input\b[^>]*\btype\s*=\s*['\"]?password\b",
    re.IGNORECASE,
)
_SHELL_MARKERS = ('id="root"', "id='root'", 'id="app"', "id='app'", "__next_data__")
_AUTH_MARKERS = ("log in", "sign in", "sign up", "create an account")
_AUTH_WALL_PHRASES = (
    "log in to continue",
    "sign in to continue",
    "authentication required",
    "login required",
    "sign in to view",
    "log in to view",
    "must sign in",
    "must log in",
)
_CHALLENGE_MARKERS = (
    "checking your browser",
    "verify you are human",
    "are you a robot",
    "unusual traffic",
    "challenge-platform",
    "cf_chl",
    "datadome-captcha",
    "press & hold",
    "<title>access denied",
    "complete the captcha",
    "captcha challenge",
    "request blocked",
    "you have been blocked",
)


def _visible_text(result: CrawlResult) -> str:
    markdown = result.fit_markdown or result.markdown
    if markdown:
        return " ".join(markdown.split())
    source = _HIDDEN_HTML.sub(" ", result.html or "")
    return " ".join(html_module.unescape(_TAG.sub(" ", source)).split())


def _is_auth_wall(result: CrawlResult, visible: str) -> bool:
    visible_lower = visible.lower()
    auth_markers = sum(marker in visible_lower for marker in _AUTH_MARKERS)
    remainder = visible_lower
    for marker in _AUTH_MARKERS:
        remainder = remainder.replace(marker, " ")
    other_words = len(re.findall(r"\b[\w'-]+\b", remainder))
    password_wall = bool(_PASSWORD_INPUT.search(result.html or "")) and other_words < 12
    explicit_prompt = any(phrase in visible_lower for phrase in _AUTH_WALL_PHRASES)
    return len(visible) < 1000 and (
        password_wall
        or (auth_markers >= 2 and other_words < 8)
        or (explicit_prompt and other_words < 20)
    )


def _is_script_shell(result: CrawlResult, probe: str, visible: str) -> bool:
    if result.extracted:
        return False
    if not probe.strip():
        return True
    html_lower = (result.html or "").lower()
    known_shell = any(marker in html_lower for marker in _SHELL_MARKERS)
    script_only = "<script" in html_lower and not visible
    return (known_shell or script_only) and len(visible) < 80


def normalize_result(result: CrawlResult) -> Detection:
    """Reject false successes before routing decisions and site learning."""
    probe = result.html or result.fit_markdown or result.markdown or ""
    detection = detect_block(probe, result.status_code)
    if not result.ok:
        return detection

    visible = _visible_text(result)
    if _is_auth_wall(result, visible):
        result.status = CrawlStatus.BLOCKED
        return Detection(
            block=BlockType.AUTH_REQUIRED,
            status_code=result.status_code,
            confidence=0.95,
            detail="Authentication wall detected",
        )

    if _is_script_shell(result, probe, visible):
        result.status = CrawlStatus.JS_REQUIRED
        return Detection(
            block=BlockType.JS_REQUIRED,
            status_code=result.status_code,
            needs_browser=True,
            confidence=0.9,
            detail="Empty or script-only page",
        )

    lower = probe.lower()
    challenge = any(marker in lower for marker in _CHALLENGE_MARKERS)
    if challenge and detection.block == BlockType.NONE:
        detection = Detection(
            block=BlockType.WAF,
            status_code=result.status_code,
            needs_stealth=True,
            confidence=0.8,
            detail="Challenge page detected",
        )
    if (result.status_code or 0) >= 400 or challenge:
        result.status = detection_to_status(detection)
        return detection
    if detection.block != BlockType.NONE:
        return Detection(
            status_code=result.status_code,
            confidence=1.0,
            detail="Content quality passed",
        )
    return detection


def _new_router() -> SmartRouter:
    from omk_crawl.router import SmartRouter

    return SmartRouter()


def _convert_with_markitdown(source: str) -> str | None:
    try:
        import tempfile

        from markitdown import MarkItDown

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "input.html")
            path.write_text(source, encoding="utf-8")
            text = MarkItDown().convert(path).text_content
            return text if text and text.strip() else None
    except Exception:
        return None


def ensure_markdown(result: CrawlResult) -> None:
    """Populate markdown from HTML when a crawler did not provide it."""
    if result.markdown or not result.html:
        return

    markdown = _convert_with_markitdown(result.html)
    if markdown:
        result.markdown = markdown
        result.metadata.setdefault("markdown_source", "markitdown")
        return

    import html as html_mod
    import re

    text = re.sub(
        r"<script[^>]*>.*?</script>",
        "",
        result.html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(
        r"<style[^>]*>.*?</style>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text).strip()
    if text:
        result.markdown = text
        result.metadata.setdefault("markdown_source", "tag-strip")


@dataclass
class Pipeline:
    """Composable crawl pipeline.

    Usage:
        p = Pipeline()
        p.fetch()                          # auto-escalating fetch
        p.extract_css("div.product", {     # CSS extraction
            "title": "h2", "price": ".price"
        })
        p.to_markdown()                    # ensure markdown output
        result = p.run("https://example.com")
    """

    steps: list[Callable[[CrawlResult], CrawlResult]] = field(default_factory=list)
    router: SmartRouter = field(default_factory=_new_router)

    def fetch(self, tool: str | None = None, **kwargs: Any) -> Pipeline:
        """Step 1: fetch with auto-escalation (or specific tool)."""

        def _fetch(r: CrawlResult) -> CrawlResult:
            if tool:
                self.router.tools = [tool]
            return self.router.crawl(r.url, **kwargs)

        self.steps.append(_fetch)
        return self

    def extract_css(self, base: str, fields: dict[str, str]) -> Pipeline:
        """Step 2: CSS extraction on HTML (requires selectolax)."""

        def _extract(r: CrawlResult) -> CrawlResult:
            if not r.html:
                r.error = "No HTML to extract from"
                return r
            try:
                parser_module = import_module("selectolax.parser")
                tree = parser_module.HTMLParser(r.html)
                rows = []
                for node in tree.css(base):
                    row = {}
                    for name, sel in fields.items():
                        child = node.css_first(sel)
                        row[name] = child.text(strip=True) if child else None
                    rows.append(row)
                r.extracted = rows
                r.metadata["extract_count"] = len(rows)
            except ImportError:
                # selectolax not installed — no extraction possible
                r.metadata["extract_note"] = "pip install selectolax for CSS extraction"
            return r

        self.steps.append(_extract)
        return self

    def to_markdown(self) -> Pipeline:
        """Step 3: ensure markdown output."""

        def _convert(r: CrawlResult) -> CrawlResult:
            ensure_markdown(r)
            return r

        self.steps.append(_convert)
        return self

    def run(self, url: str) -> CrawlResult:
        """Execute the pipeline."""
        result = CrawlResult(url=url, status=CrawlStatus.OK)
        for step in self.steps:
            result = step(result)
            if result.status is CrawlStatus.ERROR:
                break
        return result
