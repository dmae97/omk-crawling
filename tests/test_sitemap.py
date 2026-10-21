"""Tests for sitemap discovery/parsing — offline, no network."""

from __future__ import annotations

from omk_crawl.sitemap import (
    SitemapEntry,
    default_fetch,
    discover,
    parse_robots_sitemaps,
    parse_sitemap,
)

URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://x.com/a</loc>
    <lastmod>2026-01-01</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
  <url><loc>https://x.com/b</loc></url>
</urlset>"""

INDEX = """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://x.com/sm-products.xml</loc></sitemap>
  <sitemap><loc>/sm-blog.xml</loc></sitemap>
</sitemapindex>"""


class TestParseSitemap:
    def test_urlset_entries(self):
        entries, children = parse_sitemap(URLSET)
        assert children == []
        assert len(entries) == 2
        assert entries[0] == SitemapEntry(
            loc="https://x.com/a", lastmod="2026-01-01", changefreq="weekly", priority=0.8
        )
        assert entries[1] == SitemapEntry(loc="https://x.com/b")

    def test_sitemapindex_children(self):
        entries, children = parse_sitemap(INDEX)
        assert entries == []
        assert children == ["https://x.com/sm-products.xml", "/sm-blog.xml"]

    def test_plain_text_list(self):
        entries, children = parse_sitemap(
            "https://x.com/1\n# comment\n\nhttps://x.com/2\nnot: a url"
        )
        assert children == []
        assert [e.loc for e in entries] == ["https://x.com/1", "https://x.com/2"]

    def test_malformed_yields_empty(self):
        assert parse_sitemap("") == ([], [])
        assert parse_sitemap("   \n  ") == ([], [])
        assert parse_sitemap("garbage <<< not xml") == ([], [])

    def test_unknown_root_yields_empty(self):
        # A non-sitemap XML document (e.g. an HTML error page) has no entries.
        assert parse_sitemap("<html><body>hello</body></html>") == ([], [])

    def test_bad_priority_is_none(self):
        entries, _ = parse_sitemap(
            '<urlset xmlns="u"><url><loc>https://x.com/a</loc>'
            "<priority>not-a-number</priority></url></urlset>"
        )
        assert entries[0].priority is None


class TestRobotsSitemaps:
    def test_extraction_case_insensitive_multiline(self):
        robots = "User-agent: *\nDisallow: /x\n\nsitemap: https://x.com/a.xml\nSitemap: /b.xml\n"
        assert parse_robots_sitemaps(robots) == ["https://x.com/a.xml", "/b.xml"]

    def test_empty(self):
        assert parse_robots_sitemaps("") == []
        assert parse_robots_sitemaps("User-agent: *\nAllow: /\n") == []

    def test_duplicates_kept_once(self):
        robots = "Sitemap: https://x.com/s.xml\nSitemap: https://x.com/s.xml\n"
        assert parse_robots_sitemaps(robots) == ["https://x.com/s.xml"]


class TestDiscover:
    def test_robots_authoritative_with_index_expansion(self):
        docs: dict[str, str] = {
            "https://x.com/robots.txt": "Sitemap: https://x.com/index.xml\n",
            "https://x.com/index.xml": INDEX,
            "https://x.com/sm-products.xml": URLSET,
        }
        fetched: list[str] = []

        def fetch(url: str) -> str | None:
            fetched.append(url)
            return docs.get(url)

        entries = discover("https://x.com/", fetch=fetch)
        # robots.txt is not counted as a sitemap document
        assert fetched[0] == "https://x.com/robots.txt"
        assert "https://x.com/index.xml" in fetched
        assert "https://x.com/sm-products.xml" in fetched
        assert [e.loc for e in entries] == ["https://x.com/a", "https://x.com/b"]

    def test_relative_child_resolved_against_doc_url(self):
        docs = {
            "https://x.com/robots.txt": "Sitemap: /index.xml\n",
            "https://x.com/index.xml": INDEX,
            "https://x.com/sm-blog.xml": URLSET,
        }

        def fetch(url: str) -> str | None:
            return docs.get(url)

        entries = discover("https://x.com/", fetch=fetch)
        assert [e.loc for e in entries] == ["https://x.com/a", "https://x.com/b"]

    def test_fallback_paths_when_no_robots_sitemap(self):
        docs = {
            "https://x.com/robots.txt": "User-agent: *\nAllow: /\n",
            "https://x.com/sitemap.xml": URLSET,
        }

        def fetch(url: str) -> str | None:
            return docs.get(url)

        entries = discover("https://x.com/", fetch=fetch)
        assert [e.loc for e in entries] == ["https://x.com/a", "https://x.com/b"]

    def test_fallback_tries_txt_when_xml_missing(self):
        seen: list[str] = []

        def fetch(url: str) -> str | None:
            seen.append(url)
            if url.endswith("sitemap.txt"):
                return "https://x.com/one\n"
            return None

        entries = discover("https://x.com/", fetch=fetch)
        assert "https://x.com/sitemap.xml" in seen
        assert "https://x.com/sitemap.txt" in seen
        assert [e.loc for e in entries] == ["https://x.com/one"]

    def test_max_documents_bounds_fetches(self):
        # index chains into itself; the bound must stop the expansion
        docs = {
            "https://x.com/robots.txt": "Sitemap: https://x.com/index.xml\n",
            "https://x.com/index.xml": INDEX,
        }

        def fetch(url: str) -> str | None:
            return docs.get(url)

        fetched: list[str] = []
        original = docs.get

        def counting_fetch(url: str) -> str | None:
            fetched.append(url)
            return original(url)

        discover("https://x.com/", fetch=counting_fetch, max_documents=1)
        sitemap_docs = [u for u in fetched if not u.endswith("robots.txt")]
        assert len(sitemap_docs) <= 1

    def test_unreachable_robots_still_probes_fallbacks(self):
        def fetch(url: str) -> str | None:
            return URLSET if url.endswith("sitemap.xml") else None

        entries = discover("https://x.com/", fetch=fetch)
        assert [e.loc for e in entries] == ["https://x.com/a", "https://x.com/b"]

    def test_invalid_inputs(self):
        assert discover("not a url", fetch=lambda u: "<urlset/>") == []
        assert discover("ftp://x.com/", fetch=lambda u: None) == []
        try:
            discover("https://x.com/", fetch=lambda u: None, max_documents=0)
        except ValueError:
            pass
        else:
            raise AssertionError("max_documents=0 must raise")


class TestDefaultFetchGuards:
    def test_non_http_rejected_without_network(self):
        assert default_fetch("file:///etc/passwd") is None
        assert default_fetch("ftp://x.com/sitemap.xml") is None
        assert default_fetch("not a url") is None
