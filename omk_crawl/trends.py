"""Trends API — X trends via guest token (no auth, no API key).

Uses X's public guest token endpoint + public bearer token.
This works for any visitor — no login, no OAuth, no API keys.

Usage:
    >>> from omk_crawl.trends import get_trends
    >>> trends = get_trends(woeid=23424868)  # South Korea
    >>> for t in trends[:5]:
    ...     print(t["name"], t.get("tweet_volume"))
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# X public bearer token — embedded in x.com for every visitor.
_BEARER = (
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

_GUEST_ACTIVATE_URL = "https://api.x.com/1.1/guest/activate.json"
_TRENDS_URL = "https://api.x.com/1.1/trends/place.json"

# Known WOEIDs
KNOWN_WOEIDS: dict[str, int] = {
    "worldwide": 1,
    "kr": 23424868,
    "us": 23424977,
    "jp": 23424856,
    "uk": 23424975,
    "fr": 23424819,
    "de": 23424829,
    "ca": 23424775,
    "au": 23424748,
    "br": 23424768,
}

_GUEST_TOKEN_CACHE: dict[str, Any] = {"token": None, "expires": 0.0}
_GUEST_TOKEN_TTL = 180.0  # 3 minutes


def _activate_guest_token(timeout: float = 10.0) -> str | None:
    """Activate a guest token from X's public endpoint."""
    now = time.monotonic()
    if _GUEST_TOKEN_CACHE["token"] and now < _GUEST_TOKEN_CACHE["expires"]:
        return _GUEST_TOKEN_CACHE["token"]

    try:
        from curl_cffi import requests  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("curl_cffi not installed — guest token unavailable")
        return None

    try:
        resp = requests.post(
            _GUEST_ACTIVATE_URL,
            headers={"Authorization": _BEARER},
            impersonate="chrome131",
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            token = data.get("guest_token")
            if token:
                _GUEST_TOKEN_CACHE["token"] = token
                _GUEST_TOKEN_CACHE["expires"] = now + _GUEST_TOKEN_TTL
                return token
    except Exception as exc:
        # Bound the text: transport errors can carry the full request context
        # (headers included), and an unbounded repr would dump it into logs.
        logger.warning(
            "Guest token activation request failed (%s): %s",
            type(exc).__name__,
            str(exc)[:200],
        )

    return None


def get_trends(
    woeid: int | str = 23424868,
    *,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Fetch trending topics from X.

    Args:
        woeid: Where On Earth ID. Use ``KNOWN_WOEIDS`` for common regions,
               or pass a raw integer (e.g. ``23424868`` for South Korea).
        timeout: Request timeout in seconds.

    Returns:
        List of trend dicts: ``{"name": str, "url": str, "tweet_volume": int | None,
        "promoted_content": bool | None}``.
    """
    if isinstance(woeid, str):
        woeid = KNOWN_WOEIDS.get(woeid, woeid)
        try:
            woeid = int(woeid)
        except (ValueError, TypeError):
            logger.warning("Invalid WOEID: %s", woeid)
            return []

    try:
        from curl_cffi import requests  # type: ignore[import-untyped]
    except ImportError:
        return []

    token = _activate_guest_token(timeout=timeout)
    if not token:
        return []

    try:
        resp = requests.get(
            _TRENDS_URL,
            params={"id": str(woeid)},
            headers={
                "Authorization": _BEARER,
                "x-guest-token": token,
            },
            impersonate="chrome131",
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                return data[0].get("trends", [])
    except Exception as exc:
        logger.warning("Trends fetch failed: %s", exc)

    return []


def trend_to_tweets(
    trend_name: str,
    *,
    cookies: dict[str, str] | None = None,
    max_results: int = 10,
    timeout: int = 15,
) -> dict[str, Any]:
    """Search X for tweets about a trending topic (requires session cookies).

    Args:
        trend_name: Trend name to search for.
        cookies: ``dict[str, str]`` with ``auth_token`` + ``ct0``, or a
                 ``WarmSession`` from ``SessionWarmup().acquire("https://x.com")``.
                 (``warm_crawl()`` returns a ``CrawlResult``, not a session.)
        max_results: Max tweets per page.
        timeout: Request timeout in seconds.

    Returns:
        ``{"query": str, "tweets": list[dict], "error": str | None}``.
    """
    result: dict[str, Any] = {"query": trend_name, "tweets": [], "error": None}

    if not cookies:
        result["error"] = (
            "X session cookies required (auth_token + ct0). "
            "Use SessionWarmup().acquire('https://x.com') to acquire."
        )
        return result

    from omk_crawl.tools.x_search import x_search

    crawl_result = x_search(
        trend_name,
        cookies=cookies,
        max_results=max_results,
        timeout=timeout,
    )

    if crawl_result.status.value == "ok":
        result["tweets"] = crawl_result.extracted or []
    else:
        result["error"] = crawl_result.error or "search failed"

    return result


def trending_with_content(
    woeid: int | str = 23424868,
    *,
    cookies: dict[str, str] | None = None,
    top_n: int = 5,
    max_tweets_per_trend: int = 3,
    timeout: int = 15,
) -> dict[str, Any]:
    """Fetch trends and their top tweets in one call.

    Trends are fetched via guest token (no auth). Tweets require session
    cookies from warmup.

    Args:
        woeid: WOEID for the region.
        cookies: Session cookies for search.
        top_n: Number of top trends to search.
        max_tweets_per_trend: Max tweets per trend.
        timeout: Per-request timeout.

    Returns:
        ``{"trends": list[dict], "tweets_by_trend": dict[str, list[dict]]}``
    """
    trends = get_trends(woeid=woeid, timeout=timeout)[:top_n]
    tweets_by_trend: dict[str, list[dict]] = {}

    for trend in trends:
        name = trend.get("name", "")
        if not name:
            continue
        result = trend_to_tweets(
            name,
            cookies=cookies,
            max_results=max_tweets_per_trend,
            timeout=timeout,
        )
        tweets_by_trend[name] = result.get("tweets", [])

    return {"trends": trends, "tweets_by_trend": tweets_by_trend}
