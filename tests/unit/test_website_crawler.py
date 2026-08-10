"""
tests/unit/test_website_crawler.py
====================================
Unit tests for WebsiteCrawler.

All network calls and Playwright are mocked — no live requests are made.
Tests cover:
  - URL normalisation and domain matching
  - Slug generation
  - Page classification
  - Link extraction from HTML
  - Image extraction from HTML
  - Navigation extraction from HTML
  - Meta-tag and Open Graph parsing
  - Structured data (JSON-LD) extraction
  - Sitemap XML parsing
  - Asset URL discovery
  - JSON summary writing
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from bs4 import BeautifulSoup

from webmaker.config.settings import Settings
from webmaker.core.types import PageData, PageType
from webmaker.modules.website_crawler import (
    WebsiteCrawler,
    _ImageMeta,
    _OpenGraph,
    _RichPage,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def crawler(test_settings: Settings) -> WebsiteCrawler:
    """Return a WebsiteCrawler backed by isolated test settings."""
    return WebsiteCrawler(test_settings)


def _soup(html: str) -> BeautifulSoup:
    """Parse *html* with lxml and return the BeautifulSoup."""
    return BeautifulSoup(html, "lxml")


def _page(url: str, title: str = "", text: str = "", headings: list[str] | None = None) -> PageData:
    """Build a minimal PageData for classification tests."""
    return PageData(url=url, title=title, text_content=text, headings=headings or [])


# ── URL normalisation ─────────────────────────────────────────────────────────

class TestNormaliseUrl:
    def test_strips_fragment(self, crawler: WebsiteCrawler) -> None:
        assert crawler._normalise_url("https://example.com/page#section") == "https://example.com/page"

    def test_strips_fragment_only(self, crawler: WebsiteCrawler) -> None:
        result = crawler._normalise_url("https://example.com/#top")
        assert "#" not in result

    def test_non_http_returns_empty(self, crawler: WebsiteCrawler) -> None:
        assert crawler._normalise_url("ftp://example.com") == ""
        assert crawler._normalise_url("mailto:user@example.com") == ""

    def test_empty_returns_empty(self, crawler: WebsiteCrawler) -> None:
        assert crawler._normalise_url("") == ""

    def test_root_gets_slash(self, crawler: WebsiteCrawler) -> None:
        result = crawler._normalise_url("https://example.com")
        assert result.endswith("/")

    def test_trailing_slash_stripped_from_subpath(self, crawler: WebsiteCrawler) -> None:
        a = crawler._normalise_url("https://example.com/about/")
        b = crawler._normalise_url("https://example.com/about")
        assert a == b

    def test_lowercases_scheme_and_host(self, crawler: WebsiteCrawler) -> None:
        result = crawler._normalise_url("HTTPS://EXAMPLE.COM/page")
        assert result.startswith("https://example.com")

    def test_preserves_query_string(self, crawler: WebsiteCrawler) -> None:
        url = "https://example.com/search?q=hello"
        assert crawler._normalise_url(url) == url

    def test_whitespace_stripped(self, crawler: WebsiteCrawler) -> None:
        result = crawler._normalise_url("  https://example.com/  ")
        assert "  " not in result


# ── Domain matching ───────────────────────────────────────────────────────────

class TestSameDomain:
    def test_exact_match(self, crawler: WebsiteCrawler) -> None:
        assert crawler._same_domain("https://example.com/a", "https://example.com/") is True

    def test_www_vs_no_www(self, crawler: WebsiteCrawler) -> None:
        assert crawler._same_domain("https://www.example.com/a", "https://example.com/") is True
        assert crawler._same_domain("https://example.com/a", "https://www.example.com/") is True

    def test_different_domain(self, crawler: WebsiteCrawler) -> None:
        assert crawler._same_domain("https://other.com/a", "https://example.com/") is False

    def test_subdomain_treated_differently(self, crawler: WebsiteCrawler) -> None:
        # sub.example.com ≠ example.com (only www. is stripped)
        assert crawler._same_domain("https://sub.example.com/", "https://example.com/") is False

    def test_different_scheme_same_host(self, crawler: WebsiteCrawler) -> None:
        assert crawler._same_domain("http://example.com/a", "https://example.com/") is True


# ── Slug generation ───────────────────────────────────────────────────────────

class TestUrlToSlug:
    def test_root_is_home(self, crawler: WebsiteCrawler) -> None:
        assert crawler._url_to_slug("https://example.com/") == "home"
        assert crawler._url_to_slug("https://example.com") == "home"

    def test_simple_path(self, crawler: WebsiteCrawler) -> None:
        assert crawler._url_to_slug("https://example.com/about") == "about"

    def test_nested_path(self, crawler: WebsiteCrawler) -> None:
        result = crawler._url_to_slug("https://example.com/services/web-design")
        assert "services" in result
        assert "web" in result

    def test_special_chars_replaced(self, crawler: WebsiteCrawler) -> None:
        result = crawler._url_to_slug("https://example.com/über-uns")
        assert result.isascii() or all(c in "-_" or c.isalnum() for c in result)

    def test_long_path_truncated(self, crawler: WebsiteCrawler) -> None:
        long_path = "a" * 200
        result = crawler._url_to_slug(f"https://example.com/{long_path}")
        assert len(result) <= 100


# ── Page classification ───────────────────────────────────────────────────────

class TestClassifyPage:
    def test_root_is_home(self, crawler: WebsiteCrawler) -> None:
        assert crawler.classify_page(_page("https://example.com/")) == PageType.HOME

    def test_about_url(self, crawler: WebsiteCrawler) -> None:
        assert crawler.classify_page(_page("https://example.com/about")) == PageType.ABOUT
        assert crawler.classify_page(_page("https://example.com/about-us")) == PageType.ABOUT

    def test_contact_url(self, crawler: WebsiteCrawler) -> None:
        assert crawler.classify_page(_page("https://example.com/contact")) == PageType.CONTACT
        assert crawler.classify_page(_page("https://example.com/kontakt")) == PageType.CONTACT

    def test_services_url(self, crawler: WebsiteCrawler) -> None:
        assert crawler.classify_page(_page("https://example.com/services")) == PageType.SERVICES
        assert crawler.classify_page(_page("https://example.com/leistungen")) == PageType.SERVICES

    def test_blog_url(self, crawler: WebsiteCrawler) -> None:
        assert crawler.classify_page(_page("https://example.com/blog")) == PageType.BLOG
        assert crawler.classify_page(_page("https://example.com/news")) == PageType.BLOG

    def test_gallery_url(self, crawler: WebsiteCrawler) -> None:
        assert crawler.classify_page(_page("https://example.com/gallery")) == PageType.GALLERY
        assert crawler.classify_page(_page("https://example.com/galerie")) == PageType.GALLERY

    def test_unknown_url(self, crawler: WebsiteCrawler) -> None:
        assert crawler.classify_page(_page("https://example.com/some-random-page")) == PageType.UNKNOWN

    def test_home_title_fallback(self, crawler: WebsiteCrawler) -> None:
        page = _page("https://example.com/start", title="Welcome to our site")
        assert crawler.classify_page(page) == PageType.HOME


# ── Link extraction ───────────────────────────────────────────────────────────

class TestExtractLinks:
    BASE = "https://example.com/"

    def test_internal_links_discovered(self, crawler: WebsiteCrawler) -> None:
        html = """
        <html><body>
          <a href="/about">About</a>
          <a href="/contact">Contact</a>
        </body></html>
        """
        soup = _soup(html)
        internal, external = crawler._extract_links(soup, self.BASE)
        assert any("about" in u for u in internal)
        assert any("contact" in u for u in internal)

    def test_external_links_discovered(self, crawler: WebsiteCrawler) -> None:
        html = '<html><body><a href="https://google.com">Google</a></body></html>'
        soup = _soup(html)
        internal, external = crawler._extract_links(soup, self.BASE)
        assert any("google.com" in u for u in external)
        assert not any("google.com" in u for u in internal)

    def test_fragment_links_ignored(self, crawler: WebsiteCrawler) -> None:
        html = '<html><body><a href="#section">Anchor</a></body></html>'
        soup = _soup(html)
        internal, external = crawler._extract_links(soup, self.BASE)
        assert internal == []
        assert external == []

    def test_mailto_ignored(self, crawler: WebsiteCrawler) -> None:
        html = '<html><body><a href="mailto:info@example.com">Email</a></body></html>'
        soup = _soup(html)
        internal, external = crawler._extract_links(soup, self.BASE)
        assert internal == [] and external == []

    def test_deduplication(self, crawler: WebsiteCrawler) -> None:
        html = """
        <html><body>
          <a href="/about">About 1</a>
          <a href="/about">About 2</a>
          <a href="/about/">About 3</a>
        </body></html>
        """
        soup = _soup(html)
        internal, _ = crawler._extract_links(soup, self.BASE)
        about_links = [u for u in internal if "about" in u]
        assert len(about_links) == 1

    def test_relative_urls_resolved(self, crawler: WebsiteCrawler) -> None:
        html = '<html><body><a href="services">Services</a></body></html>'
        soup = _soup(html)
        internal, _ = crawler._extract_links(soup, self.BASE)
        assert all(u.startswith("http") for u in internal)


# ── Image extraction ──────────────────────────────────────────────────────────

class TestExtractImages:
    BASE = "https://example.com/"

    def test_standard_img(self, crawler: WebsiteCrawler) -> None:
        html = '<html><body><img src="/images/logo.png" alt="Logo" width="200" height="50"></body></html>'
        soup = _soup(html)
        images = crawler._extract_images(soup, self.BASE)
        assert len(images) == 1
        assert images[0].alt_text == "Logo"
        assert images[0].width == 200
        assert images[0].height == 50

    def test_lazy_loaded_img(self, crawler: WebsiteCrawler) -> None:
        html = '<html><body><img data-src="/images/lazy.jpg" alt="Lazy"></body></html>'
        soup = _soup(html)
        images = crawler._extract_images(soup, self.BASE)
        assert len(images) == 1
        assert "lazy" in images[0].source_url

    def test_data_uri_ignored(self, crawler: WebsiteCrawler) -> None:
        html = '<html><body><img src="data:image/png;base64,abc123"></body></html>'
        soup = _soup(html)
        images = crawler._extract_images(soup, self.BASE)
        assert images == []

    def test_deduplication(self, crawler: WebsiteCrawler) -> None:
        html = """
        <html><body>
          <img src="/img/photo.jpg">
          <img src="/img/photo.jpg">
        </body></html>
        """
        soup = _soup(html)
        images = crawler._extract_images(soup, self.BASE)
        assert len(images) == 1

    def test_absolute_external_image(self, crawler: WebsiteCrawler) -> None:
        html = '<html><body><img src="https://cdn.other.com/img.png" alt="CDN"></body></html>'
        soup = _soup(html)
        images = crawler._extract_images(soup, self.BASE)
        assert len(images) == 1
        assert "cdn.other.com" in images[0].source_url


# ── Navigation extraction ─────────────────────────────────────────────────────

class TestExtractNavigation:
    BASE = "https://example.com/"

    def test_nav_links_captured(self, crawler: WebsiteCrawler) -> None:
        html = """
        <html><body>
          <nav>
            <a href="/home">Home</a>
            <a href="/about">About</a>
            <a href="/contact">Contact</a>
          </nav>
        </body></html>
        """
        soup = _soup(html)
        nav = crawler._extract_navigation(soup, self.BASE)
        texts = [item["text"] for item in nav]
        assert "Home" in texts
        assert "About" in texts
        assert "Contact" in texts

    def test_header_links_captured(self, crawler: WebsiteCrawler) -> None:
        html = """
        <html><body>
          <header>
            <a href="/services">Services</a>
          </header>
        </body></html>
        """
        soup = _soup(html)
        nav = crawler._extract_navigation(soup, self.BASE)
        assert any("services" in item["url"] for item in nav)

    def test_fragment_links_excluded(self, crawler: WebsiteCrawler) -> None:
        html = '<html><body><nav><a href="#top">Top</a></nav></body></html>'
        soup = _soup(html)
        nav = crawler._extract_navigation(soup, self.BASE)
        assert nav == []


# ── Meta-tag parsing ──────────────────────────────────────────────────────────

class TestParseMeta:
    def test_title_extracted(self, crawler: WebsiteCrawler) -> None:
        html = '<html><head><title>My Page</title></head><body></body></html>'
        soup = _soup(html)
        rp = _RichPage(url="https://example.com/")
        crawler._parse_meta(soup, rp)
        assert rp.title == "My Page"

    def test_meta_description(self, crawler: WebsiteCrawler) -> None:
        html = '<html><head><meta name="description" content="Great site"></head></html>'
        soup = _soup(html)
        rp = _RichPage(url="https://example.com/")
        crawler._parse_meta(soup, rp)
        assert rp.meta_description == "Great site"

    def test_meta_robots(self, crawler: WebsiteCrawler) -> None:
        html = '<html><head><meta name="robots" content="noindex,nofollow"></head></html>'
        soup = _soup(html)
        rp = _RichPage(url="https://example.com/")
        crawler._parse_meta(soup, rp)
        assert "noindex" in rp.meta_robots

    def test_canonical_url(self, crawler: WebsiteCrawler) -> None:
        html = '<html><head><link rel="canonical" href="https://example.com/page"></head></html>'
        soup = _soup(html)
        rp = _RichPage(url="https://example.com/")
        crawler._parse_meta(soup, rp)
        assert rp.canonical_url == "https://example.com/page"

    def test_language(self, crawler: WebsiteCrawler) -> None:
        html = '<html lang="de"><head></head><body></body></html>'
        soup = _soup(html)
        rp = _RichPage(url="https://example.com/")
        crawler._parse_meta(soup, rp)
        assert rp.language == "de"

    def test_open_graph_tags(self, crawler: WebsiteCrawler) -> None:
        html = """
        <html><head>
          <meta property="og:title" content="OG Title">
          <meta property="og:description" content="OG Desc">
          <meta property="og:image" content="https://example.com/img.png">
          <meta property="og:type" content="website">
          <meta property="og:site_name" content="ExampleSite">
        </head></html>
        """
        soup = _soup(html)
        rp = _RichPage(url="https://example.com/")
        crawler._parse_meta(soup, rp)
        assert rp.open_graph.og_title == "OG Title"
        assert rp.open_graph.og_description == "OG Desc"
        assert rp.open_graph.og_image == "https://example.com/img.png"
        assert rp.open_graph.og_type == "website"
        assert rp.open_graph.og_site_name == "ExampleSite"


# ── Heading extraction ────────────────────────────────────────────────────────

class TestParseHeadings:
    def test_h1_h2_h3_extracted(self, crawler: WebsiteCrawler) -> None:
        html = """
        <html><body>
          <h1>Main Title</h1>
          <h2>Section A</h2>
          <h2>Section B</h2>
          <h3>Subsection</h3>
        </body></html>
        """
        soup = _soup(html)
        rp = _RichPage(url="https://example.com/")
        crawler._parse_headings(soup, rp)
        assert rp.h1 == ["Main Title"]
        assert rp.h2 == ["Section A", "Section B"]
        assert rp.h3 == ["Subsection"]

    def test_empty_headings_excluded(self, crawler: WebsiteCrawler) -> None:
        html = "<html><body><h1>  </h1><h2>Real</h2></body></html>"
        soup = _soup(html)
        rp = _RichPage(url="https://example.com/")
        crawler._parse_headings(soup, rp)
        assert rp.h1 == []
        assert rp.h2 == ["Real"]


# ── Structured data (JSON-LD) ─────────────────────────────────────────────────

class TestParseStructuredData:
    def test_valid_jsonld_extracted(self, crawler: WebsiteCrawler) -> None:
        html = """
        <html><head>
          <script type="application/ld+json">
            {"@type": "Organization", "name": "Acme Corp"}
          </script>
        </head></html>
        """
        soup = _soup(html)
        rp = _RichPage(url="https://example.com/")
        crawler._parse_structured_data(soup, rp)
        assert len(rp.structured_data) == 1
        assert rp.structured_data[0]["name"] == "Acme Corp"

    def test_invalid_jsonld_ignored(self, crawler: WebsiteCrawler) -> None:
        html = """
        <html><head>
          <script type="application/ld+json">NOT_JSON</script>
        </head></html>
        """
        soup = _soup(html)
        rp = _RichPage(url="https://example.com/")
        crawler._parse_structured_data(soup, rp)
        assert rp.structured_data == []

    def test_multiple_jsonld_blocks(self, crawler: WebsiteCrawler) -> None:
        html = """
        <html><head>
          <script type="application/ld+json">{"@type": "A"}</script>
          <script type="application/ld+json">{"@type": "B"}</script>
        </head></html>
        """
        soup = _soup(html)
        rp = _RichPage(url="https://example.com/")
        crawler._parse_structured_data(soup, rp)
        assert len(rp.structured_data) == 2


# ── Sitemap parsing ───────────────────────────────────────────────────────────

class TestParseSitemap:
    def _make_sitemap_xml(self, urls: list[str]) -> str:
        """Build a minimal sitemap XML string."""
        items = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          {items}
        </urlset>"""

    def test_url_set_parsed(self, crawler: WebsiteCrawler, requests_mock) -> None:
        xml_body = self._make_sitemap_xml([
            "https://example.com/",
            "https://example.com/about",
            "https://example.com/contact",
        ])
        requests_mock.get("https://example.com/sitemap.xml", text=xml_body)
        crawler._session = crawler._create_session()
        urls = crawler._parse_sitemap("https://example.com/sitemap.xml")
        assert len(urls) == 3
        assert "https://example.com/about" in urls

    def test_invalid_xml_returns_empty(self, crawler: WebsiteCrawler, requests_mock) -> None:
        requests_mock.get("https://example.com/sitemap.xml", text="<NOTVALID")
        crawler._session = crawler._create_session()
        urls = crawler._parse_sitemap("https://example.com/sitemap.xml")
        assert urls == []

    def test_404_returns_empty(self, crawler: WebsiteCrawler, requests_mock) -> None:
        requests_mock.get("https://example.com/sitemap.xml", status_code=404)
        crawler._session = crawler._create_session()
        urls = crawler._parse_sitemap("https://example.com/sitemap.xml")
        assert urls == []


