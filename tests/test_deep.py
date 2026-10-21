"""Tests for the bounded BFS deep crawler — offline, no network."""

from __future__ import annotations

from typing import Any

import pytest

from omk_crawl.deep import (
    DeepCrawlConfig,
    DeepCrawlResult,
    deep_crawl,
    extract_links,
    is_crawlable,
    normalize_url,
    same_scope,
)
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.router import SmartRouter

SEED = "https://x.com/"


@pytest.fixture(autouse=True)
def offline_robots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep robots reads offline and deterministic; robots tests re-patch."""
    monkeypatch.setattr("omk_crawl.deep.check_robots_txt", lambda url: True)


def ok_page(url: str, html: str | None = None) -> CrawlResult:
    return CrawlResult(url=url, status=CrawlStatus.OK, html=html, markdown="md", tool="fake")


def install_pages(monkeypatch: pytest.MonkeyPatch, pages: dict[str, CrawlResult]) -> list[str]:
    """Point SmartRouter.crawl at a fixture map; returns fetch order."""
    order: list[str] = []

    def fake_crawl(self: SmartRouter, url: str, **kwargs: Any) -> CrawlResult:
        order.append(url)
        result = pages.get(url)
        if result is None:
            return CrawlResult(url=url, status=CrawlStatus.ERROR, error="not in fixture")
        return result

    monkeypatch.setattr("omk_crawl.router.SmartRouter.crawl", fake_crawl)
    return order


def make_router(**overrides: Any) -> SmartRouter:
    params: dict[str, Any] = {"respect_robots": False, "min_delay": 0.0}
    params.update(overrides)
    return SmartRouter(**params)


class TestNormalizeUrl:
    def test_canonicalization(self):
        assert normalize_url("HTTP://X.com:80/p?x=1#f") == "http://x.com/p?x=1"
        assert normalize_url("https://x.com:443/") == "https://x.com/"
        assert normalize_url("https://x.com") == "https://x.com/"
        assert normalize_url("https://[::1]:8080/p") == "https://[::1]:8080/p"

    def test_rejects_non_pages(self):
        non_pages = (
            "javascript:void(0)",
            "mailto:a@b.c",
            "tel:+82",
            "data:text/html,x",
            "/relative",
            "",
        )
        for url in non_pages:
            assert normalize_url(url) is None

    def test_keeps_query_and_drops_userinfo(self):
        assert normalize_url("https://u:p@x.com/p?a=1") == "https://x.com/p?a=1"


class TestExtractLinks:
    def test_relative_resolution_and_dedup(self):
        links = extract_links(
            '<a href="/b">1</a><a href="https://x.com/b">dup</a>'
            '<a href="mailto:a@b.c">m</a><a href="/c?q=1#top">q</a>',
            SEED,
        )
        assert links == ["https://x.com/b", "https://x.com/c?q=1"]

    def test_broken_html_never_raises(self):
        assert extract_links("<a href='/x", SEED) == []


class TestCrawlableAndScope:
    def test_assets_excluded(self):
        for url in (
            "https://x.com/logo.png",
            "https://x.com/a/b/c.css",
            "https://x.com/doc.pdf",
            "ftp://x.com/x",
            "https://x.com/",  # pages are crawlable
        ):
            assert is_crawlable(url) == url.endswith("/")

    def test_same_origin_default(self):
        assert same_scope("https://x.com/a", "https://x.com/b")
        assert not same_scope("https://x.com/a", "https://sub.x.com/b")
        assert not same_scope("https://x.com/a", "http://x.com/b")

    def test_same_domain_widens(self):
        assert same_scope("https://x.com/a", "https://sub.x.com/b", same_domain=True)
        assert not same_scope("https://x.com/a", "https://x.com.evil.io/b", same_domain=True)


class TestDeepCrawl:
    def test_bfs_respects_depth_cap(self, monkeypatch):
        pages = {
            SEED: ok_page(SEED, '<a href="/a">a</a><a href="/b">b</a>'),
            "https://x.com/a": ok_page("https://x.com/a", '<a href="/c">c</a>'),
            "https://x.com/b": ok_page("https://x.com/b"),
            "https://x.com/c": ok_page("https://x.com/c"),
        }
        order = install_pages(monkeypatch, pages)
        result = deep_crawl(SEED, DeepCrawlConfig(max_depth=1), router=make_router())
        assert order == [SEED, "https://x.com/a", "https://x.com/b"]
        assert result.stats.pages_ok == 3
        # links are only extracted within link-extraction depth (seed is depth 0)
        assert result.stats.links_found == 2
        assert result.stop_reason == "frontier_empty"
        assert [p.url for p in result.pages] == order

    def test_depth_two_reaches_second_hop(self, monkeypatch):
        pages = {
            SEED: ok_page(SEED, '<a href="/a">a</a>'),
            "https://x.com/a": ok_page("https://x.com/a", '<a href="/c">c</a>'),
            "https://x.com/c": ok_page("https://x.com/c"),
        }
        install_pages(monkeypatch, pages)
        result = deep_crawl(SEED, DeepCrawlConfig(max_depth=2), router=make_router())
        assert result.stats.pages_ok == 3

    def test_max_pages_stop_reason(self, monkeypatch):
        pages = {
            SEED: ok_page(SEED, '<a href="/1">1</a>'),
            "https://x.com/1": ok_page("https://x.com/1", '<a href="/2">2</a>'),
            "https://x.com/2": ok_page("https://x.com/2", '<a href="/3">3</a>'),
        }
        install_pages(monkeypatch, pages)
        result = deep_crawl(SEED, DeepCrawlConfig(max_pages=2), router=make_router())
        assert result.stats.pages_fetched == 2
        assert result.stop_reason == "max_pages"
        assert result.stats.pending >= 1

    def test_failed_page_yields_no_links(self, monkeypatch):
        pages = {
            SEED: ok_page(SEED, '<a href="/dead">d</a>'),
            "https://x.com/dead": CrawlResult(
                url="https://x.com/dead", status=CrawlStatus.BLOCKED, error="403"
            ),
        }
        install_pages(monkeypatch, pages)
        result = deep_crawl(SEED, DeepCrawlConfig(max_depth=2), router=make_router())
        assert result.stats.pages_ok == 1
        assert result.stats.pages_failed == 1
        # no links extracted from the failed page
        assert result.stats.links_found == 1

    def test_scope_and_assets_filtered(self, monkeypatch):
        pages = {
            SEED: ok_page(
                SEED,
                '<a href="https://sub.x.com/x">sub</a>'
                '<a href="/img.png">img</a>'
                '<a href="https://other.com/y">other</a>'
                '<a href="/ok">ok</a>',
            ),
            "https://x.com/ok": ok_page("https://x.com/ok"),
        }
        install_pages(monkeypatch, pages)
        result = deep_crawl(SEED, DeepCrawlConfig(), router=make_router())
        fetched = [p.url for p in result.pages]
        assert fetched == [SEED, "https://x.com/ok"]
        assert result.stats.filtered == 3

    def test_same_domain_allows_subdomain(self, monkeypatch):
        pages = {
            SEED: ok_page(SEED, '<a href="https://sub.x.com/x">sub</a>'),
            "https://sub.x.com/x": ok_page("https://sub.x.com/x"),
        }
        install_pages(monkeypatch, pages)
        result = deep_crawl(SEED, DeepCrawlConfig(same_domain=True), router=make_router())
        assert result.stats.pages_ok == 2

    def test_include_exclude_regexes(self, monkeypatch):
        pages = {
            SEED: ok_page(SEED, '<a href="/blog/1">1</a><a href="/shop/2">2</a>'),
            "https://x.com/blog/1": ok_page("https://x.com/blog/1"),
            "https://x.com/shop/2": ok_page("https://x.com/shop/2"),
        }
        install_pages(monkeypatch, pages)
        result = deep_crawl(
            SEED,
            DeepCrawlConfig(include=(r"/blog/",), exclude=(r"\?draft",)),
            router=make_router(),
        )
        fetched = [p.url for p in result.pages]
        assert "https://x.com/blog/1" in fetched
        assert "https://x.com/shop/2" not in fetched

    def test_robots_skips_host(self, monkeypatch):
        pages = {SEED: ok_page(SEED)}
        install_pages(monkeypatch, pages)
        monkeypatch.setattr("omk_crawl.deep.check_robots_txt", lambda url: False)
        result = deep_crawl(SEED, DeepCrawlConfig(respect_robots=True), router=make_router())
        assert result.stats.pages_fetched == 0
        assert result.stats.robots_skipped == 1
        assert result.stop_reason == "frontier_empty"

    def test_robots_cache_one_check_per_host(self, monkeypatch):
        pages = {
            SEED: ok_page(SEED, '<a href="/a">a</a>'),
            "https://x.com/a": ok_page("https://x.com/a"),
        }
        install_pages(monkeypatch, pages)
        calls: list[str] = []

        def fake_robots(url: str) -> bool:
            calls.append(url)
            return True

        monkeypatch.setattr("omk_crawl.deep.check_robots_txt", fake_robots)
        deep_crawl(SEED, DeepCrawlConfig(respect_robots=True), router=make_router())
        assert len(calls) == 1

    def test_sitemap_seeding(self, monkeypatch):
        pages = {
            SEED: ok_page(SEED),
            "https://x.com/from-sitemap": ok_page("https://x.com/from-sitemap"),
        }
        order = install_pages(monkeypatch, pages)
        docs = {
            "https://x.com/robots.txt": "Sitemap: /sm.xml\n",
            "https://x.com/sm.xml": (
                '<?xml version="1.0"?><urlset xmlns="u">'
                "<url><loc>https://x.com/from-sitemap</loc></url>"
                "<url><loc>https://x.com/asset.png</loc></url>"
                "</urlset>"
            ),
        }
        result = deep_crawl(
            SEED,
            DeepCrawlConfig(use_sitemap=True, sitemap_max_documents=4),
            router=make_router(),
            fetch=lambda url: docs.get(url),
        )
        assert result.stats.sitemap_seeds == 1
        assert "https://x.com/from-sitemap" in order
        # seeded page is a depth-0 peer: its links would be followed, png was filtered
        assert result.stats.filtered == 1

    def test_invalid_seed_raises(self):
        with pytest.raises(ValueError, match="http"):
            deep_crawl("not a url", router=make_router())

    def test_config_validation(self):
        with pytest.raises(ValueError, match="max_pages"):
            DeepCrawlConfig(max_pages=0)
        with pytest.raises(ValueError, match="max_depth"):
            DeepCrawlConfig(max_depth=-1)
        with pytest.raises(ValueError, match="valid regex"):
            DeepCrawlConfig(include=("([",))
        with pytest.raises(ValueError, match="sitemap_max_documents"):
            DeepCrawlConfig(sitemap_max_documents=0)

    def test_result_shape(self, monkeypatch):
        pages = {SEED: ok_page(SEED)}
        install_pages(monkeypatch, pages)
        result = deep_crawl(SEED, router=make_router())
        assert isinstance(result, DeepCrawlResult)
        payload = result.to_dict()
        assert payload["seed"] == SEED
        assert payload["stop_reason"] == "frontier_empty"
        assert payload["pages"][0]["status"] == "ok"
        assert payload["pages"][0]["markdown"] == "md"
        assert "deep crawl" in result.summary()

    def test_router_exception_never_stops_crawl(self, monkeypatch):
        pages = {
            SEED: ok_page(SEED, '<a href="/a">a</a><a href="/b">b</a>'),
            "https://x.com/a": ok_page("https://x.com/a"),
            "https://x.com/b": ok_page("https://x.com/b"),
        }

        def flaky_crawl(self: SmartRouter, url: str, **kwargs: Any) -> CrawlResult:
            if url == "https://x.com/a":
                raise RuntimeError("boom")
            return pages[url]

        monkeypatch.setattr("omk_crawl.router.SmartRouter.crawl", flaky_crawl)
        result = deep_crawl(SEED, DeepCrawlConfig(max_depth=1), router=make_router())
        assert result.stats.pages_failed == 1
        assert result.stats.pages_ok == 2
