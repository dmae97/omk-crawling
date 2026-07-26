"""Reddit client — old.reddit session warmup bypasses new www challenge.

Working path (2026-07):
  1. GET old.reddit.com/  (safari TLS) → loid + session_tracker cookies
  2. GET old.reddit.com/r/{sub}/.json  or  www.reddit.com/r/{sub}/.json?raw_json=1

www HTML returns "Please wait for verification" without browser JS.
Direct .json without cookies → 403 challenge page (~190KB).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from omk_crawl.resilience import ResponseCache, TokenBucket

DEFAULT_IMPERSONATE = "safari17_0"
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


@dataclass
class RedditConfig:
    rate: float = 0.5
    burst: float = 2.0
    cache_ttl: float = 120.0
    cache_dir: str | Path = ".crawl_cache/reddit"
    timeout: int = 25
    impersonate: str = DEFAULT_IMPERSONATE
    user_agent: str = DEFAULT_UA


@dataclass(frozen=True)
class RedditPost:
    id: str
    title: str
    author: str = ""
    subreddit: str = ""
    score: int = 0
    num_comments: int = 0
    url: str = ""
    permalink: str = ""
    created_utc: float = 0.0
    is_self: bool = False
    selftext: str = ""
    link_flair_text: str = ""
    over_18: bool = False
    raw: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        return d


@dataclass
class RedditResult:
    ok: bool
    endpoint: str = ""
    status_code: int | None = None
    data: Any = None
    posts: list[RedditPost] = field(default_factory=list)
    error: str = ""


def normalize_post(data: dict[str, Any]) -> RedditPost | None:
    if not isinstance(data, dict):
        return None
    # listing child wrapper
    if data.get("kind") == "t3" and isinstance(data.get("data"), dict):
        data = data["data"]
    title = str(data.get("title") or "").strip()
    pid = str(data.get("id") or data.get("name") or "").strip()
    if not title or not pid:
        return None
    try:
        score = int(data.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    try:
        ncomm = int(data.get("num_comments") or 0)
    except (TypeError, ValueError):
        ncomm = 0
    try:
        created = float(data.get("created_utc") or 0)
    except (TypeError, ValueError):
        created = 0.0
    permalink = str(data.get("permalink") or "")
    if permalink and not permalink.startswith("http"):
        permalink = "https://www.reddit.com" + permalink
    return RedditPost(
        id=pid.replace("t3_", ""),
        title=title,
        author=str(data.get("author") or ""),
        subreddit=str(data.get("subreddit") or ""),
        score=score,
        num_comments=ncomm,
        url=str(data.get("url") or ""),
        permalink=permalink,
        created_utc=created,
        is_self=bool(data.get("is_self")),
        selftext=str(data.get("selftext") or "")[:2000],
        link_flair_text=str(data.get("link_flair_text") or ""),
        over_18=bool(data.get("over_18")),
        raw=data,
    )


def posts_from_listing(payload: Any) -> list[RedditPost]:
    posts: list[RedditPost] = []
    if not isinstance(payload, dict):
        return posts
    data = payload.get("data") if "data" in payload else payload
    children = []
    if isinstance(data, dict):
        children = data.get("children") or []
    for child in children:
        p = normalize_post(child if isinstance(child, dict) else {})
        if p:
            posts.append(p)
    return posts


def posts_to_markdown(posts: list[RedditPost], *, title: str = "Reddit") -> str:
    lines = [f"# {title}", "", f"count: {len(posts)}", ""]
    for i, p in enumerate(posts, 1):
        lines.append(
            f"{i}. **{p.title}** — ▲{p.score:,} · 💬{p.num_comments} · "
            f"r/{p.subreddit} · u/{p.author}"
        )
        if p.permalink:
            lines.append(f"   - {p.permalink}")
        elif p.url:
            lines.append(f"   - {p.url}")
    return "\n".join(lines) + "\n"


class RedditClient:
    """Reddit JSON via old.reddit cookie warmup + TLS impersonation."""

    def __init__(self, config: RedditConfig | None = None) -> None:
        self.cfg = config or RedditConfig()
        self.bucket = TokenBucket(rate=self.cfg.rate, capacity=self.cfg.burst)
        self.cache = ResponseCache(cache_dir=self.cfg.cache_dir, ttl=self.cfg.cache_ttl)
        self._session: Any = None
        self._warmed = False

    def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        from curl_cffi import requests as cffi

        s = cffi.Session(impersonate=self.cfg.impersonate)
        s.headers.update(
            {
                "User-Agent": self.cfg.user_agent,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self._session = s
        return s

    def warmup(self, force: bool = False) -> bool:
        """Hit old.reddit front page to mint loid/session cookies."""
        if self._warmed and not force:
            return True
        s = self._ensure_session()
        try:
            r = s.get("https://old.reddit.com/", timeout=self.cfg.timeout)
            self._warmed = r.status_code < 500 and len(r.content) > 1000
            return self._warmed
        except Exception:
            self._warmed = False
            return False

    def _get_json(self, url: str) -> RedditResult:
        cache_key = f"reddit:{url}"
        cached = self.cache.get(cache_key)
        if cached:
            posts = posts_from_listing(cached.get("data", cached))
            return RedditResult(
                ok=True,
                endpoint=url,
                status_code=cached.get("_code", 200),
                data=cached.get("data", cached),
                posts=posts,
            )

        self.bucket.acquire()
        if not self.warmup():
            return RedditResult(ok=False, endpoint=url, error="warmup failed")

        s = self._ensure_session()
        try:
            r = s.get(
                url,
                headers={
                    "Accept": "application/json,text/javascript,*/*",
                    "Referer": "https://old.reddit.com/",
                },
                timeout=self.cfg.timeout,
            )
        except Exception as exc:
            return RedditResult(ok=False, endpoint=url, error=str(exc)[:200])

        if r.status_code >= 400:
            return RedditResult(
                ok=False,
                endpoint=url,
                status_code=r.status_code,
                error=f"HTTP {r.status_code}: {(r.text or '')[:160]}",
            )
        try:
            data = r.json()
        except Exception:
            return RedditResult(
                ok=False,
                endpoint=url,
                status_code=r.status_code,
                error=f"non-json body ({len(r.content)} bytes)",
            )

        posts = posts_from_listing(data)
        self.cache.put(cache_key, {"_code": r.status_code, "data": data})
        return RedditResult(
            ok=True,
            endpoint=url,
            status_code=r.status_code,
            data=data,
            posts=posts,
        )

    def subreddit(
        self,
        name: str,
        *,
        sort: str = "hot",
        t: str | None = None,
        limit: int = 25,
        after: str | None = None,
    ) -> RedditResult:
        """Fetch subreddit listing JSON.

        sort: hot | new | top | rising | controversial
        t: hour|day|week|month|year|all (for top/controversial)
        """
        sub = name.lstrip("r/").strip("/")
        sort = sort if sort in {"hot", "new", "top", "rising", "controversial"} else "hot"
        path = f"/r/{quote(sub)}/{sort}/.json"
        params = [f"limit={max(1, min(int(limit), 100))}", "raw_json=1"]
        if t and sort in {"top", "controversial"}:
            params.append(f"t={t}")
        if after:
            params.append(f"after={after}")
        # Prefer old.reddit (stable with safari cookies)
        url = f"https://old.reddit.com{path}?{'&'.join(params)}"
        res = self._get_json(url)
        if res.ok:
            return res
        # Fallback www with same session cookies
        url2 = f"https://www.reddit.com{path}?{'&'.join(params)}"
        return self._get_json(url2)

    def search(
        self,
        query: str,
        *,
        subreddit: str | None = None,
        sort: str = "relevance",
        t: str = "all",
        limit: int = 25,
    ) -> RedditResult:
        q = quote(query)
        params = [
            f"q={q}",
            f"sort={sort}",
            f"t={t}",
            f"limit={max(1, min(int(limit), 100))}",
            "raw_json=1",
        ]
        if subreddit:
            sub = subreddit.lstrip("r/").strip("/")
            url = f"https://old.reddit.com/r/{quote(sub)}/search/.json?{'&'.join(params)}&restrict_sr=1"
        else:
            url = f"https://old.reddit.com/search/.json?{'&'.join(params)}"
        return self._get_json(url)

    def user(self, username: str, *, limit: int = 25) -> RedditResult:
        user = username.lstrip("u/").strip("/")
        url = (
            f"https://old.reddit.com/user/{quote(user)}/.json"
            f"?limit={max(1, min(int(limit), 100))}&raw_json=1"
        )
        return self._get_json(url)

    def comments(self, subreddit: str, article_id: str, *, limit: int = 50) -> RedditResult:
        sub = subreddit.lstrip("r/").strip("/")
        aid = article_id.replace("t3_", "")
        url = (
            f"https://old.reddit.com/r/{quote(sub)}/comments/{quote(aid)}/.json"
            f"?limit={max(1, min(int(limit), 100))}&raw_json=1"
        )
        res = self._get_json(url)
        # comments endpoint returns [listing_post, listing_comments]
        if res.ok and isinstance(res.data, list) and res.data:
            res.posts = posts_from_listing(res.data[0])
        return res

    def status(self) -> dict[str, Any]:
        warm = self.warmup()
        cookies = {}
        if self._session is not None:
            try:
                cookies = dict(self._session.cookies)
            except Exception:
                cookies = {}
        return {
            "warmed": warm,
            "impersonate": self.cfg.impersonate,
            "cookie_keys": sorted(cookies.keys()),
            "rate": self.cfg.rate,
            "cache_dir": str(self.cfg.cache_dir),
            "live_path": "old.reddit.com/r/{sub}/.json after session warmup",
            "ts": time.time(),
        }
