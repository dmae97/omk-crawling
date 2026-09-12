"""Tests for XSearchTool, trends, and x_search (offline, deterministic).

All tests are offline — no network calls.  GraphQL response parsing
is verified against known-good fixtures.
"""

# ruff: noqa: E501  — test fixtures contain long literal strings

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.tools.x_search import (
    XSearchTool,
    _build_search_url,
    _coerce_cookies,
    _format_tweets,
    _parse_search_response,
    _parse_tweet,
    _safe_int,
    _strip_html,
    x_search,
)
from omk_crawl.trends import (
    KNOWN_WOEIDS,
    get_trends,
    trend_to_tweets,
    trending_with_content,
)

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def sample_tweet_raw() -> dict:
    """Minimal tweet result dict from X GraphQL (legacy format)."""
    return {
        "rest_id": "1906123456789012345",
        "legacy": {
            "full_text": "hello world #test",
            "created_at": "Tue Aug 19 06:00:00 +0000 2026",
            "lang": "en",
            "source": '<a href="https://x.com">Twitter Web App</a>',
            "retweet_count": 10,
            "favorite_count": 42,
            "reply_count": 3,
            "quote_count": 1,
            "bookmark_count": 0,
            "conversation_id_str": "1906123456789012345",
            "is_quote_status": False,
            "entities": {
                "hashtags": [{"text": "test"}],
                "user_mentions": [],
                "urls": [],
            },
        },
        "core": {
            "user_results": {
                "result": {
                    "rest_id": "12345",
                    "is_blue_verified": True,
                    "legacy": {
                        "screen_name": "testuser",
                        "name": "Test User",
                        "verified": False,
                        "profile_image_url_https": "https://pbs.twimg.com/photo.jpg",
                        "description": "A test account",
                        "followers_count": 1000,
                        "friends_count": 500,
                    },
                }
            }
        },
        "views": {"count": 5000},
    }


@pytest.fixture
def sample_search_response() -> dict:
    """Minimal SearchTimeline GraphQL response."""
    return {
        "data": {
            "search_by_raw_query": {
                "search_timeline": {
                    "timeline": {
                        "instructions": [
                            {
                                "type": "TimelineAddEntries",
                                "entries": [
                                    {
                                        "entryId": "tweet-1906123456789012345",
                                        "content": {
                                            "entryType": "TimelineTimelineItem",
                                            "itemContent": {
                                                "tweet_results": {
                                                    "result": {
                                                        "rest_id": "1906123456789012345",
                                                        "legacy": {
                                                            "full_text": "hello world #test",
                                                            "created_at": "Tue Aug 19 06:00:00 +0000 2026",
                                                            "lang": "en",
                                                            "source": '<a href="https://x.com">Twitter Web App</a>',
                                                            "retweet_count": 10,
                                                            "favorite_count": 42,
                                                            "reply_count": 3,
                                                            "quote_count": 1,
                                                            "bookmark_count": 0,
                                                            "conversation_id_str": "1906123456789012345",
                                                            "is_quote_status": False,
                                                            "entities": {
                                                                "hashtags": [{"text": "test"}],
                                                                "user_mentions": [],
                                                                "urls": [],
                                                            },
                                                        },
                                                        "core": {
                                                            "user_results": {
                                                                "result": {
                                                                    "rest_id": "12345",
                                                                    "is_blue_verified": True,
                                                                    "legacy": {
                                                                        "screen_name": "testuser",
                                                                        "name": "Test User",
                                                                        "verified": False,
                                                                        "profile_image_url_https": "https://pbs.twimg.com/photo.jpg",
                                                                        "description": "A test account",
                                                                        "followers_count": 1000,
                                                                        "friends_count": 500,
                                                                    },
                                                                }
                                                            }
                                                        },
                                                        "views": {"count": 5000},
                                                    }
                                                }
                                            },
                                        },
                                    }
                                ],
                            }
                        ]
                    }
                }
            }
        },
    }


# ── _build_search_url ────────────────────────────────────────────────────


class TestBuildSearchUrl:
    def test_basic(self) -> None:
        url = _build_search_url("python")
        assert "SearchTimeline" in url
        assert "python" in url
        assert "rawQuery" in url or "python" in url

    def test_with_cursor(self) -> None:
        url = _build_search_url("test", cursor="abc123")
        assert "cursor" in url
        assert "abc123" in url

    def test_count_clamped(self) -> None:
        url = _build_search_url("test", count=200)
        assert "query" in url or "rawQuery" in url  # doesn't crash

    def test_sort_top(self) -> None:
        url = _build_search_url("test", sort="Top")
        assert "Top" in url