# ── Asset URL discovery ───────────────────────────────────────────────────────

class TestExtractAssetUrls:
    BASE = "https://example.com/"

    def test_favicon_link_captured(self, crawler: WebsiteCrawler) -> None:
        html = '<html><head><link rel="icon" href="/favicon.ico"></head></html>'
        soup = _soup(html)
        assets = crawler._extract_asset_urls(soup, self.BASE)
        assert any("favicon" in a for a in assets)

    def test_pdf_link_captured(self, crawler: WebsiteCrawler) -> None:
        html = '<html><body><a href="/brochure.pdf">Download</a></body></html>'
        soup = _soup(html)
        assets = crawler._extract_asset_urls(soup, self.BASE)
        assert any(".pdf" in a for a in assets)

    def test_js_not_captured(self, crawler: WebsiteCrawler) -> None:
        html = '<html><body><a href="/app.js">Script</a></body></html>'
        soup = _soup(html)
        assets = crawler._extract_asset_urls(soup, self.BASE)
        # .js is not in _ASSET_EXTENSIONS — should not appear
        assert not any(".js" in a for a in assets)


# ── JSON summary writing ──────────────────────────────────────────────────────

class TestWriteJsonSummaries:
    def _build_page(self, url: str, title: str = "Title") -> _RichPage:
        return _RichPage(
            url=url,
            title=title,
            status_code=200,
            page_type="home",
            word_count=42,
            internal_links=["https://example.com/about"],
            external_links=["https://google.com"],
            images=[_ImageMeta(source_url="https://example.com/logo.png", alt_text="Logo")],
            crawled_at="2026-01-01T00:00:00+00:00",
            load_time_ms=250.0,
        )

    def test_pages_json_created(self, crawler: WebsiteCrawler, tmp_path: Path) -> None:
        json_dir = tmp_path / "json"
        json_dir.mkdir()
        (tmp_path / "json" / "pages").mkdir()

        pages = [self._build_page("https://example.com/")]
        crawler._image_registry = {}
        crawler._asset_registry = {}
        crawler._write_json_summaries(tmp_path, pages, "https://example.com/", 3.5)

        pages_json = tmp_path / "json" / "pages.json"
        assert pages_json.exists()
        from webmaker.core.schema import unwrap_json
        data = unwrap_json(json.loads(pages_json.read_text()))
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["url"] == "https://example.com/"
        assert data[0]["word_count"] == 42

    def test_crawl_summary_created(self, crawler: WebsiteCrawler, tmp_path: Path) -> None:
        (tmp_path / "json").mkdir()
        (tmp_path / "json" / "pages").mkdir()
        pages = [self._build_page("https://example.com/")]
        crawler._image_registry = {}
        crawler._asset_registry = {}
        crawler._write_json_summaries(tmp_path, pages, "https://example.com/", 5.0)

        summary = json.loads((tmp_path / "json" / "crawl_summary.json").read_text())
        assert summary["total_pages"] == 1
        assert summary["crawl_duration_s"] == 5.0
        assert summary["target_url"] == "https://example.com/"

    def test_images_json_created(self, crawler: WebsiteCrawler, tmp_path: Path) -> None:
        (tmp_path / "json").mkdir()
        (tmp_path / "json" / "pages").mkdir()
        pages = [self._build_page("https://example.com/")]
        crawler._image_registry = {
            "https://example.com/logo.png": _ImageMeta(
                source_url="https://example.com/logo.png", downloaded=True
            )
        }
        crawler._asset_registry = {}
        crawler._write_json_summaries(tmp_path, pages, "https://example.com/", 2.0)

        from webmaker.core.schema import unwrap_json
        images = unwrap_json(json.loads((tmp_path / "json" / "images.json").read_text()))
        assert isinstance(images, list)


