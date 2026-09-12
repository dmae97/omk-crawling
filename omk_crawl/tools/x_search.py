"""XSearchTool — X search via GraphQL SearchTimeline (session cookies, no OAuth).

Uses X's public web bearer token + session cookies from warmup.
No API keys, no OAuth secrets — this is session reuse, not API auth.

Prerequisites:
    - Session cookies from warmup: ``ct0`` (csrf token) + ``auth_token`` (login session)
    - Or use ``SessionWarmup().acquire("https://x.com")`` — it returns a
      ``WarmSession`` carrying those cookies. Note ``warm_crawl()`` returns a
      ``CrawlResult``, NOT a session, and cannot be passed as ``session=``.

Usage:
    >>> from omk_crawl import SessionWarmup, x_search
    >>> session = SessionWarmup().acquire("https://x.com")  # one-time login
    >>> result = x_search("python programming", session=session)
    >>> for tweet in result.tweets:
    ...     print(tweet["text"])
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from collections.abc import Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.tools.base import BaseTool

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── X web client constants ───────────────────────────────────────────────
# Public bearer token — embedded in x.com for every visitor, NOT a secret.
_BEARER = (
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# GraphQL query IDs — rotate when X ships a new web build.
# Refresh from: https://github.com/fa0311/twitter-openapi
# or from a browser HAR capture (F12 → Network → filter "graphql").
_QUERY_IDS: dict[str, str] = {
    "SearchTimeline": "M1jEez78PEfVfbQLvlWMvQ",
    "UserByScreenName": "2qvSHpkWTMS9i0zJAwDNiA",
    "UserTweets": "hr4gzZONlq23okjU8fIe_A",
    "TweetDetail": "97JF30KziU00483E_8elBA",
    "HomeTimeline": "gKia-nBM9kwuDEfSDeWMfQ",
    "HomeLatestTimeline": "iCyHMXVutL66dZyvMtyChA",
}

# Feature flags — extracted from X's web client, updated periodically.
_SEARCH_FEATURES: dict[str, bool] = {
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "responsive_web_enhance_cards_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "articles_preview_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "rweb_cashtags_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_jetfuel_frame": True,
}

_GQL_BASE = "https://x.com/i/api/graphql"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Required cookies for X search (session reuse, not API auth).
_REQUIRED_COOKIES = ("auth_token", "ct0")


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _coerce_cookies(obj: Any) -> dict[str, str]:
    """Coerce a session-ish object into a plain ``dict[str, str]`` of cookies.

    Accepts ``None``, any ``Mapping``, or any object exposing a ``.cookies``
    mapping/jar (``WarmSession``, requests/curl_cffi sessions). Everything
    else is rejected with an actionable ``TypeError`` instead of letting a
    bare ``dict.update()``/``in`` blow up deeper in the call stack.

    Raises:
        TypeError: naming the offending type and the correct session source.
    """
    if obj is None:
        return {}
    if isinstance(obj, Mapping):
        return {str(k): str(v) for k, v in obj.items()}

    inner = getattr(obj, "cookies", None)
    if isinstance(inner, Mapping):
        return {str(k): str(v) for k, v in inner.items()}
    if inner is not None:
        # requests/curl_cffi cookie jars: iterable but dict()-able
        with suppress(TypeError, ValueError):
            return {str(k): str(v) for k, v in dict(inner).items()}

    raise TypeError(
        f"cannot read X session cookies from {type(obj).__name__!r}. "
        "Pass a WarmSession from SessionWarmup().acquire('https://x.com'), "
        "or a plain dict with auth_token + ct0. "
        "(warm_crawl() returns a CrawlResult and is not a session.)"
    )


def _build_search_url(
    query: str,
    *,
    sort: str = "Latest",
    cursor: str | None = None,
    count: int = 20,
) -> str:
    """Build a SearchTimeline GraphQL URL."""
    query_id = _QUERY_IDS["SearchTimeline"]
    variables: dict[str, Any] = {
        "rawQuery": query,
        "count": min(max(count, 1), 100),
        "product": sort,
        "querySource": "typed_query",
    }
    if cursor:
        variables["cursor"] = cursor

    compact_features = {k: v for k, v in _SEARCH_FEATURES.items() if v}
    url = (
        f"{_GQL_BASE}/{query_id}/SearchTimeline"
        f"?variables={urllib.parse.quote(json.dumps(variables, separators=(',', ':')))}"
        f"&features={urllib.parse.quote(json.dumps(compact_features, separators=(',', ':')))}"
    )
    return url


def _parse_search_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse SearchTimeline response into flat tweet dicts."""
    tweets: list[dict[str, Any]] = []
    instructions = (
        data.get("data", {})
        .get("search_by_raw_query", {})
        .get("search_timeline", {})
        .get("timeline", {})
        .get("instructions", [])
    )
    for instr in instructions:
        if instr.get("type") != "TimelineAddEntries":
            continue
        for entry in instr.get("entries", []):
            entry_id = entry.get("entryId", "")
            if not entry_id.startswith("tweet-"):
                continue
            content = entry.get("content", {})
            if content.get("entryType") != "TimelineTimelineItem":
                continue
            result = content.get("itemContent", {}).get("tweet_results", {}).get("result", {})
            if not result:
                continue
            tweet = _parse_tweet(result)
            if tweet:
                tweets.append(tweet)
    return tweets


