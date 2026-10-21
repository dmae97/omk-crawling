"""Sitemap discovery and parsing — zero-dependency, offline-testable.

Discovers sitemaps via robots.txt ``Sitemap:`` lines (authoritative when
present), falling back to the conventional paths (``/sitemap.xml``,
``/sitemap_index.xml``, ``/sitemap.txt``). Parses XML urlsets, sitemap
indexes (recursively, bounded) and plain-text URL lists.

Security: parsing uses stdlib ``xml.etree.ElementTree``, which does not
resolve external entities; the default fetcher caps document size.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

MAX_DOC_BYTES = 8 * 1024 * 1024  # refuse to parse more than 8 MiB of sitemap

_FALLBACK_PATHS = ("/sitemap.xml", "/sitemap_index.xml", "/sitemap.txt")

_ROBOTS_SITEMAP_RE = re.compile(r"^\s*sitemap\s*:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class SitemapEntry:
    """One crawlable URL advertised by a sitemap."""

    loc: str
    lastmod: str | None = None
    changefreq: str | None = None
    priority: float | None = None


def _localname(tag: str) -> str:
    """Namespace-agnostic tag name: ``{ns}urlset`` -> ``urlset``."""
    return tag.rpartition("}")[2].lower()


def _parse_priority(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_sitemap(text: str) -> tuple[list[SitemapEntry], list[str]]:
    """Parse one sitemap document into (entries, child index URLs).

    Accepts XML urlset / sitemapindex documents and plain-text URL lists.
    Malformed input yields ``([], [])`` — a broken sitemap never raises.
    """
    if not text or not text.strip():
        return [], []
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return _parse_text_list(text), []
    root_name = _localname(root.tag)
    if root_name not in {"urlset", "sitemapindex"}:
        return _parse_text_list(text), []
    entries: list[SitemapEntry] = []
    children: list[str] = []
    for element in root:
        if _localname(element.tag) not in {"url", "sitemap"}:
            continue
        loc: str | None = None
        lastmod = changefreq = None
        priority: float | None = None
        for child in element:
            name = _localname(child.tag)
            value = (child.text or "").strip()
            if name == "loc" and value:
                loc = value
            elif name == "lastmod" and value:
                lastmod = value
            elif name == "changefreq" and value:
                changefreq = value
            elif name == "priority":
                priority = _parse_priority(value)
        if loc:
            if root_name == "sitemapindex":
                children.append(loc)
            else:
                entries.append(
                    SitemapEntry(loc=loc, lastmod=lastmod, changefreq=changefreq, priority=priority)
                )
    return entries, children


def _parse_text_list(text: str) -> list[SitemapEntry]:
    """A ``sitemap.txt``-style plain list of absolute URLs."""
    entries: list[SitemapEntry] = []
    for line in text.splitlines():
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        parts = urlsplit(url)
        if parts.scheme in ("http", "https") and parts.netloc:
            entries.append(SitemapEntry(loc=url))
    return entries


def parse_robots_sitemaps(robots_text: str) -> list[str]:
    """Extract absolute ``Sitemap:`` URLs from robots.txt content."""
    found: list[str] = []
    for match in _ROBOTS_SITEMAP_RE.finditer(robots_text or ""):
        url = match.group(1)
        if url not in found:
            found.append(url)
    return found


def default_fetch(url: str, timeout: float = 10.0) -> str | None:
    """Fetch a sitemap document over http(s) with a hard size cap.

    Injectable everywhere; tests never touch the network. Returns ``None``
    on any transport problem — discovery degrades, never raises.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    from urllib.request import Request, urlopen

    request = Request(url, headers={"User-Agent": "omk-crawl/2.15 (+sitemap discovery)"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme forced above
            data = response.read(MAX_DOC_BYTES + 1)
    except OSError:
        return None
    if len(data) > MAX_DOC_BYTES:
        data = data[:MAX_DOC_BYTES]
    return data.decode("utf-8", errors="replace")


def discover(
    base_url: str,
    fetch: Callable[[str], str | None] | None = None,
    max_documents: int = 8,
) -> list[SitemapEntry]:
    """Find and flatten every sitemap reachable from ``base_url``.

    robots.txt ``Sitemap:`` lines win when present; otherwise the
    conventional paths are probed in order. Sitemap indexes are expanded
    breadth-first, bounded by ``max_documents`` sitemap fetches (robots.txt
    excluded from the budget). Results are deduplicated in discovery order.
    """
    if max_documents < 1:
        raise ValueError("max_documents must be >= 1")
    get = fetch or default_fetch
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return []
    origin = f"{parts.scheme}://{parts.netloc}"

    seen_docs: set[str] = set()
    queue: list[str] = []
    robots_body = get(f"{origin}/robots.txt")
    if robots_body is not None:
        # robots.txt Sitemap values may be relative; absolutize against the origin.
        base = f"{origin}/"
        queue = [urljoin(base, candidate) for candidate in parse_robots_sitemaps(robots_body)]
    if not queue:
        queue = [f"{origin}{path}" for path in _FALLBACK_PATHS]

    entries: list[SitemapEntry] = []
    seen_locs: set[str] = set()
    docs_fetched = 0
    while queue and docs_fetched < max_documents:
        doc_url = queue.pop(0)
        if doc_url in seen_docs:
            continue
        seen_docs.add(doc_url)
        body = get(doc_url)
        docs_fetched += 1
        if body is None:
            continue
        page_entries, children = parse_sitemap(body)
        for entry in page_entries:
            loc = entry.loc
            if loc not in seen_locs:
                seen_locs.add(loc)
                entries.append(entry)
        for child in children:
            absolute = urljoin(doc_url, child)
            if absolute not in seen_docs and absolute not in queue:
                queue.append(absolute)
    return entries