# ── Unique path helper ────────────────────────────────────────────────────────

class TestUniquePath:
    def test_unique_when_no_conflict(self, tmp_path: Path) -> None:
        result = WebsiteCrawler._unique_path(tmp_path, "file.png")
        assert result == tmp_path / "file.png"

    def test_increments_on_conflict(self, tmp_path: Path) -> None:
        (tmp_path / "file.png").write_bytes(b"")
        result = WebsiteCrawler._unique_path(tmp_path, "file.png")
        assert result == tmp_path / "file_1.png"

    def test_increments_twice(self, tmp_path: Path) -> None:
        (tmp_path / "file.png").write_bytes(b"")
        (tmp_path / "file_1.png").write_bytes(b"")
        result = WebsiteCrawler._unique_path(tmp_path, "file.png")
        assert result == tmp_path / "file_2.png"


# ── robots.txt ────────────────────────────────────────────────────────────────

class TestRobotsAllowed:
    def test_no_parser_always_allowed(self, crawler: WebsiteCrawler) -> None:
        crawler._robot_parser = None
        assert crawler.is_allowed_by_robots("https://example.com/anything") is True

    def test_respect_robots_false_always_allowed(self, crawler: WebsiteCrawler) -> None:
        crawler._respect_robots = False
        mock_rp = MagicMock()
        mock_rp.can_fetch.return_value = False
        crawler._robot_parser = mock_rp
        # Even though parser says False, setting overrides it
        assert crawler.is_allowed_by_robots("https://example.com/blocked") is True

    def test_parser_consulted_when_respect_true(self, crawler: WebsiteCrawler) -> None:
        crawler._respect_robots = True
        mock_rp = MagicMock()
        mock_rp.can_fetch.return_value = True
        crawler._robot_parser = mock_rp
        assert crawler.is_allowed_by_robots("https://example.com/page") is True
        mock_rp.can_fetch.assert_called_once()


# ── Project directory creation ────────────────────────────────────────────────

class TestCreateProjectDir:
    def test_creates_required_subdirs(self, crawler: WebsiteCrawler, tmp_path: Path) -> None:
        crawler._settings = MagicMock()
        crawler._settings.projects_dir = tmp_path
        project_dir = crawler._create_project_dir("https://example.com/")
        for sub in ("pages", "images", "screenshots", "assets", "raw", "json"):
            assert (project_dir / sub).is_dir(), f"Missing subdir: {sub}"

    def test_strips_www_from_folder_name(self, crawler: WebsiteCrawler, tmp_path: Path) -> None:
        crawler._settings = MagicMock()
        crawler._settings.projects_dir = tmp_path
        project_dir = crawler._create_project_dir("https://www.example.com/")
        # www should not be in the folder name
        assert "www" not in project_dir.name

    def test_dots_replaced_with_dashes(self, crawler: WebsiteCrawler, tmp_path: Path) -> None:
        crawler._settings = MagicMock()
        crawler._settings.projects_dir = tmp_path
        project_dir = crawler._create_project_dir("https://example.com/")
        assert "." not in project_dir.name