def _parse_tweet(result: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a single tweet result dict into a flat structure."""
    # Handle __typename wrapping (Tweet vs TweetWithVisibilityResults)
    if result.get("__typename") == "TweetWithVisibilityResults":
        result = result.get("tweet", result)

    legacy = result.get("legacy") or {}
    core = result.get("core", {})
    user_results = core.get("user_results", {}).get("result", {})
    user_legacy = user_results.get("legacy") or {}

    rest_id = result.get("rest_id") or legacy.get("id_str", "")
    if not rest_id:
        return None

    screen_name = user_legacy.get("screen_name", "")
    tweet_id = rest_id

    return {
        "id": tweet_id,
        "url": f"https://x.com/{screen_name}/status/{tweet_id}",
        "text": legacy.get("full_text", ""),
        "created_at": legacy.get("created_at", ""),
        "lang": legacy.get("lang", ""),
        "source": _strip_html(legacy.get("source", "")),
        "retweet_count": _safe_int(legacy.get("retweet_count", 0)),
        "reply_count": _safe_int(legacy.get("reply_count", 0)),
        "like_count": _safe_int(legacy.get("favorite_count", 0)),
        "quote_count": _safe_int(legacy.get("quote_count", 0)),
        "bookmark_count": _safe_int(legacy.get("bookmark_count", 0)),
        "view_count": _safe_int(result.get("views", {}).get("count", 0)),
        "is_quote": legacy.get("is_quote_status", False),
        "conversation_id": legacy.get("conversation_id_str", ""),
        "in_reply_to_id": legacy.get("in_reply_to_status_id_str"),
        "in_reply_to_user": _extract_in_reply_to_screen_name(legacy),
        "hashtags": [h.get("text", "") for h in legacy.get("entities", {}).get("hashtags", [])],
        "mentions": [
            m.get("screen_name", "") for m in legacy.get("entities", {}).get("user_mentions", [])
        ],
        "urls": [u.get("expanded_url", "") for u in legacy.get("entities", {}).get("urls", [])],
        "media": _extract_media(legacy),
        "author": {
            "id": rest_id,
            "user_name": screen_name,
            "name": user_legacy.get("name", ""),
            "url": f"https://x.com/{screen_name}",
            "is_verified": user_legacy.get("verified", False),
            "is_blue_verified": user_results.get("is_blue_verified", False),
            "profile_picture": user_legacy.get("profile_image_url_https", ""),
            "description": user_legacy.get("description", ""),
            "followers": _safe_int(user_legacy.get("followers_count", 0)),
            "following": _safe_int(user_legacy.get("friends_count", 0)),
        },
    }


def _extract_media(legacy: dict[str, Any]) -> list[dict[str, str]]:
    media: list[dict[str, str]] = []
    extended = legacy.get("extended_entities", {}) or {}
    for m in extended.get("media", []):
        media.append(
            {
                "type": m.get("type", "photo"),
                "url": m.get("media_url_https", ""),
                "expanded_url": m.get("expanded_url", ""),
                "alt_text": (m.get("ext_alt_text") or ""),
            }
        )
    return media


def _extract_in_reply_to_screen_name(legacy: dict[str, Any]) -> str | None:
    entities = legacy.get("entities", {})
    mentions = entities.get("user_mentions", [])
    reply_id = legacy.get("in_reply_to_user_id_str")
    if reply_id and mentions:
        for m in mentions:
            if str(m.get("id_str")) == reply_id:
                return m.get("screen_name")
    return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _format_tweets(tweets: list[dict[str, Any]]) -> str:
    """Format tweets as markdown for CrawlResult."""
    if not tweets:
        return "*(no tweets found)*"
    lines: list[str] = []
    for t in tweets:
        author = t.get("author", {})
        name = author.get("name", "")
        handle = author.get("user_name", "?")
        likes = t.get("like_count", 0)
        retweets = t.get("retweet_count", 0)
        replies = t.get("reply_count", 0)
        views = t.get("view_count", 0)
        created = t.get("created_at", "")[:19]  # truncate tz

        lines.append(f"**{name}** (@{handle}) · {created}")
        lines.append(t.get("text", ""))
        stats = f"❤ {likes}  🔁 {retweets}  💬 {replies}  👁 {views}"
        if t.get("hashtags"):
            stats += "  #" + " #".join(t["hashtags"])
        lines.append(stats)
        lines.append(t.get("url", ""))
        lines.append("---")
    return "\n".join(lines)


# ── Tool ─────────────────────────────────────────────────────────────────


class XSearchTool(BaseTool):
    """Search X (Twitter) for tweets using GraphQL SearchTimeline.

    Requires session cookies (``auth_token`` + ``ct0``) from warmup.
    No API keys, no OAuth — session reuse only.
    """

    name = "x_search"
    pip_package = ""
    layer = 0
    needs_browser = False
    needs_llm = False
    capabilities: frozenset[str] = frozenset({"timeout", "cookies"})

    def available(self) -> bool:
        """curl_cffi is required for TLS impersonation."""
        try:
            import curl_cffi  # noqa: F401  # availability check

            return True
        except ImportError:
            return False

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        """Search X for tweets matching ``url`` (query string).

        Args:
            url: Search query string (e.g. ``"python programming"``).
            cookies: ``dict[str, str]`` — must include ``auth_token`` + ``ct0``.
            max_results: int (default 20, max 100).
            sort: "Latest" (default) or "Top".
            cursor: str — pagination cursor from previous response.
            timeout: int — request timeout in seconds (default 15).

        Returns:
            CrawlResult with ``.extracted`` = list of tweet dicts,
            ``.markdown`` = formatted tweet feed.
        """
        start = time.monotonic()
        query = url.strip()
        max_results = _safe_int(kwargs.get("max_results", 20), 20)
        max_results = max(1, min(max_results, 100))
        sort = kwargs.get("sort", "Latest")
        cursor = kwargs.get("cursor")
        timeout = _safe_int(kwargs.get("timeout", 15), 15)
        # Accept a WarmSession, a cookie jar, or a plain dict — fail closed
        # with a clear error rather than a TypeError from deeper in the stack.
        try:
            cookies = _coerce_cookies(kwargs.get("cookies"))
        except TypeError as exc:
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                tool=self.name,
                error=str(exc),
                elapsed_ms=(time.monotonic() - start) * 1000,
            )

        # Validate required cookies
        missing = [k for k in _REQUIRED_COOKIES if k not in cookies]
        if missing:
            return CrawlResult(
                url=url,
                status=CrawlStatus.BLOCKED,
                tool=self.name,
                error=(
                    f"X search requires session cookies: {missing}. "
                    "Use SessionWarmup().acquire('https://x.com') to acquire, "
                    "or provide auth_token + ct0 cookies manually."
                ),
                elapsed_ms=(time.monotonic() - start) * 1000,
            )

        try:
            import curl_cffi  # noqa: F401  # availability check
            from curl_cffi import requests  # type: ignore[import-untyped]
        except ImportError:
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                tool=self.name,
                error="curl_cffi is not installed. Run: pip install curl_cffi",
                elapsed_ms=(time.monotonic() - start) * 1000,
            )

        api_url = _build_search_url(query, sort=sort, cursor=cursor, count=max_results)
        headers = {
            "Authorization": _BEARER,
            "User-Agent": _UA,
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "x-csrf-token": cookies.get("ct0", ""),
            "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        }

        try:
            resp = requests.get(
                api_url,
                headers=headers,
                impersonate="chrome131",
                timeout=timeout,
            )
        except Exception as exc:
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                tool=self.name,
                error=f"X search request failed: {exc}",
                elapsed_ms=(time.monotonic() - start) * 1000,
            )

        elapsed = (time.monotonic() - start) * 1000

        if resp.status_code == 401:
            return CrawlResult(
                url=url,
                status=CrawlStatus.BLOCKED,
                tool=self.name,
                status_code=resp.status_code,
                error=(
                    "X session expired. Re-acquire via SessionWarmup().acquire('https://x.com')."
                ),
                elapsed_ms=elapsed,
            )

        if resp.status_code == 429:
            return CrawlResult(
                url=url,
                status=CrawlStatus.BLOCKED,
                tool=self.name,
                status_code=resp.status_code,
                error="X rate limit exceeded. Wait before retrying.",
                elapsed_ms=elapsed,
            )

        if not resp.ok:
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                tool=self.name,
                status_code=resp.status_code,
                error=f"X API error {resp.status_code}: {resp.text[:200]}",
                elapsed_ms=elapsed,
            )

        try:
            data = resp.json()
        except Exception:
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                tool=self.name,
                error="X API returned non-JSON response",
                elapsed_ms=elapsed,
            )

        tweets = _parse_search_response(data)

        return CrawlResult(
            url=url,
            status=CrawlStatus.OK,
            tool=self.name,
            status_code=resp.status_code,
            html=json.dumps(data, ensure_ascii=False, indent=2),
            extracted=tweets,
            markdown=_format_tweets(tweets),
            elapsed_ms=elapsed,
            fit_markdown=json.dumps({"count": len(tweets)}, separators=(",", ":")),
        )


# ── Convenience ──────────────────────────────────────────────────────────


def x_search(
    query: str,
    *,
    session: Any = None,
    cookies: dict[str, str] | None = None,
    max_results: int = 20,
    sort: str = "Latest",
    cursor: str | None = None,
    timeout: int | float = 15,
) -> CrawlResult:
    """Search X for tweets (convenience function).

    Args:
        query: Search query string.
        session: ``WarmSession`` from ``SessionWarmup().acquire("https://x.com")``
            — preferred. Also accepts any cookie mapping or cookie jar.
        cookies: Direct ``dict[str, str]`` with ``auth_token`` + ``ct0``.
        max_results: Max tweets per page (1–100).
        sort: ``"Latest"`` or ``"Top"``.
        cursor: Pagination cursor for next page.
        timeout: Request timeout in seconds.

    Returns:
        ``CrawlResult`` with ``.extracted`` = list of tweet dicts.
    """
    tool = XSearchTool()
    try:
        all_cookies = _coerce_cookies(session)
        all_cookies.update(_coerce_cookies(cookies))
    except TypeError as exc:
        return CrawlResult(
            url=_build_search_url(query),
            status=CrawlStatus.ERROR,
            tool=tool.name,
            error=str(exc),
        )

    return tool.fetch(
        query,
        cookies=all_cookies,
        max_results=max_results,
        sort=sort,
        cursor=cursor,
        timeout=timeout,
    )
