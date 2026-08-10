"""
webmaker.modules.website_crawler
================================
Deterministic, AI-free crawler that extracts every publicly accessible piece
of information from a target website and stores it locally in a structured
project directory.

Pipeline per crawl
------------------
1. Robots.txt loaded → disallowed paths filtered
2. Sitemap discovered → seeds the BFS queue
3. BFS over all same-domain HTML pages (bounded by max_pages / max_depth)
4. For every page: fetch HTML, parse metadata, take Playwright screenshot,
   save raw HTML and structured JSON
5. Download all discovered images and binary assets
6. Write aggregate JSON summaries (pages, images, navigation, crawl_summary)

Project output structure
------------------------
projects/<domain>/
  pages/        – cleaned plain-text page content
  images/       – downloaded images
  screenshots/  – full-page PNG screenshots
  assets/       – favicons, PDFs, and other downloads
  raw/          – original HTML files
  json/
    pages.json          – summary table of all crawled pages
    images.json         – full image inventory
    navigation.json     – aggregated site navigation
    crawl_summary.json  – crawl statistics and errors
    pages/              – per-page rich JSON

No AI. No LLM. No SEO optimisation.

Primary class: WebsiteCrawler
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urldefrag, urlparse, urlunparse

import requests
import requests.exceptions
import urllib3
from bs4 import BeautifulSoup
from playwright.sync_api import Browser, sync_playwright
from pydantic import BaseModel, Field
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from webmaker.core.exceptions import CrawlerError, RobotsBlockedError
from webmaker.core.logging import get_logger
from webmaker.core.schema import write_versioned_json
from webmaker.core.types import AssetReference, CrawlResult, PageData, PageType
from webmaker.utils.helpers import format_bytes, slugify

if TYPE_CHECKING:
    from webmaker.config.settings import Settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = get_logger("website_crawler")

_USER_AGENT = "WebMaker-Crawler/1.0 (research; not for production scraping)"

_IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif",
})
_ASSET_EXTENSIONS = frozenset({
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip",
})
# URL suffixes that should never be fetched as HTML pages
_SKIP_PAGE_EXTENSIONS = frozenset({
    ".js", ".css", ".woff", ".woff2", ".ttf", ".eot",
    ".mp4", ".mp3", ".avi", ".mov", ".mkv", ".wmv",
    ".json", ".xml", ".txt", ".rss", ".atom",
} | _IMAGE_EXTENSIONS | _ASSET_EXTENSIONS)


# ── Module-local rich data models ─────────────────────────────────────────────
# These are richer than the shared PageData and are only used within this
# module to produce JSON output for downstream consumers.

class _ImageMeta(BaseModel):
    source_url:      str
    local_path:      str  = ""
    filename:        str  = ""
    alt_text:        str  = ""
    width:           int  = 0
    height:          int  = 0
    file_size_bytes: int  = 0
    content_type:    str  = ""
    downloaded:      bool = False


class _OpenGraph(BaseModel):
    og_title:       str = ""
    og_description: str = ""
    og_image:       str = ""
    og_url:         str = ""
    og_type:        str = ""
    og_site_name:   str = ""


class _RichPage(BaseModel):
    """Full data model for one crawled page — saved to JSON."""

    url:             str
    final_url:       str            = ""
    status_code:     int            = 200
    title:           str            = ""
    meta_title:      str            = ""
    meta_description: str           = ""
    meta_robots:     str            = ""
    canonical_url:   str            = ""
    language:        str            = ""
    h1:              list[str]      = Field(default_factory=list)
    h2:              list[str]      = Field(default_factory=list)
    h3:              list[str]      = Field(default_factory=list)
    text_content:    str            = ""
    word_count:      int            = 0
    internal_links:  list[str]      = Field(default_factory=list)
    external_links:  list[str]      = Field(default_factory=list)
    images:          list[_ImageMeta] = Field(default_factory=list)
    assets:          list[str]      = Field(default_factory=list)
    open_graph:      _OpenGraph     = Field(default_factory=_OpenGraph)
    structured_data: list[dict]     = Field(default_factory=list)
    navigation:      list[dict]     = Field(default_factory=list)
    page_type:       str            = PageType.UNKNOWN.value
    screenshot_path: str            = ""
    raw_html_path:   str            = ""
    page_text_path:  str            = ""
    crawled_at:      str            = ""
    load_time_ms:    float          = 0.0
    errors:          list[str]      = Field(default_factory=list)


# ── Main class ────────────────────────────────────────────────────────────────

class WebsiteCrawler:
    """Crawls a public website and returns structured page data.

    Responsibilities:
    - Navigate all reachable pages within the same domain.
    - Capture full-page screenshots via Playwright.
    - Extract text content, headings, links, meta tags, OG tags, JSON-LD.
    - Respect robots.txt rules and configurable crawl depth / page limit.
    - Download images, favicons, PDFs, and other binary assets.
    - Persist raw HTML, extracted text, per-page JSON, and summary files.

    Args:
        settings: Application settings instance.
        cache_dir: Directory for downloaded assets (defaults to settings.cache_dir).
    """

    def __init__(self, settings: "Settings", cache_dir: Path | None = None) -> None:
        self._settings   = settings
        self._cache_dir  = cache_dir or settings.cache_dir
        self._max_pages  = settings.crawler_max_pages
        self._max_depth  = settings.crawler_max_depth
        self._timeout    = settings.crawler_timeout_s
        self._respect_robots = settings.crawler_respect_robots

        # Runtime state – reset at the start of each crawl()
        self._visited:        set[str]                        = set()
        self._image_registry: dict[str, _ImageMeta]          = {}
        self._asset_registry: dict[str, str]                  = {}
        self._session:        requests.Session | None         = None
        self._robot_parser:   urllib.robotparser.RobotFileParser | None = None
        self._base_url:       str                             = ""
        self._base_domain:    str                             = ""
        self._project_dir:    Path | None                     = None

        log.debug(
            "WebsiteCrawler initialised (max_pages={p}, max_depth={d}, timeout={t}s)",
            p=self._max_pages, d=self._max_depth, t=self._timeout,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def crawl(self, url: str, output_dir: Path | None = None) -> CrawlResult:
        """Crawl *url* and all reachable same-domain pages.

        Performs a full BFS crawl:
        - fetches and parses each HTML page
        - captures Playwright screenshots (cookie banners dismissed first)
        - downloads images and binary assets
        - writes structured JSON output to the project directory

        Args:
            url: Fully qualified target URL (must be http or https).
            output_dir: When set, write crawl output here instead of a
                        domain-slug folder (so named projects like ``DemoBiz``
                        own all target artifacts).

        Returns:
            CrawlResult with all discovered pages and a project_dir reference.

        Raises:
            CrawlerError: If *url* is invalid or entirely unreachable.
            RobotsBlockedError: If robots.txt forbids crawling the root URL.
        """
        url = self._normalise_url(url)
        if not url:
            raise CrawlerError("Invalid or unsupported URL", url=url)

        parsed = urlparse(url)
        self._base_domain = parsed.netloc
        self._base_url    = f"{parsed.scheme}://{parsed.netloc}"

        log.info("=== Starting crawl: {url} ===", url=url)

        self._session       = self._create_session()
        self._robot_parser  = self._load_robots(url)
        self._visited       = set()
        self._image_registry = {}
        self._asset_registry = {}

        if not self.is_allowed_by_robots(url):
            raise RobotsBlockedError(
                "Root URL is blocked by robots.txt — aborting crawl", url=url,
            )

        project_dir = self._create_project_dir(url, output_dir=output_dir)
        self._project_dir = project_dir

        # Seed queue: root URL + any sitemap URLs
        sitemap_urls  = self._fetch_sitemap_urls(url)
        queue: deque[str] = deque([url] + sitemap_urls)
        rich_pages: list[_RichPage] = []

        crawl_start = time.monotonic()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                while queue and len(self._visited) < self._max_pages:
                    current = self._normalise_url(queue.popleft())
                    if not current or current in self._visited:
                        continue
                    if not self._same_domain(current, url):
                        continue
                    if not self.is_allowed_by_robots(current):
                        log.debug("robots.txt skip: {u}", u=current)
                        continue
                    if Path(urlparse(current).path).suffix.lower() in _SKIP_PAGE_EXTENSIONS:
                        continue

                    self._visited.add(current)

                    try:
                        rp, new_links = self._crawl_full_page(current, project_dir, browser)
                        if rp:
                            rich_pages.append(rp)
                            for img in rp.images:
                                if img.source_url not in self._image_registry:
                                    self._image_registry[img.source_url] = img
                            for asset_url in rp.assets:
                                self._asset_registry.setdefault(asset_url, "")
                            for link in new_links:
                                norm = self._normalise_url(link)
                                if norm and norm not in self._visited:
                                    queue.append(norm)

                    except Exception as exc:
                        log.warning("Page failed ({u}): {e}", u=current, e=exc)
                        rich_pages.append(_RichPage(
                            url=current,
                            status_code=0,
                            errors=[str(exc)],
                            crawled_at=datetime.now(timezone.utc).isoformat(),
                        ))
            finally:
                browser.close()

        duration = time.monotonic() - crawl_start

        # ── Download images ───────────────────────────────────────────────────
        log.info("Downloading {n} unique images …", n=len(self._image_registry))
        for img_url, img_meta in self._image_registry.items():
            try:
                self._download_image(img_url, project_dir / "images", img_meta)
            except Exception as exc:
                log.warning("Image failed ({u}): {e}", u=img_url, e=exc)

        # ── Download binary assets ────────────────────────────────────────────
        log.info("Downloading {n} binary assets …", n=len(self._asset_registry))
        for asset_url in list(self._asset_registry):
            try:
                local = self._download_asset(asset_url, project_dir / "assets")
                self._asset_registry[asset_url] = local
            except Exception as exc:
                log.warning("Asset failed ({u}): {e}", u=asset_url, e=exc)

        # ── Write aggregate JSON ──────────────────────────────────────────────
        self._write_json_summaries(project_dir, rich_pages, url, duration)

        log.info(
            "=== Crawl complete: {n} pages in {t:.1f}s — output: {d} ===",
            n=len(rich_pages), t=duration, d=project_dir,
        )

        return self._to_crawl_result(url, rich_pages, duration)

    def crawl_page(self, url: str) -> PageData:
        """Retrieve and parse a single page without screenshots or downloads.

        Useful for ad-hoc inspection or re-parsing an already visited page.

        Args:
            url: Absolute URL to fetch.

        Returns:
            Populated PageData.

        Raises:
            CrawlerError: If the page cannot be fetched or is not HTML.
        """
        if not self._session:
            self._session = self._create_session()

        norm = self._normalise_url(url)
        if not norm:
            raise CrawlerError("Invalid URL", url=url)

        try:
            response = self._fetch(norm)
        except requests.exceptions.RequestException as exc:
            raise CrawlerError(str(exc), url=norm) from exc

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            raise CrawlerError(
                "URL does not serve HTML", url=norm, content_type=content_type,
            )

        soup = BeautifulSoup(response.text, "lxml")

        title_tag   = soup.find("title")
        title       = title_tag.get_text(strip=True) if title_tag else ""
        desc_tag    = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        description = desc_tag.get("content", "") if desc_tag else ""

        headings = (
            [h.get_text(strip=True) for h in soup.find_all("h1")] +
            [h.get_text(strip=True) for h in soup.find_all("h2")] +
            [h.get_text(strip=True) for h in soup.find_all("h3")]
        )
        internal, _ = self._extract_links(soup, norm)
        images_meta  = self._extract_images(soup, norm)

        for tag in soup(["script", "style", "noscript", "iframe"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True)).strip()

        page = PageData(
            url=norm,
            title=title,
            description=description,
            text_content=text,
            headings=headings,
            links=internal,
            images=[img.source_url for img in images_meta],
            status_code=response.status_code,
        )
        page.page_type = self.classify_page(page)
        return page

    def take_screenshot(self, url: str, output_path: Path) -> Path:
        """Capture a full-page screenshot of *url* with Playwright Chromium.

        Args:
            url:         Page to render.
            output_path: PNG destination (parent directories created if needed).

        Returns:
            Resolved absolute path to the saved file.

        Raises:
            CrawlerError: If Playwright cannot navigate to or render *url*.
        """
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    self._playwright_screenshot(browser, url, output_path)
                finally:
                    browser.close()
        except Exception as exc:
            raise CrawlerError(
                f"Screenshot failed: {exc}", url=url, path=str(output_path),
            ) from exc

        return output_path

    def extract_assets(self, page: PageData) -> list[str]:
        """Return all asset URLs already associated with *page*.

        For a richer asset list including favicons and PDFs, use
        ``crawl()`` which processes the full HTML.

        Args:
            page: Previously crawled PageData.

        Returns:
            Deduplicated list of asset URLs (images + declared assets).
        """
        seen: set[str] = set()
        result: list[str] = []
        for url in page.images + [a.url for a in page.assets]:
            if url not in seen:
                seen.add(url)
                result.append(url)
        return result

    def classify_page(self, page: PageData) -> PageType:
        """Infer the semantic role of a page from its URL and content.

        Uses URL path patterns first (fast, reliable), then title keywords
        as a fallback.

        Args:
            page: PageData with at least *url* and *title* populated.

        Returns:
            PageType enum value.
        """
        path = urlparse(page.url).path.lower().strip("/")
        title = page.title.lower()

        # Root path → home
        if not path or path in ("index.html", "index.php", "home"):
            return PageType.HOME

        segments = path.split("/")
        first = segments[0]

        _about    = re.compile(r"about|ueber|uber|wir|team|uns")
        _contact  = re.compile(r"contact|kontakt|reach|anschrift")
        _services = re.compile(r"service|leistung|angebot|dienstleistung|portfolio|offer|solution")
        _blog     = re.compile(r"blog|news|artikel|beitrag|post|press|presse|aktuell")
        _product  = re.compile(r"product|produkt|shop|store|kauf|buy|item")
        _gallery  = re.compile(r"galerie|gallery|foto|photo|bild|image|media")

        for pattern, page_type in [
            (_about,    PageType.ABOUT),
            (_contact,  PageType.CONTACT),
            (_services, PageType.SERVICES),
            (_blog,     PageType.BLOG),
            (_product,  PageType.PRODUCT),
            (_gallery,  PageType.GALLERY),
        ]:
            if pattern.search(first):
                return page_type

        # Title fallback
        if re.search(r"home|welcome|willkommen|startseite", title):
            return PageType.HOME
        if _about.search(title):
            return PageType.ABOUT
        if _contact.search(title):
            return PageType.CONTACT
        if _services.search(title):
            return PageType.SERVICES

        return PageType.UNKNOWN

    def is_allowed_by_robots(self, url: str) -> bool:
        """Check whether *url* is permitted by the site's robots.txt.

        Always returns True if ``crawler_respect_robots`` is disabled in
        settings or no robots.txt could be loaded.

        Args:
            url: Absolute URL to check.

        Returns:
            True if the crawler is allowed to fetch this URL.
        """
        if not self._respect_robots:
            return True
        if self._robot_parser is None:
            return True
        return bool(self._robot_parser.can_fetch(_USER_AGENT, url))

    # ── Private: URL helpers ───────────────────────────────────────────────────

    def _normalise_url(self, url: str) -> str:
        """Strip fragment, normalise scheme+host to lowercase, ensure path."""
        if not url or not isinstance(url, str):
            return ""
        url = url.strip()
        url, _ = urldefrag(url)   # remove #anchor

        try:
            p = urlparse(url)
        except Exception:
            return ""

        if p.scheme not in ("http", "https"):
            return ""

        scheme = p.scheme.lower()
        netloc = p.netloc.lower()
        path   = p.path if p.path else "/"

        # Strip trailing slash from non-root paths for consistency
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        return urlunparse((scheme, netloc, path, p.params, p.query, ""))

    def _same_domain(self, url: str, base: str) -> bool:
        """Return True if *url* shares the registered domain with *base*.

        www/non-www variants are treated as the same domain.

        Args:
            url:  URL to test.
            base: Reference base URL.

        Returns:
            True if both URLs share the same effective domain.
        """
        try:
            url_host  = urlparse(url).netloc.lower()
            base_host = urlparse(base).netloc.lower()
        except Exception:
            return False

        if url_host == base_host:
            return True

        # Treat www.example.com and example.com as the same
        return url_host.removeprefix("www.") == base_host.removeprefix("www.")

    def _cache_path(self, url: str) -> Path:
        """Compute a cache file path for *url* using an MD5 hash.

        Args:
            url: URL to hash.

        Returns:
            Path under the cache directory (two-level hash sharding).
        """
        h = hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()
        return self._cache_dir / h[:2] / h

    def _url_to_slug(self, url: str) -> str:
        """Convert a URL path to a filesystem-safe, human-readable slug.

        Args:
            url: Absolute URL.

        Returns:
            Slug string, e.g. ``"services-web-design"``.
        """
        path = urlparse(url).path.strip("/")
        if not path:
            return "home"
        raw  = re.sub(r"[^a-zA-Z0-9\-_]", "-", path)
        slug = re.sub(r"-+", "-", raw).strip("-")
        return (slug or "home")[:100]

    # ── Private: HTTP ──────────────────────────────────────────────────────────

    def _create_session(self) -> requests.Session:
        """Build a requests.Session with retries and a sensible User-Agent."""
        session = requests.Session()
        session.headers.update({
            "User-Agent": _USER_AGENT,
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
        })
        retry = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://",  adapter)
        session.mount("https://", adapter)
        return session

    def _fetch(self, url: str, *, stream: bool = False, timeout: float | None = None) -> requests.Response:
        """GET *url*, falling back to unverified SSL on certificate errors.

        Args:
            url:     Absolute URL to fetch.
            stream:  Pass True for streaming large binary responses.
            timeout: Override default timeout in seconds.

        Returns:
            requests.Response.

        Raises:
            requests.exceptions.RequestException: On network-level failure.
        """
        t = timeout or self._timeout
        try:
            return self._session.get(url, timeout=t, stream=stream, allow_redirects=True)
        except requests.exceptions.SSLError:
            log.warning("SSL error for {u} — retrying unverified", u=url)
            return self._session.get(url, timeout=t, stream=stream, allow_redirects=True, verify=False)

    # ── Private: robots.txt ────────────────────────────────────────────────────

    def _load_robots(self, base_url: str) -> urllib.robotparser.RobotFileParser:
        """Fetch and parse robots.txt for *base_url*.

        Args:
            base_url: Root URL of the target site.

        Returns:
            Configured RobotFileParser (empty/permissive if unavailable).
        """
        rp = urllib.robotparser.RobotFileParser()
        robots_url = urljoin(base_url, "/robots.txt")
        try:
            response = self._session.get(robots_url, timeout=10)
            if response.status_code == 200:
                rp.parse(response.text.splitlines())
                log.debug("Loaded robots.txt ({u})", u=robots_url)
            else:
                log.debug("No robots.txt at {u} ({s})", u=robots_url, s=response.status_code)
        except Exception as exc:
            log.debug("Could not load robots.txt: {e}", e=exc)
        return rp

    # ── Private: sitemap ───────────────────────────────────────────────────────

    def _fetch_sitemap_urls(self, base_url: str) -> list[str]:
        """Collect page URLs from sitemap.xml (and sitemap hints in robots.txt).

        Returns a deduplicated, normalised list of same-domain URLs.

        Args:
            base_url: Root URL of the target site.

        Returns:
            List of discovered page URLs.
        """
        # 1 — sitemap hints from robots.txt
        sitemap_locations: list[str] = self._sitemap_locations_from_robots(base_url)

        # 2 — fall back to canonical locations
        if not sitemap_locations:
            sitemap_locations = [
                urljoin(base_url, "/sitemap.xml"),
                urljoin(base_url, "/sitemap_index.xml"),
            ]

        raw_urls: list[str] = []
        for loc in sitemap_locations:
            try:
                found = self._parse_sitemap(loc)
                log.debug("Sitemap {u}: {n} URLs", u=loc, n=len(found))
                raw_urls.extend(found)
            except Exception as exc:
                log.debug("Sitemap unavailable ({u}): {e}", u=loc, e=exc)

        result: list[str] = []
        seen:   set[str]  = set()
        for raw in raw_urls:
            norm = self._normalise_url(raw)
            if norm and norm not in seen and self._same_domain(norm, base_url):
                seen.add(norm)
                result.append(norm)

        return result

    def _sitemap_locations_from_robots(self, base_url: str) -> list[str]:
        """Extract ``Sitemap:`` directives from robots.txt.

        Args:
            base_url: Root URL of the target site.

        Returns:
            List of sitemap URLs found in robots.txt.
        """
        locations: list[str] = []
        try:
            resp = self._session.get(urljoin(base_url, "/robots.txt"), timeout=10)
            if resp.status_code == 200:
                for line in resp.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        loc = line.split(":", 1)[1].strip()
                        if loc:
                            locations.append(loc)
        except Exception:
            pass
        return locations

    def _parse_sitemap(self, sitemap_url: str, _depth: int = 0) -> list[str]:
        """Recursively parse a sitemap or sitemap index XML.

        Args:
            sitemap_url: URL of the sitemap document.
            _depth:      Recursion depth guard (max 2).

        Returns:
            Flat list of page URL strings.
        """
        if _depth > 2:
            return []
        try:
            resp = self._session.get(sitemap_url, timeout=15)
            if resp.status_code != 200:
                return []
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            log.debug("Sitemap XML invalid ({u}): {e}", u=sitemap_url, e=exc)
            return []
        except Exception:
            return []

        # Strip XML namespace for simpler access
        ns_match = re.match(r"\{(.+?)\}", root.tag)
        ns = f"{{{ns_match.group(1)}}}" if ns_match else ""

        urls: list[str] = []

        # Sitemap index → recurse
        for sm in root.findall(f"{ns}sitemap"):
            loc = sm.find(f"{ns}loc")
            if loc is not None and loc.text:
                urls.extend(self._parse_sitemap(loc.text.strip(), _depth + 1))

        # URL set
        for url_el in root.findall(f"{ns}url"):
            loc = url_el.find(f"{ns}loc")
            if loc is not None and loc.text:
                urls.append(loc.text.strip())

        return urls

    # ── Private: directory management ─────────────────────────────────────────

    def _create_project_dir(
        self, url: str, output_dir: Path | None = None,
    ) -> Path:
        """Create the full project directory tree for a crawl.

        Args:
            url: Root URL of the crawled site.
            output_dir: Optional fixed root. When omitted, uses a domain-slug
                        folder under ``projects_dir``.

        Returns:
            Path to the project root directory.
        """
        if output_dir is not None:
            project_dir = Path(output_dir)
        else:
            netloc = urlparse(url).netloc
            # Build folder name: strip www., replace . and : with -
            folder = re.sub(r"[.:]", "-", netloc.removeprefix("www."))
            folder = re.sub(r"-+", "-", folder).strip("-") or "crawl"
            project_dir = self._settings.projects_dir / folder

        for sub in ("pages", "images", "screenshots", "assets", "raw",
                    "json", "json/pages", "artifacts"):
            (project_dir / sub).mkdir(parents=True, exist_ok=True)

        log.info("Project dir: {d}", d=project_dir)
        return project_dir

    # ── Private: full-page crawl ───────────────────────────────────────────────

    def _crawl_full_page(
        self,
        url: str,
        project_dir: Path,
        browser: Browser,
    ) -> tuple[_RichPage | None, list[str]]:
        """Fetch, parse, screenshot, and persist one page.

        Args:
            url:         Normalised page URL.
            project_dir: Root of the project output tree.
            browser:     Open Playwright Browser instance.

        Returns:
            Tuple of (_RichPage or None, list of discovered internal links).
        """
        slug = self._url_to_slug(url)
        log.info("Crawling [{n}/{m}]: {u}", n=len(self._visited), m=self._max_pages, u=url)

        t0 = time.monotonic()
        try:
            resp = self._fetch(url)
        except requests.exceptions.RequestException as exc:
            log.warning("Fetch error ({u}): {e}", u=url, e=exc)
            return None, []

        load_ms = (time.monotonic() - t0) * 1000

        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type:
            log.debug("Skip non-HTML ({u}, {ct})", u=url, ct=content_type.split(";")[0])
            return None, []

        html = resp.text

        # ── Save raw HTML ─────────────────────────────────────────────────────
        raw_path = project_dir / "raw" / f"{slug}.html"
        try:
            raw_path.write_text(html, encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("Could not save raw HTML: {e}", e=exc)

        soup = BeautifulSoup(html, "lxml")

        # ── Populate rich page record ─────────────────────────────────────────
        rp = _RichPage(
            url=url,
            final_url=resp.url,
            status_code=resp.status_code,
            crawled_at=datetime.now(timezone.utc).isoformat(),
            load_time_ms=round(load_ms, 2),
            raw_html_path=f"raw/{slug}.html",
        )

        self._parse_meta(soup, rp)
        self._parse_headings(soup, rp)
        self._parse_structured_data(soup, rp)

        internal, external = self._extract_links(soup, url)
        rp.internal_links = internal
        rp.external_links = external

        rp.navigation = self._extract_navigation(soup, url)
        rp.images     = self._extract_images(soup, url)
        rp.assets     = self._extract_asset_urls(soup, url)

        # ── Text content (must come after all BS4 extractions) ────────────────
        for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
            tag.decompose()
        raw_text = re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True)).strip()
        rp.text_content = raw_text
        rp.word_count   = len(raw_text.split())

        # ── Page type ─────────────────────────────────────────────────────────
        rp.page_type = self.classify_page(PageData(
            url=url, title=rp.title, text_content=rp.text_content,
            headings=rp.h1 + rp.h2 + rp.h3,
        )).value

        # ── Save plain-text page ──────────────────────────────────────────────
        txt_path = project_dir / "pages" / f"{slug}.txt"
        try:
            txt_path.write_text(raw_text, encoding="utf-8", errors="replace")
            rp.page_text_path = f"pages/{slug}.txt"
        except OSError:
            pass

        # ── Playwright screenshot ─────────────────────────────────────────────
        ss_path = project_dir / "screenshots" / f"{slug}.png"
        try:
            self._playwright_screenshot(browser, url, ss_path)
            rp.screenshot_path = f"screenshots/{slug}.png"
        except Exception as exc:
            log.warning("Screenshot failed ({u}): {e}", u=url, e=exc)
            rp.errors.append(f"screenshot: {exc}")

        # ── Per-page JSON ─────────────────────────────────────────────────────
        page_json = project_dir / "json" / "pages" / f"{slug}.json"
        try:
            write_versioned_json(page_json, rp.model_dump())
        except OSError as exc:
            log.warning("Could not write page JSON: {e}", e=exc)

        log.info(
            "  → {s} | {n_i} links | {n_img} images | {wc} words",
            s=resp.status_code, n_i=len(internal), n_img=len(rp.images), wc=rp.word_count,
        )
        return rp, internal

    # ── Private: HTML parsing ──────────────────────────────────────────────────

    def _parse_meta(self, soup: BeautifulSoup, rp: _RichPage) -> None:
        """Populate meta fields on *rp* from the parsed *soup*.

        Extracts: title, meta description, meta robots, canonical URL,
        lang attribute, and all Open Graph properties.

        Args:
            soup: Parsed BeautifulSoup tree.
            rp:   _RichPage to populate (mutated in place).
        """
        title_tag = soup.find("title")
        if title_tag:
            rp.title = title_tag.get_text(strip=True)

        html_tag = soup.find("html")
        if html_tag and isinstance(html_tag.get("lang"), str):
            rp.language = html_tag["lang"]

        canonical = soup.find("link", rel="canonical")
        if canonical and canonical.get("href"):
            rp.canonical_url = canonical["href"]

        for meta in soup.find_all("meta"):
            name    = (meta.get("name") or "").lower().strip()
            prop    = (meta.get("property") or "").lower().strip()
            content = meta.get("content", "")

            if name == "description":
                rp.meta_description = content
            elif name == "title":
                rp.meta_title = content
            elif name == "robots":
                rp.meta_robots = content
            elif prop == "og:title":
                rp.open_graph.og_title = content
            elif prop == "og:description":
                rp.open_graph.og_description = content
            elif prop == "og:image":
                rp.open_graph.og_image = content
            elif prop == "og:url":
                rp.open_graph.og_url = content
            elif prop == "og:type":
                rp.open_graph.og_type = content
            elif prop == "og:site_name":
                rp.open_graph.og_site_name = content

    def _parse_headings(self, soup: BeautifulSoup, rp: _RichPage) -> None:
        """Extract H1 / H2 / H3 text into *rp*.

        Args:
            soup: Parsed BeautifulSoup tree.
            rp:   _RichPage to populate (mutated in place).
        """
        rp.h1 = [h.get_text(strip=True) for h in soup.find_all("h1") if h.get_text(strip=True)]
        rp.h2 = [h.get_text(strip=True) for h in soup.find_all("h2") if h.get_text(strip=True)]
        rp.h3 = [h.get_text(strip=True) for h in soup.find_all("h3") if h.get_text(strip=True)]

    def _parse_structured_data(self, soup: BeautifulSoup, rp: _RichPage) -> None:
        """Extract JSON-LD structured data blocks into *rp*.

        Args:
            soup: Parsed BeautifulSoup tree.
            rp:   _RichPage to populate (mutated in place).
        """
        for script in soup.find_all("script", type="application/ld+json"):
            raw = script.string or ""
            if not raw.strip():
                continue
            try:
                data = json.loads(raw)
                rp.structured_data.append(data)
            except json.JSONDecodeError as exc:
                log.debug("JSON-LD parse error: {e}", e=exc)

    def _extract_links(
        self, soup: BeautifulSoup, base_url: str,
    ) -> tuple[list[str], list[str]]:
        """Return (internal_links, external_links) for all <a href> in *soup*.

        Args:
            soup:     Parsed BeautifulSoup tree.
            base_url: URL of the page being parsed (used for relative resolution).

        Returns:
            Tuple of (internal URL list, external URL list), both deduplicated.
        """
        internal: list[str] = []
        external: list[str] = []
        seen:     set[str]  = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
                continue
            abs_url = urljoin(base_url, href)
            norm    = self._normalise_url(abs_url)
            if not norm or norm in seen:
                continue
            seen.add(norm)

            if self._same_domain(norm, base_url):
                internal.append(norm)
            else:
                external.append(norm)

        return internal, external

    def _extract_navigation(
        self, soup: BeautifulSoup, base_url: str,
    ) -> list[dict]:
        """Extract navigation links from <nav> and <header> elements.

        Args:
            soup:     Parsed BeautifulSoup tree.
            base_url: Base URL for resolving relative hrefs.

        Returns:
            List of dicts with ``text`` and ``url`` keys.
        """
        items: list[dict] = []
        seen:  set[str]   = set()

        for container in soup.find_all(["nav", "header"]):
            for a in container.find_all("a", href=True):
                href    = a["href"].strip()
                text    = a.get_text(strip=True)
                if not href or href.startswith(("#", "javascript:", "mailto:")):
                    continue
                abs_url = urljoin(base_url, href)
                norm    = self._normalise_url(abs_url)
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                items.append({"text": text, "url": norm})

        return items

    def _extract_images(
        self, soup: BeautifulSoup, base_url: str,
    ) -> list[_ImageMeta]:
        """Collect all <img> references including lazy-loaded variants.

        Checks ``src``, ``data-src``, ``data-lazy-src``, and ``data-original``.

        Args:
            soup:     Parsed BeautifulSoup tree.
            base_url: Base URL for resolving relative hrefs.

        Returns:
            Deduplicated list of _ImageMeta objects.
        """
        images: list[_ImageMeta] = []
        seen:   set[str]         = set()

        for img in soup.find_all("img"):
            src = (
                img.get("src") or
                img.get("data-src") or
                img.get("data-lazy-src") or
                img.get("data-original") or
                ""
            )
            if not src or src.startswith("data:"):
                continue

            abs_url = urljoin(base_url, src.strip())
            norm    = self._normalise_url(abs_url)
            if not norm or norm in seen:
                continue
            seen.add(norm)

            w = h = 0
            try:
                w = int(img.get("width") or 0)
                h = int(img.get("height") or 0)
            except (ValueError, TypeError):
                pass

            filename = Path(urlparse(norm).path).name or ""
            images.append(_ImageMeta(
                source_url=norm,
                alt_text=img.get("alt", ""),
                width=w,
                height=h,
                filename=filename,
            ))

        return images

    def _extract_asset_urls(
        self, soup: BeautifulSoup, base_url: str,
    ) -> list[str]:
        """Discover favicon, PDF, and other binary asset URLs.

        Scans <link rel="icon">, <link rel="shortcut icon">, and <a href>
        links pointing to known binary extensions.

        Args:
            soup:     Parsed BeautifulSoup tree.
            base_url: Base URL for resolving relative hrefs.

        Returns:
            Deduplicated list of absolute asset URLs.
        """
        assets: list[str] = []
        seen:   set[str]  = set()

        # Favicons and icons
        for link in soup.find_all("link"):
            rel  = " ".join(link.get("rel", [])).lower()
            href = link.get("href", "")
            if "icon" in rel and href:
                abs_url = urljoin(base_url, href)
                norm    = self._normalise_url(abs_url)
                if norm and norm not in seen:
                    seen.add(norm)
                    assets.append(norm)

        # Binary downloads linked from page text
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            ext  = Path(urlparse(href).path).suffix.lower()
            if ext in _ASSET_EXTENSIONS:
                abs_url = urljoin(base_url, href)
                norm    = self._normalise_url(abs_url)
                if norm and norm not in seen:
                    seen.add(norm)
                    assets.append(norm)

        return assets

    # ── Private: Playwright ────────────────────────────────────────────────────

    def _playwright_screenshot(
        self, browser: Browser, url: str, output_path: Path,
    ) -> None:
        """Render *url* with Playwright and save a full-page PNG.

        Args:
            browser:     Open Playwright Browser.
            url:         Page to render.
            output_path: PNG destination.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            page.goto(
                url,
                timeout=int(self._timeout * 1000),
                wait_until="networkidle",
            )
            self._dismiss_cookie_banners(page)
            page.screenshot(path=str(output_path), full_page=True)
            log.debug("Screenshot → {f}", f=output_path.name)
        finally:
            page.close()

    @staticmethod
    def _dismiss_cookie_banners(page) -> None:
        """Click German/EU cookie consent accept-all before capturing SS."""
        # Prefer full accept so the banner disappears for clean screenshots.
        labels = (
            "Alle akzeptieren",
            "Alles akzeptieren",
            "Accept all",
            "Accept All",
            "Alle Cookies akzeptieren",
            "Zustimmen",
            "Ich stimme zu",
            "Allow all",
        )
        for label in labels:
            try:
                btn = page.get_by_role("button", name=re.compile(
                    re.escape(label), re.IGNORECASE,
                ))
                if btn.count() > 0:
                    btn.first.click(timeout=2500)
                    page.wait_for_timeout(400)
                    log.debug("Cookie banner dismissed via: {l}", l=label)
                    return
            except Exception:
                pass
            try:
                loc = page.locator(
                    f"button:has-text('{label}'), "
                    f"a:has-text('{label}'), "
                    f"[role='button']:has-text('{label}')"
                )
                if loc.count() > 0:
                    loc.first.click(timeout=2500)
                    page.wait_for_timeout(400)
                    log.debug("Cookie banner dismissed via text: {l}", l=label)
                    return
            except Exception:
                pass
        # Common CMP selectors (Borlabs / Cookiebot / Usercentrics / OneTrust)
        for sel in (
            "#CookieBoxSaveButton",
            "a.cc-btn.cc-allow",
            "button#onetrust-accept-btn-handler",
            "button[data-testid='uc-accept-all-button']",
            ".cm-btn-accept-all",
            "#acceptAllButton",
        ):
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=2500)
                    page.wait_for_timeout(400)
                    log.debug("Cookie banner dismissed via selector: {s}", s=sel)
                    return
            except Exception:
                pass

    # ── Private: downloads ─────────────────────────────────────────────────────

    def _download_image(
        self, url: str, dest_dir: Path, meta: _ImageMeta,
    ) -> None:
        """Stream-download an image to *dest_dir* and update *meta*.

        Skips the download if the Content-Type is not image/* or SVG.

        Args:
            url:      Absolute image URL.
            dest_dir: Destination directory (created if missing).
            meta:     _ImageMeta object to update with local path and size.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)

        filename = Path(urlparse(url).path).name
        if not filename or "." not in filename:
            h = hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()[:8]
            filename = f"img_{h}"

        dest_path = self._unique_path(dest_dir, filename)

        try:
            resp = self._fetch(url, stream=True, timeout=20)
            ct   = resp.headers.get("content-type", "")
            if not any(k in ct for k in ("image/", "svg", "octet-stream")):
                return

            size = 0
            with dest_path.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
                    size += len(chunk)

            meta.local_path      = str(dest_path)
            meta.filename        = dest_path.name
            meta.file_size_bytes = size
            meta.content_type    = ct
            meta.downloaded      = True
            log.debug("Image: {fn} ({sz})", fn=dest_path.name, sz=format_bytes(size))

        except Exception as exc:
            log.warning("Image download failed ({u}): {e}", u=url, e=exc)

    def _download_asset(self, url: str, dest_dir: Path) -> str:
        """Download a binary asset (PDF, favicon, etc.) to *dest_dir*.

        Args:
            url:      Absolute asset URL.
            dest_dir: Destination directory.

        Returns:
            Absolute path of the saved file, or empty string on failure.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)

        filename = Path(urlparse(url).path).name
        if not filename:
            h = hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()[:8]
            ext = Path(urlparse(url).path).suffix or ""
            filename = f"asset_{h}{ext}"

        dest_path = self._unique_path(dest_dir, filename)

        try:
            resp = self._fetch(url, stream=True, timeout=30)
            resp.raise_for_status()
            with dest_path.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
            log.debug("Asset: {fn}", fn=dest_path.name)
            return str(dest_path)
        except Exception as exc:
            log.warning("Asset download failed ({u}): {e}", u=url, e=exc)
            return ""

    @staticmethod
    def _unique_path(directory: Path, filename: str) -> Path:
        """Return a path that does not yet exist in *directory*.

        Appends ``_1``, ``_2`` … to the stem if the filename is taken.

        Args:
            directory: Target directory.
            filename:  Desired filename.

        Returns:
            A non-existent Path inside *directory*.
        """
        candidate = directory / filename
        if not candidate.exists():
            return candidate
        stem   = Path(filename).stem
        suffix = Path(filename).suffix
        i = 1
        while True:
            candidate = directory / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                return candidate
            i += 1

    # ── Private: JSON output ───────────────────────────────────────────────────

    def _write_json_summaries(
        self,
        project_dir: Path,
        pages:        list[_RichPage],
        target_url:   str,
        duration_s:   float,
    ) -> None:
        """Write aggregate JSON summary files to ``project_dir/json/``.

        Files written:
        - ``pages.json``         – one-row-per-page summary table
        - ``images.json``        – full image inventory
        - ``navigation.json``    – aggregated site navigation
        - ``crawl_summary.json`` – statistics and top-level metadata

        Args:
            project_dir: Root of the project output tree.
            pages:       All crawled _RichPage records.
            target_url:  Root URL of the crawl.
            duration_s:  Total crawl duration in seconds.
        """
        jdir = project_dir / "json"

        # pages.json
        pages_summary = [
            {
                "url":            p.url,
                "title":          p.title,
                "status_code":    p.status_code,
                "page_type":      p.page_type,
                "word_count":     p.word_count,
                "internal_links": len(p.internal_links),
                "external_links": len(p.external_links),
                "images":         len(p.images),
                "has_screenshot": bool(p.screenshot_path),
                "load_time_ms":   p.load_time_ms,
                "crawled_at":     p.crawled_at,
            }
            for p in pages
        ]
        self._write_json(jdir / "pages.json", pages_summary)

        # images.json
        all_images = [img.model_dump() for p in pages for img in p.images]
        # Merge metadata from download into each entry
        for entry in all_images:
            final = self._image_registry.get(entry["source_url"])
            if final:
                entry.update(final.model_dump())
        self._write_json(jdir / "images.json", all_images)

        # navigation.json
        nav_map: dict[str, dict] = {}
        for p in pages:
            for item in p.navigation:
                nav_map.setdefault(item["url"], item)
        self._write_json(jdir / "navigation.json", list(nav_map.values()))

        # crawl_summary.json
        all_errors = [e for p in pages for e in p.errors]
        summary = {
            "target_url":         target_url,
            "project_dir":        str(project_dir),
            "total_pages":        len(pages),
            "successful_pages":   sum(1 for p in pages if 200 <= p.status_code < 300),
            "failed_pages":       sum(1 for p in pages if p.status_code == 0 or p.status_code >= 400),
            "total_images":       len(self._image_registry),
            "downloaded_images":  sum(1 for m in self._image_registry.values() if m.downloaded),
            "total_assets":       len(self._asset_registry),
            "total_internal_links": sum(len(p.internal_links) for p in pages),
            "total_external_links": sum(len(p.external_links) for p in pages),
            "page_types":         {},
            "crawl_duration_s":   round(duration_s, 2),
            "errors":             all_errors[:100],
            "completed_at":       datetime.now(timezone.utc).isoformat(),
        }
        # Count page types
        for p in pages:
            summary["page_types"][p.page_type] = summary["page_types"].get(p.page_type, 0) + 1

        self._write_json(jdir / "crawl_summary.json", summary)
        log.info("JSON summaries written → {d}", d=jdir)

    @staticmethod
    def _write_json(path: Path, data) -> None:
        """Serialise *data* to *path* as versioned UTF-8 JSON."""
        write_versioned_json(path, data)

    # ── Private: result conversion ─────────────────────────────────────────────

    def _to_crawl_result(
        self,
        target_url: str,
        rich_pages: list[_RichPage],
        duration_s: float,
    ) -> CrawlResult:
        """Convert internal _RichPage records to the shared CrawlResult type.

        Args:
            target_url:  Root URL of the crawl.
            rich_pages:  All crawled _RichPage records.
            duration_s:  Total crawl duration in seconds.

        Returns:
            Populated CrawlResult using the shared PageData model.
        """
        page_data_list: list[PageData] = []
        all_errors:     list[str]      = []

        for rp in rich_pages:
            try:
                page_type = PageType(rp.page_type)
            except ValueError:
                page_type = PageType.UNKNOWN

            assets: list[AssetReference] = []
            for img in rp.images:
                assets.append(AssetReference(
                    url=img.source_url,
                    asset_type="image",
                    local_path=Path(img.local_path) if img.local_path else None,
                    size_bytes=img.file_size_bytes,
                ))
            for asset_url in rp.assets:
                local = self._asset_registry.get(asset_url, "")
                assets.append(AssetReference(
                    url=asset_url,
                    asset_type=self._classify_asset_type(asset_url),
                    local_path=Path(local) if local else None,
                ))

            page_data_list.append(PageData(
                url=rp.url,
                title=rp.title,
                description=rp.meta_description,
                page_type=page_type,
                text_content=rp.text_content,
                headings=rp.h1 + rp.h2 + rp.h3,
                links=rp.internal_links,
                images=[img.source_url for img in rp.images],
                assets=assets,
                screenshot_path=Path(self._project_dir / rp.screenshot_path)
                    if (rp.screenshot_path and self._project_dir) else None,
                status_code=rp.status_code,
            ))
            all_errors.extend(rp.errors)

        return CrawlResult(
            target_url=target_url,
            pages=page_data_list,
            total_pages=len(page_data_list),
            crawl_duration_s=round(duration_s, 2),
            errors=all_errors,
        )

    @staticmethod
    def _classify_asset_type(url: str) -> str:
        """Return a string asset type based on the URL file extension.

        Args:
            url: Asset URL.

        Returns:
            One of ``"pdf"``, ``"image"``, ``"favicon"``, or ``"file"``.
        """
        ext = Path(urlparse(url).path).suffix.lower()
        if ext == ".pdf":
            return "pdf"
        if ext in _IMAGE_EXTENSIONS:
            return "image"
        if ext in (".ico", ".png") and "favicon" in url.lower():
            return "favicon"
        return "file"
