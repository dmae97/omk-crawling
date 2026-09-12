"""Baemin client unit tests (network-free + optional live)."""

from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest

from omk_crawl.baemin import (
    BaeminConfig,
    BaeminShop,
    normalize_shop,
    rank_shops,
    shops_to_markdown,
)
from omk_crawl.tools import ALL_TOOLS, get_tool
from omk_crawl.tools.baemin_tool import BaeminTool, _parse_baemin_url


def test_baemin_registered() -> None:
    assert "baemin" in ALL_TOOLS
    assert isinstance(get_tool("baemin"), BaeminTool)


# ── config coercion: bad input must degrade to defaults, never raise ──────


def _captured_cfg(url: str, **kwargs: object) -> BaeminConfig:
    """Run BaeminTool.fetch far enough to capture the BaeminConfig it built."""
    import omk_crawl.tools.baemin_tool as bt

    seen: dict[str, BaeminConfig] = {}

    class _FakeClient:
        def __init__(self, cfg: BaeminConfig) -> None:
            seen["cfg"] = cfg

    with (
        patch.object(BaeminTool, "available", return_value=True),
        patch.object(bt, "BaeminClient", _FakeClient),
        # _FakeClient deliberately implements nothing else; we only care that
        # config construction happened without raising.
        contextlib.suppress(AttributeError),
    ):
        BaeminTool().fetch(url, **kwargs)
    return seen["cfg"]


@pytest.mark.parametrize(
    ("kwargs", "attr", "expected"),
    [
        ({"timeout": "abc"}, "timeout", 15),
        ({"timeout": None}, "timeout", 15),
        ({"rate": ["x"]}, "rate", 0.5),
        ({"rate": object()}, "rate", 0.5),
    ],
)
def test_bad_kwargs_fall_back_to_defaults(kwargs: dict, attr: str, expected: object) -> None:
    cfg = _captured_cfg("baemin://shops", **kwargs)
    assert getattr(cfg, attr) == expected


def test_non_numeric_latlng_falls_back_to_defaults() -> None:
    cfg = _captured_cfg("baemin://shops?lat=NaNaN&lng=zzz")
    assert cfg.lat == pytest.approx(37.4979)
    assert cfg.lng == pytest.approx(127.0276)


def test_valid_values_are_applied() -> None:
    cfg = _captured_cfg("baemin://shops?lat=36.8&lng=127.1", timeout=30, rate=2.0)
    assert cfg.timeout == 30
    assert cfg.rate == pytest.approx(2.0)
    assert cfg.lat == pytest.approx(36.8)
    assert cfg.lng == pytest.approx(127.1)


def test_parse_baemin_url_coords() -> None:
    p = _parse_baemin_url("baemin://36.833,127.130")
    assert p["lat"] == pytest.approx(36.833)
    assert p["lng"] == pytest.approx(127.130)
    assert p["action"] == "shops"


def test_parse_baemin_url_query() -> None:
    p = _parse_baemin_url("baemin://shops?lat=37.5&lng=127.0&limit=10")
    assert p["action"] == "shops"
    assert p["lat"] == "37.5"
    assert p["limit"] == "10"


def test_normalize_shop_statics() -> None:
    raw = {
        "shop": {
            "number": 123,
            "name": "테스트치킨",
            "statics": {"starScore": 4.8, "latestReviewCount": 1500},
            "isBaeminClub": True,
            "menus": [{"id": 1, "name": "후라이드", "price": {"value": 18000}}],
            "thumbnailImages": ["http://img/x.jpg"],
        }
    }
    s = normalize_shop(raw)
    assert s is not None
    assert s.name == "테스트치킨"
    assert s.number == 123
    assert s.latest_review_count == 1500
    assert s.star_score == pytest.approx(4.8)
    assert s.menus[0]["price"] == 18000


def test_rank_and_markdown() -> None:
    shops = [
        BaeminShop(number=1, name="A", latest_review_count=10, star_score=4.0),
        BaeminShop(number=2, name="B", latest_review_count=99, star_score=4.5),
        BaeminShop(number=3, name="C", latest_review_count=50, star_score=5.0),
    ]
    top = rank_shops(shops, limit=2)
    assert [s.name for s in top] == ["B", "C"]
    md = shops_to_markdown(top, title="t")
    assert "B" in md and "99" in md


def test_baemin_tool_status_offline() -> None:
    """status action does not need network success for client construction."""
    tool = BaeminTool()
    if not tool.available():
        pytest.skip("curl_cffi missing")
    r = tool.fetch("baemin://status")
    # status endpoint hits gateway — may be ok or error depending on network
    assert r.tool == "baemin"
    assert r.elapsed_ms >= 0


@pytest.mark.live
def test_live_list_shops_cheonan() -> None:
    """Optional live probe — skip if offline / blocked."""
    pytest.importorskip("curl_cffi")
    from omk_crawl.baemin import BaeminClient, BaeminConfig

    client = BaeminClient(BaeminConfig(rate=2.0, cache_ttl=0, timeout=20))
    res = client.list_shops(lat=36.8330, lng=127.1303, limit=5)
    if not res.ok:
        pytest.skip(f"live baemin unavailable: {res.status_code} {res.error}")
    data = res.data
    assert isinstance(data, dict)
    body = data.get("data") if "data" in data else data
    shops = (body or {}).get("shops") if isinstance(body, dict) else None
    assert isinstance(shops, list) and len(shops) > 0
    first = normalize_shop(shops[0])
    assert first is not None
    assert first.name