# ── _parse_search_response ───────────────────────────────────────────────


class TestParseSearchResponse:
    def test_empty(self) -> None:
        assert _parse_search_response({"data": {}}) == []

    def test_parses_tweets(self, sample_search_response: dict) -> None:
        tweets = _parse_search_response(sample_search_response)
        assert len(tweets) == 1
        assert tweets[0]["text"] == "hello world #test"
        assert tweets[0]["author"]["user_name"] == "testuser"
        assert tweets[0]["like_count"] == 42
        assert tweets[0]["hashtags"] == ["test"]

    def test_skips_non_tweet_entries(self) -> None:
        data = {
            "data": {
                "search_by_raw_query": {
                    "search_timeline": {
                        "timeline": {
                            "instructions": [
                                {
                                    "type": "TimelineAddEntries",
                                    "entries": [
                                        {"entryId": "cursor-bottom-abc", "content": {}},
                                        {"entryId": "cursor-top-xyz", "content": {}},
                                    ],
                                }
                            ]
                        }
                    }
                }
            }
        }
        assert _parse_search_response(data) == []


# ── _parse_tweet ─────────────────────────────────────────────────────────


class TestParseTweet:
    def test_basic(self, sample_tweet_raw: dict) -> None:
        tweet = _parse_tweet(sample_tweet_raw)
        assert tweet is not None
        assert tweet["id"] == "1906123456789012345"
        assert tweet["text"] == "hello world #test"
        assert tweet["like_count"] == 42
        assert tweet["retweet_count"] == 10
        assert tweet["reply_count"] == 3
        assert tweet["quote_count"] == 1
        assert tweet["view_count"] == 5000
        assert tweet["is_quote"] is False
        assert tweet["author"]["user_name"] == "testuser"
        assert tweet["author"]["is_blue_verified"] is True
        assert tweet["author"]["followers"] == 1000
        assert tweet["url"] == "https://x.com/testuser/status/1906123456789012345"

    def test_with_visibility_results(self) -> None:
        raw = {
            "__typename": "TweetWithVisibilityResults",
            "tweet": {
                "rest_id": "123",
                "legacy": {
                    "full_text": "wrapped",
                    "created_at": "d",
                    "lang": "en",
                    "source": "s",
                    "retweet_count": 0,
                    "favorite_count": 0,
                    "reply_count": 0,
                    "quote_count": 0,
                    "bookmark_count": 0,
                    "conversation_id_str": "123",
                    "is_quote_status": False,
                    "entities": {"hashtags": [], "user_mentions": [], "urls": []},
                },
                "core": {
                    "user_results": {
                        "result": {
                            "rest_id": "u",
                            "is_blue_verified": False,
                            "legacy": {
                                "screen_name": "u",
                                "name": "U",
                                "verified": False,
                                "profile_image_url_https": "",
                                "description": "",
                                "followers_count": 0,
                                "friends_count": 0,
                            },
                        }
                    }
                },
                "views": {"count": 0},
            },
        }
        tweet = _parse_tweet(raw)
        assert tweet is not None
        assert tweet["text"] == "wrapped"

    def test_none_for_missing_id(self) -> None:
        assert _parse_tweet({}) is None
        assert _parse_tweet({"legacy": {}, "core": {}}) is None


# ── _format_tweets ───────────────────────────────────────────────────────


class TestFormatTweets:
    def test_empty(self) -> None:
        assert _format_tweets([]) == "*(no tweets found)*"

    def test_single(self) -> None:
        tweets = [
            {
                "author": {"name": "Alice", "user_name": "alice"},
                "text": "hello world",
                "created_at": "2026-08-19T06:00:00.000Z",
                "like_count": 10,
                "retweet_count": 2,
                "reply_count": 1,
                "view_count": 100,
                "hashtags": ["test"],
                "url": "https://x.com/alice/status/123",
            }
        ]
        result = _format_tweets(tweets)
        assert "**Alice**" in result
        assert "hello world" in result
        assert "❤ 10" in result
        assert "🔁 2" in result
        assert "#test" in result


# ── _safe_int / _strip_html ──────────────────────────────────────────────


class TestSafeInt:
    def test_valid(self) -> None:
        assert _safe_int(10) == 10
        assert _safe_int("20") == 20
        assert _safe_int(0) == 0

    def test_invalid(self) -> None:
        assert _safe_int("abc", 10) == 10
        assert _safe_int(None, 5) == 5
        assert _safe_int([], 3) == 3


class TestStripHtml:
    def test_strips_tags(self) -> None:
        assert _strip_html('<a href="x">Twitter Web App</a>') == "Twitter Web App"

    def test_no_tags(self) -> None:
        assert _strip_html("plain text") == "plain text"


