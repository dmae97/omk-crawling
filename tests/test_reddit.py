"""Reddit client unit tests."""

from __future__ import annotations

import pytest

from omk_crawl.reddit import (
    normalize_post,
    posts_from_listing,
    posts_to_markdown,
)
from omk_crawl.tools import ALL_TOOLS, get_tool
from omk_crawl.tools.reddit_tool import RedditTool, _parse_reddit_url


def test_reddit_registered() -> None:
    assert "reddit" in ALL_TOOLS
    assert isinstance(get_tool("reddit"), RedditTool)


def test_parse_reddit_url_sub() -> None:
    p = _parse_reddit_url("reddit://r/programming/hot")
    assert p["action"] == "subreddit"
    assert p["sub"] == "programming"
    assert p.get("sort") == "hot"


def test_parse_reddit_url_search() -> None:
    p = _parse_reddit_url("reddit://search?q=rust+async&limit=5")
    assert p["action"] == "search"
    assert "rust" in (p.get("q") or "")


def test_parse_https_reddit() -> None:
    p = _parse_reddit_url("https://www.reddit.com/r/korea/new/")
    assert p["action"] == "subreddit"
    assert p["sub"] == "korea"
    assert p.get("sort") == "new"


def test_normalize_and_markdown() -> None:
    child = {
        "kind": "t3",
        "data": {
            "id": "abc123",
            "title": "Hello Reddit",
            "author": "alice",
            "subreddit": "programming",
            "score": 42,
            "num_comments": 3,
            "permalink": "/r/programming/comments/abc123/hello/",
            "url": "https://example.com",
            "is_self": False,
        },
    }
    p = normalize_post(child)
    assert p is not None
    assert p.title == "Hello Reddit"
    assert p.score == 42
    listing = {"data": {"children": [child]}}
    posts = posts_from_listing(listing)
    assert len(posts) == 1
    md = posts_to_markdown(posts, title="t")
    assert "Hello Reddit" in md and "42" in md


def test_reddit_tool_status() -> None:
    tool = RedditTool()
    if not tool.available():
        pytest.skip("curl_cffi missing")
    r = tool.fetch("reddit://status")
    assert r.tool == "reddit"
    # may fail offline — still should return a result object
    assert r.elapsed_ms >= 0


@pytest.mark.live
def test_live_programming() -> None:
    pytest.importorskip("curl_cffi")
    from omk_crawl.reddit import RedditClient, RedditConfig

    client = RedditClient(RedditConfig(rate=1.0, cache_ttl=0, timeout=30))
    res = client.subreddit("programming", limit=5)
    if not res.ok:
        pytest.skip(f"reddit live unavailable: {res.status_code} {res.error}")
    assert len(res.posts) >= 1
    assert res.posts[0].title
