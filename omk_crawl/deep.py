"""Deep crawl — bounded BFS site traversal on top of SmartRouter escalation.

Design rules:

* Every page fetch goes through :class:`~omk_crawl.router.SmartRouter`, so
  per-page anti-bot escalation, per-domain politeness and robots.txt all
  keep their single source of truth. The deep crawler adds no second
  fetching path.
* Budgets are explicit: ``max_pages`` (fetch budget) and ``max_depth``
  (link hops from the seed). The stop reason is always reported.
* Zero dependencies: link extraction uses ``html.parser``, so the core
  package's zero-dep promise holds.
* Optional sitemap seeding: robots.txt-discovered sitemap URLs enter the
  frontier at depth 0 alongside the seed.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

from omk_crawl.detect import check_robots_txt
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.router import SmartRouter
from omk_crawl.sitemap import discover as discover_sitemaps

_ASSET_SUFFIX_RE = re.compile(
    r"\.(?:jpg|jpeg|png|gif|webp|svg|ico|bmp|tiff?|avif|"
    r"css|js|mjs|map|json|xml|rss|atom|"
    r"woff2?|ttf|eot|otf|"
    r"mp[34]|m4a|webm|ogg|wav|avi|mov|"
    r"zip|gz|tgz|tar|rar|7z|dmg|exe|msi|apk|ipa|"
    r"pdf|docx?|xlsx?|pptx?)$",
    re.IGNORECASE,
)

_NON_PAGE_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "ftp:")


class _LinkExtractor(HTMLParser):
    """Collect hrefs from ``<a>`` tags; tolerant of broken markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def normalize_url(url: str) -> str | None:
    """Canonical page URL: lowercase scheme/host, default port stripped,
    fragment dropped, userinfo dropped. Returns ``None`` for non-page URLs.
    """
    if not url:
        return None
    url = url.strip()
    if url.startswith(_NON_PAGE_SCHEMES) or url.startswith("#"):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port  # may raise ValueError for malformed ports
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    scheme = parts.scheme.lower()
    netloc = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80
    if port is not None and port != default_port:
        netloc = f"{netloc}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def extract_links(html: str, base_url: str) -> list[str]:
    """Absolute, normalized, order-preserving deduplicated links from HTML."""
    parser = _LinkExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - broken markup must never fail a page
        pass
    seen: set[str] = set()
    links: list[str] = []
    for href in parser.hrefs:
        from urllib.parse import urljoin

        absolute = normalize_url(urljoin(base_url, href.strip()))
        if absolute and absolute not in seen:
            seen.add(absolute)
            links.append(absolute)
    return links