# ── XSearchTool ──────────────────────────────────────────────────────────


class TestXSearchTool:
    def test_available(self) -> None:
        tool = XSearchTool()
        assert tool.available() is True  # curl_cffi is installed

    def test_interface_compliance(self) -> None:
        tool = XSearchTool()
        assert tool.name == "x_search"
        assert tool.pip_package == ""
        assert tool.layer == 0
        assert tool.needs_browser is False
        assert tool.needs_llm is False
        assert "timeout" in tool.capabilities
        assert "cookies" in tool.capabilities

    def test_fetch_missing_cookies(self) -> None:
        tool = XSearchTool()
        result = tool.fetch("test query")
        assert result.status == CrawlStatus.BLOCKED
        assert "auth_token" in (result.error or "")

    def test_fetch_with_cookies_mock(self) -> None:
        """fetch with cookies but curl_cffi mocked → ERROR."""
        tool = XSearchTool()
        with patch.dict("sys.modules", {"curl_cffi": None}):
            result = tool.fetch(
                "test",
                cookies={"auth_token": "x", "ct0": "y"},
            )
            assert result.status == CrawlStatus.ERROR
            assert "curl_cffi" in (result.error or "").lower()

    def test_fetch_success_mock(self) -> None:
        """fetch with valid cookies and mocked API response."""
        tool = XSearchTool()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "data": {
                "search_by_raw_query": {
                    "search_timeline": {
                        "timeline": {
                            "instructions": [
                                {
                                    "type": "TimelineAddEntries",
                                    "entries": [],
                                }
                            ]
                        }
                    }
                }
            }
        }
        with patch("curl_cffi.requests.get", return_value=mock_resp):
            result = tool.fetch(
                "test query",
                cookies={"auth_token": "x", "ct0": "y"},
            )
            assert result.status == CrawlStatus.OK
            assert result.extracted == []

    def test_fetch_401(self) -> None:
        tool = XSearchTool()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.ok = False
        with patch("curl_cffi.requests.get", return_value=mock_resp):
            result = tool.fetch(
                "test",
                cookies={"auth_token": "x", "ct0": "y"},
            )
            assert result.status == CrawlStatus.BLOCKED
            assert "session expired" in (result.error or "").lower()

    def test_fetch_429(self) -> None:
        tool = XSearchTool()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.ok = False
        with patch("curl_cffi.requests.get", return_value=mock_resp):
            result = tool.fetch(
                "test",
                cookies={"auth_token": "x", "ct0": "y"},
            )
            assert result.status == CrawlStatus.BLOCKED
            assert "rate limit" in (result.error or "").lower()

    def test_fetch_accepts_warmsession(self) -> None:
        """fetch should accept WarmSession as cookies."""
        from omk_crawl.fingerprint import profile_for
        from omk_crawl.warmup import WarmSession

        session = WarmSession(
            domain="x.com",
            cookies={"auth_token": "at", "ct0": "ct"},
            profile=profile_for("x.com"),
            acquired_at=0.0,
            tool="manual",
            ttl=3600,
        )
        tool = XSearchTool()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "data": {
                "search_by_raw_query": {
                    "search_timeline": {
                        "timeline": {"instructions": []},
                    }
                }
            }
        }
        with patch("curl_cffi.requests.get", return_value=mock_resp):
            result = tool.fetch("test", cookies=session)
            assert result.status == CrawlStatus.OK


# ── x_search convenience ─────────────────────────────────────────────────


class TestXSearchConvenience:
    def test_no_cookies(self) -> None:
        result = x_search("test")
        assert result.status == CrawlStatus.BLOCKED

    def test_with_cookies(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "data": {
                "search_by_raw_query": {
                    "search_timeline": {
                        "timeline": {"instructions": []},
                    }
                }
            }
        }
        with patch("curl_cffi.requests.get", return_value=mock_resp):
            result = x_search(
                "test",
                cookies={"auth_token": "x", "ct0": "y"},
            )
            assert result.status == CrawlStatus.OK


# ── session coercion (regression: warm_crawl returns CrawlResult) ────────


