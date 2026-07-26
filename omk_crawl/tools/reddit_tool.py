"""Reddit adapter — reddit://r/programming or reddit://search?q=."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from omk_crawl.reddit import RedditClient, RedditConfig, posts_to_markdown
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.tools.base import BaseTool


def _parse_reddit_url(url: str) -> dict[str, Any]:
    raw = url.strip()
    if raw in {"reddit://", "reddit:///", "reddit"}:
        return {"action": "status"}
    # plain https reddit URLs
    if raw.startswith("http") and "reddit.com" in raw:
        u = urlparse(raw)
        parts = [p for p in (u.path or "").split("/") if p]
        qs = {k: v[0] for k, v in parse_qs(u.query or "").items() if v}
        out: dict[str, Any] = {**qs}
        if parts and parts[0] == "r" and len(parts) >= 2:
            out["action"] = "subreddit"
            out["sub"] = parts[1]
            if len(parts) >= 3 and parts[2] in {
                "hot",
                "new",
                "top",
                "rising",
                "controversial",
            }:
                out["sort"] = parts[2]
            if len(parts) >= 4 and parts[2] == "comments":
                out["action"] = "comments"
                out["article"] = parts[3]
        elif parts and parts[0] == "user" and len(parts) >= 2:
            out["action"] = "user"
            out["user"] = parts[1]
        elif parts and parts[0] == "search":
            out["action"] = "search"
            out["q"] = qs.get("q") or ""
        else:
            out["action"] = "status"
        return out

    if not raw.startswith("reddit://"):
        return {"action": "status"}

    u = urlparse(raw)
    host = unquote(u.netloc or "")
    path = (u.path or "").strip("/")
    qs = {k: v[0] for k, v in parse_qs(u.query or "").items() if v}
    out = {**qs}

    # reddit://r/programming/hot
    if host in {"r", "sub", "subreddit"} or host.startswith("r/"):
        out["action"] = "subreddit"
        if host.startswith("r/") and len(host) > 2:
            rest = host[2:]
            segs = [path] if path else []
            # r/name in host rare
            out["sub"] = rest
        else:
            segs = [p for p in path.split("/") if p]
            out["sub"] = segs[0] if segs else qs.get("sub") or qs.get("r") or ""
            if len(segs) >= 2:
                out["sort"] = segs[1]
    elif host in {"search"}:
        out["action"] = "search"
        out["q"] = qs.get("q") or qs.get("query") or path
    elif host in {"user", "u"}:
        out["action"] = "user"
        out["user"] = path or qs.get("user") or ""
    elif host in {"status"}:
        out["action"] = "status"
    elif host:
        # reddit://programming → subreddit
        out["action"] = "subreddit"
        out["sub"] = host
        if path:
            out["sort"] = path.split("/")[0]
    else:
        out["action"] = "status"
    return out


class RedditTool(BaseTool):
    """Fetch Reddit listings via old.reddit JSON (session warmup)."""

    name = "reddit"
    pip_package = "curl_cffi"
    layer = 1
    needs_browser = False
    capabilities: frozenset[str] = frozenset({"timeout"})

    def available(self) -> bool:
        try:
            import curl_cffi  # noqa: F401
        except ImportError:
            return False
        return True

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        t0 = time.perf_counter()
        meta = self.contract_metadata(kwargs)
        if not self.available():
            return self._missing(url)

        parts = _parse_reddit_url(url)
        action = str(parts.get("action") or "status")
        cfg = RedditConfig(
            timeout=int(kwargs.get("timeout") or 25),
            rate=float(kwargs.get("rate") or 0.8),
        )
        client = RedditClient(cfg)

        if action == "status":
            st = client.status()
            md = (
                "# Reddit client status\n\n```json\n"
                + __import__("json").dumps(st, ensure_ascii=False, indent=2)
                + "\n```\n"
            )
            return CrawlResult(
                url=url,
                status=CrawlStatus.OK,
                status_code=200,
                markdown=md,
                fit_markdown=md,
                extracted=[st],
                tool=self.name,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={**meta, **st},
            )

        try:
            limit = int(parts.get("limit") or 25)
        except (TypeError, ValueError):
            limit = 25

        if action == "search":
            res = client.search(
                str(parts.get("q") or ""),
                subreddit=parts.get("sub") or parts.get("subreddit"),
                sort=str(parts.get("sort") or "relevance"),
                t=str(parts.get("t") or "all"),
                limit=limit,
            )
            title = f"Reddit search: {parts.get('q')}"
        elif action == "user":
            res = client.user(str(parts.get("user") or ""), limit=limit)
            title = f"Reddit user u/{parts.get('user')}"
        elif action == "comments":
            res = client.comments(
                str(parts.get("sub") or ""),
                str(parts.get("article") or ""),
                limit=limit,
            )
            title = f"Reddit comments {parts.get('article')}"
        else:
            sub = str(parts.get("sub") or "all")
            sort = str(parts.get("sort") or "hot")
            t = parts.get("t")
            res = client.subreddit(sub, sort=sort, t=t, limit=limit)
            title = f"r/{sub} ({sort})"

        if not res.ok:
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                status_code=res.status_code,
                tool=self.name,
                error=res.error or "reddit fetch failed",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={**meta, "endpoint": res.endpoint},
            )

        posts = res.posts
        md = posts_to_markdown(posts, title=title)
        extracted = [p.to_dict() for p in posts]
        return CrawlResult(
            url=url,
            status=CrawlStatus.OK,
            status_code=res.status_code or 200,
            markdown=md,
            fit_markdown=md,
            extracted=extracted,
            tool=self.name,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            metadata={
                **meta,
                "endpoint": res.endpoint,
                "count": len(posts),
                "top": posts[0].title if posts else None,
            },
        )