def is_crawlable(url: str) -> bool:
    """True for http(s) page URLs — assets and non-web schemes excluded."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    path = parts.path or "/"
    if _ASSET_SUFFIX_RE.search(path):
        return False
    return True


def _domain_of(host: str) -> str:
    """Zero-dep registrable-domain approximation: last two labels.

    Good enough for scope gating; multi-label public suffixes (``co.uk``)
    over-couple by design — use ``--include``/``--exclude`` for precision.
    """
    labels = host.rstrip(".").split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def same_scope(seed_url: str, candidate: str, same_domain: bool = False) -> bool:
    """Same-origin (default) or same-domain scope check."""
    seed_parts, cand_parts = urlsplit(seed_url), urlsplit(candidate)
    if same_domain:
        return _domain_of(cand_parts.hostname or "") == _domain_of(seed_parts.hostname or "")
    return seed_parts.scheme == cand_parts.scheme and seed_parts.netloc == cand_parts.netloc


@dataclass(frozen=True)
class DeepCrawlConfig:
    """Explicit budgets and scope for a site crawl."""

    max_pages: int = 20
    max_depth: int = 2
    same_domain: bool = False
    include: tuple[str, ...] = ()  # regexes; a URL must match at least one
    exclude: tuple[str, ...] = ()  # regexes; any match skips the URL
    use_sitemap: bool = False
    sitemap_max_documents: int = 8
    respect_robots: bool = True

    def __post_init__(self) -> None:
        if type(self.max_pages) is not int or self.max_pages < 1:
            raise ValueError("max_pages must be an integer >= 1")
        if type(self.max_depth) is not int or self.max_depth < 0:
            raise ValueError("max_depth must be an integer >= 0")
        if type(self.sitemap_max_documents) is not int or self.sitemap_max_documents < 1:
            raise ValueError("sitemap_max_documents must be an integer >= 1")
        for name in ("respect_robots", "same_domain", "use_sitemap"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        for name in ("include", "exclude"):
            for pattern in getattr(self, name):
                try:
                    re.compile(pattern)
                except re.error as error:
                    msg = f"{name} pattern {pattern!r} is not a valid regex: {error}"
                    raise ValueError(msg) from None


@dataclass
class DeepCrawlStats:
    """Counters for one run. Every skipped URL lands in exactly one bucket."""

    pages_fetched: int = 0
    pages_ok: int = 0
    pages_failed: int = 0
    links_found: int = 0  # links extracted for frontier consideration (pages within link depth)
    sitemap_seeds: int = 0
    robots_skipped: int = 0
    filtered: int = 0
    pending: int = 0  # frontier entries left unfetched (budget/depth stop)


@dataclass
class DeepCrawlResult:
    """Outcome of one site crawl: pages in fetch order + stats + stop reason."""

    seed: str
    pages: list[CrawlResult] = field(default_factory=list)
    stats: DeepCrawlStats = field(default_factory=DeepCrawlStats)
    stop_reason: str = "frontier_empty"

    def summary(self) -> str:
        s = self.stats
        return (
            f"deep crawl {self.seed}: {s.pages_ok}/{s.pages_fetched} ok, "
            f"{s.pages_failed} failed, {s.links_found} links, "
            f"{s.pending} pending, stop={self.stop_reason}"
        )

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "stop_reason": self.stop_reason,
            "stats": asdict(self.stats),
            "pages": [
                {
                    "url": page.url,
                    "status": page.status.value,
                    "tool": page.tool,
                    "status_code": page.status_code,
                    "elapsed_ms": round(page.elapsed_ms, 1),
                    "content_chars": len(page.content or ""),
                    "markdown": page.content,
                }
                for page in self.pages
            ],
        }


def deep_crawl(
    seed_url: str,
    config: DeepCrawlConfig | None = None,
    *,
    router: SmartRouter | None = None,
    fetch: Callable[[str], str | None] | None = None,
) -> DeepCrawlResult:
    """Crawl a site breadth-first from ``seed_url`` within explicit budgets.

    ``router`` defaults to a fresh :class:`SmartRouter` (its per-domain rate
    limiter and robots check govern every fetch). ``fetch`` is the sitemap
    document fetcher (injectable for tests).
    """
    cfg = config or DeepCrawlConfig()
    if router is None:
        from omk_crawl.router import SmartRouter

        router = SmartRouter(respect_robots=cfg.respect_robots)

    seed = normalize_url(seed_url)
    if seed is None:
        raise ValueError(f"deep crawl requires an absolute http(s) URL, got {seed_url!r}")

    include_res = [re.compile(p) for p in cfg.include]
    exclude_res = [re.compile(p) for p in cfg.exclude]
    stats = DeepCrawlStats()

    def allowed(url: str) -> bool:
        if not is_crawlable(url) or not same_scope(seed, url, cfg.same_domain):
            stats.filtered += 1
            return False
        if any(rx.search(url) for rx in exclude_res):
            stats.filtered += 1
            return False
        if include_res and not any(rx.search(url) for rx in include_res):
            stats.filtered += 1
            return False
        return True

    robots_cache: dict[str, bool] = {}

    def robots_ok(url: str) -> bool:
        if not cfg.respect_robots:
            return True
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        if key not in robots_cache:
            robots_cache[key] = check_robots_txt(url)
        return robots_cache[key]

    visited: set[str] = {seed}
    frontier: deque[tuple[str, int]] = deque([(seed, 0)])

    if cfg.use_sitemap:
        for entry in discover_sitemaps(seed, fetch=fetch, max_documents=cfg.sitemap_max_documents):
            url = normalize_url(entry.loc)
            if url and url not in visited and allowed(url):
                visited.add(url)
                frontier.append((url, 0))
                stats.sitemap_seeds += 1

    pages: list[CrawlResult] = []
    stop_reason = "frontier_empty"
    while frontier:
        if stats.pages_fetched >= cfg.max_pages:
            stop_reason = "max_pages"
            break
        url, depth = frontier.popleft()
        if not robots_ok(url):
            stats.robots_skipped += 1
            continue
        try:
            result = router.crawl(url)
        except Exception as error:  # noqa: BLE001 - one page failing never stops the crawl
            result = CrawlResult(url=url, status=CrawlStatus.ERROR, error=str(error))
        stats.pages_fetched += 1
        pages.append(result)
        if result.ok:
            stats.pages_ok += 1
        else:
            stats.pages_failed += 1
        if result.ok and result.html and depth < cfg.max_depth:
            for link in extract_links(result.html, url):
                stats.links_found += 1
                if link not in visited and allowed(link):
                    visited.add(link)
                    frontier.append((link, depth + 1))

    stats.pending = len(frontier)
    return DeepCrawlResult(seed=seed, pages=pages, stats=stats, stop_reason=stop_reason)