class TestCoerceCookies:
    """``_coerce_cookies`` must never leak a bare TypeError upstream."""

    def test_none_gives_empty(self) -> None:
        assert _coerce_cookies(None) == {}

    def test_plain_dict_passthrough(self) -> None:
        assert _coerce_cookies({"auth_token": "a", "ct0": "b"}) == {
            "auth_token": "a",
            "ct0": "b",
        }

    def test_values_are_stringified(self) -> None:
        assert _coerce_cookies({"n": 1}) == {"n": "1"}

    def test_warm_session(self) -> None:
        import time

        from omk_crawl.fingerprint import profile_for
        from omk_crawl.warmup import WarmSession

        ws = WarmSession(
            domain="x.com",
            cookies={"auth_token": "t", "ct0": "c"},
            profile=profile_for("x.com"),
            acquired_at=time.time(),
            tool="nodriver",
        )
        assert _coerce_cookies(ws) == {"auth_token": "t", "ct0": "c"}

    def test_duck_typed_cookie_mapping(self) -> None:
        class Session:
            cookies = {"auth_token": "a", "ct0": "b"}

        assert _coerce_cookies(Session()) == {"auth_token": "a", "ct0": "b"}

    def test_cookie_jar_of_pairs(self) -> None:
        class Jar:
            cookies = [("auth_token", "a"), ("ct0", "b")]

        assert _coerce_cookies(Jar()) == {"auth_token": "a", "ct0": "b"}

    @pytest.mark.parametrize("bad", [123, "cookiestring", ["a"], object()])
    def test_rejects_unusable_types(self, bad: object) -> None:
        with pytest.raises(TypeError, match="cannot read X session cookies"):
            _coerce_cookies(bad)

    def test_rejects_crawl_result(self) -> None:
        """warm_crawl() returns CrawlResult — must be rejected, not iterated."""
        cr = CrawlResult(url="https://x.com", status=CrawlStatus.OK, tool="warm_crawl")
        with pytest.raises(TypeError, match="CrawlResult"):
            _coerce_cookies(cr)


class TestSessionMisuseFailsClosed:
    """Regression: passing warm_crawl()'s CrawlResult used to raise
    ``TypeError: 'CrawlResult' object is not iterable`` from dict.update().
    It must now come back as a clean ERROR result."""

    def test_x_search_with_crawl_result_session(self) -> None:
        cr = CrawlResult(url="https://x.com", status=CrawlStatus.OK, tool="warm_crawl")
        result = x_search("query", session=cr)
        assert result.status == CrawlStatus.ERROR
        assert "CrawlResult" in (result.error or "")
        assert "SessionWarmup" in (result.error or "")

    def test_tool_fetch_with_bad_cookies(self) -> None:
        result = XSearchTool().fetch("query", cookies=12345)
        assert result.status == CrawlStatus.ERROR
        assert "cannot read X session cookies" in (result.error or "")

    def test_duck_typed_session_reaches_auth_stage(self) -> None:
        """A non-WarmSession object with .cookies must be unwrapped, not
        rejected as 'missing cookies'."""

        class Session:
            cookies = {"auth_token": "a", "ct0": "b"}

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "data": {"search_by_raw_query": {"search_timeline": {"timeline": {"instructions": []}}}}
        }
        with patch("curl_cffi.requests.get", return_value=mock_resp):
            result = x_search("query", session=Session())
        assert result.status == CrawlStatus.OK


# ── trends ───────────────────────────────────────────────────────────────


class TestTrends:
    def test_known_woeids(self) -> None:
        assert KNOWN_WOEIDS["worldwide"] == 1
        assert KNOWN_WOEIDS["kr"] == 23424868
        assert KNOWN_WOEIDS["us"] == 23424977
        assert KNOWN_WOEIDS["jp"] == 23424856
        assert KNOWN_WOEIDS["uk"] == 23424975

    def test_get_trends_no_curl_cffi(self) -> None:
        with patch.dict("sys.modules", {"curl_cffi": None}):
            result = get_trends()
            assert result == []

    def test_trend_to_tweets_no_cookies(self) -> None:
        result = trend_to_tweets("test")
        assert result["query"] == "test"
        assert result["tweets"] == []
        assert result["error"] is not None
        assert "cookies" in (result["error"] or "").lower()

    def test_trending_with_content_no_curl_cffi(self) -> None:
        with patch.dict("sys.modules", {"curl_cffi": None}):
            result = trending_with_content()
            assert result["trends"] == []
            assert result["tweets_by_trend"] == {}


# ── Tool registration ────────────────────────────────────────────────────


class TestToolRegistration:
    def test_x_search_in_all_tools(self) -> None:
        from omk_crawl.tools import ALL_TOOLS

        assert "x_search" in ALL_TOOLS

    def test_x_search_instantiable(self) -> None:
        from omk_crawl.tools import ALL_TOOLS

        tool_cls = ALL_TOOLS["x_search"]
        tool = tool_cls()
        assert tool.name == "x_search"
        assert tool.layer == 0
