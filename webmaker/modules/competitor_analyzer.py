"""
webmaker.modules.competitor_analyzer
=====================================
Analyses competitor websites and compares them against the client's business
profile to surface structural, organisational, and presentational ideas that
can improve the client's future website.

Pipeline
--------
1. Load client context from business_profile.json (written by BusinessAnalyzer)
2. For each provided competitor URL:
   a. Crawl via WebsiteCrawler (no duplicated logic)
   b. Run AI-powered competitor profiling via AIRouter
   c. Persist per-competitor JSON
3. Run AI-powered comparison: client profile vs all competitor profiles
4. Write aggregate JSON output:
   - competitors.json           — one-row-per-competitor summary
   - competitor_analysis.json   — full per-competitor profiles
   - comparison_report.json     — structural ideas and gaps
5. Return shared AnalysisResult for downstream modules

Important constraints
---------------------
- Never copy competitor content, wording, or design.
- Only identify IDEAS, APPROACHES, and STRUCTURAL OPPORTUNITIES.
- All AI calls go through AIRouter — no provider SDK imported here.
- Errors in one competitor do not abort the rest.

Primary class: CompetitorAnalyzer
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from webmaker.core.exceptions import AIError, AnalysisError, CrawlerError
from webmaker.core.logging import get_logger
from webmaker.core.prompts import load_prompt_or_default
from webmaker.core.schema import unwrap_json, write_versioned_json
from webmaker.core.types import (
    AIProvider, AnalysisResult, BusinessInfo, CompetitorInfo, CrawlResult,
)
from webmaker.modules.ai_router import AIRouter
from webmaker.modules.website_crawler import WebsiteCrawler

if TYPE_CHECKING:
    from webmaker.config.settings import Settings

log = get_logger("competitor_analyzer")


# ── Constants ─────────────────────────────────────────────────────────────────

_FALLBACK_SYSTEM_PROMPT = (
    "You are an expert competitive intelligence analyst. "
    "Analyse website content and extract structured competitor data. "
    "Respond ONLY with a single valid JSON object — no markdown, "
    "no code fences, no text before or after the JSON."
)

_MAX_CONTENT_CHARS = 10_000   # total page text chars sent to AI per competitor
_MAX_PAGES_IN_PROMPT = 8      # max pages included in each competitor prompt

# Fields that must always be lists in AI responses
_LIST_FIELDS = frozenset({
    "main_services", "trust_signals", "contact_methods", "service_areas",
    "faq_topics", "strengths", "weaknesses",
    "features_competitors_have", "navigation_ideas", "trust_building_elements",
    "homepage_structure_ideas", "service_presentation_ideas",
    "customer_journey_ideas", "content_organization_ideas", "cta_ideas",
    "faq_ideas", "image_presentation_ideas", "local_business_ideas",
    "overall_opportunities",
})

# Page type priority for prompt ordering
_PAGE_PRIORITY: dict[str, int] = {
    "home": 0, "about": 1, "services": 2, "contact": 3,
    "product": 4, "blog": 5, "gallery": 6, "unknown": 9,
}


# ── Module-local data models ──────────────────────────────────────────────────

class _CompetitorProfile(BaseModel):
    """AI-extracted profile for one competitor website."""

    company_name:        str       = ""
    business_category:   str       = ""
    main_services:       list[str] = Field(default_factory=list)
    service_structure:   str       = ""   # single-page | multi-page | landing-page
    navigation_structure: str      = ""   # simple | standard | mega-menu | complex
    homepage_layout:     str       = ""
    cta_strategy:        str       = ""
    trust_signals:       list[str] = Field(default_factory=list)
    contact_methods:     list[str] = Field(default_factory=list)
    service_areas:       list[str] = Field(default_factory=list)
    has_faq:             bool      = False
    faq_topics:          list[str] = Field(default_factory=list)
    image_usage:         str       = ""   # minimal | moderate | heavy
    content_quality:     str       = ""   # poor | basic | adequate | good | excellent
    content_depth:       str       = ""   # shallow | moderate | deep
    brand_tone:          str       = ""
    customer_focus:      str       = ""
    strengths:           list[str] = Field(default_factory=list)
    weaknesses:          list[str] = Field(default_factory=list)


class _CompetitorEntry(BaseModel):
    """One competitor with crawl status, AI profile, and error log."""

    url:             str                = ""
    crawled:         bool               = False
    project_dir:     str                = ""
    profile:         _CompetitorProfile = Field(default_factory=_CompetitorProfile)
    crawl_errors:    list[str]          = Field(default_factory=list)
    analysis_errors: list[str]          = Field(default_factory=list)
    analyzed_at:     str                = ""
    markdown_summary: str               = ""


class _ClientContext(BaseModel):
    """Minimal client business data used to frame competitor analysis."""

    company_name:     str       = ""
    industry:         str       = ""
    main_services:    list[str] = Field(default_factory=list)
    target_customers: str       = ""
    service_areas:    list[str] = Field(default_factory=list)
    brand_tone:       str       = ""
    source_url:       str       = ""


class _ComparisonReport(BaseModel):
    """Structured comparison of competitors against the client business."""

    client_url:                 str       = ""
    analyzed_at:                str       = ""
    competitors_analyzed:       int       = 0

    # Gap / opportunity categories (from requirements)
    features_competitors_have:  list[str] = Field(default_factory=list)
    navigation_ideas:           list[str] = Field(default_factory=list)
    trust_building_elements:    list[str] = Field(default_factory=list)
    homepage_structure_ideas:   list[str] = Field(default_factory=list)
    service_presentation_ideas: list[str] = Field(default_factory=list)
    customer_journey_ideas:     list[str] = Field(default_factory=list)
    content_organization_ideas: list[str] = Field(default_factory=list)
    cta_ideas:                  list[str] = Field(default_factory=list)
    faq_ideas:                  list[str] = Field(default_factory=list)
    image_presentation_ideas:   list[str] = Field(default_factory=list)
    local_business_ideas:       list[str] = Field(default_factory=list)
    overall_opportunities:      list[str] = Field(default_factory=list)

    ai_provider_used:           str       = ""
    errors:                     list[str] = Field(default_factory=list)


# ── Main class ────────────────────────────────────────────────────────────────

class CompetitorAnalyzer:
    """Finds, crawls, and profiles competitor websites; compares them to the client.

    Args:
        settings:        Application settings instance.
        max_competitors: Maximum number of competitors to process per run.
                         Defaults to ``settings.competitor_max_count``.
        ai_router:       Optional pre-constructed AIRouter; created from
                         settings if omitted.
        crawler:         Optional pre-constructed WebsiteCrawler; created
                         from settings if omitted.
    """

    def __init__(
        self,
        settings:        "Settings",
        max_competitors: int | None = None,
        ai_router:       AIRouter | None = None,
        crawler:         WebsiteCrawler | None = None,
    ) -> None:
        self._settings        = settings
        self._max_competitors = max_competitors or settings.competitor_max_count
        self._ai_router       = ai_router or AIRouter(settings)
        self._crawler         = crawler  or WebsiteCrawler(settings)
        self._force_reprofile = False

        log.debug(
            "CompetitorAnalyzer initialised (max={m})", m=self._max_competitors,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def analyze(self, business: BusinessInfo) -> AnalysisResult:
        """Pipeline entry point when competitor URLs are not known in advance.

        In Phase 5, competitor URLs must be provided explicitly via
        ``analyze_from_urls()``.  Automatic URL discovery is reserved for a
        future phase.

        Args:
            business: Client business profile from BusinessAnalyzer.

        Raises:
            AnalysisError: Always — use ``analyze_from_urls()`` instead.
        """
        raise AnalysisError(
            "analyze() requires explicit competitor URLs. "
            "Call analyze_from_urls(competitor_urls, project_dir) instead. "
            "Automatic competitor discovery is not yet implemented.",
            business_name=business.name,
        )

    def analyze_from_urls(
        self,
        competitor_urls: list[str],
        project_dir:     Path,
        *,
        max_competitors: int | None = None,
        force: bool = False,
    ) -> AnalysisResult:
        """Main entry point: crawl and analyse each competitor, then compare.

        Loads ``business_profile.json`` from *project_dir* to frame all
        comparisons against the client business.

        Each competitor is crawled into
        ``projects/competitors/<slug>/`` (screenshots, pages, json). Summary
        markdown / comparison still lands under the target *project_dir*.

        Args:
            competitor_urls: Ordered list of competitor website URLs.
            project_dir:     Client's project directory root.
            max_competitors: Override for how many URLs to process; defaults
                             to ``self._max_competitors``.
            force:           Re-crawl / re-analyse even if already in ``.md``.

        Returns:
            AnalysisResult with competitor profiles, gaps, and recommendations.
        """
        project_dir = Path(project_dir)
        limit = max_competitors or self._max_competitors
        self._force_reprofile = bool(force)

        log.info(
            "=== Competitor analysis: {n} URL(s) provided ===",
            n=len(competitor_urls),
        )

        # Load client context
        client_ctx = self._load_client_context(project_dir)
        if client_ctx:
            log.info(
                "Client context loaded: {name} ({industry})",
                name=client_ctx.company_name or "(unknown)",
                industry=client_ctx.industry or "(unknown)",
            )
        else:
            log.warning(
                "business_profile.json not found in {d} — "
                "comparison will proceed without client context",
                d=project_dir / "json",
            )

        # Profile each competitor (skip URLs already documented in .md unless force)
        force = bool(getattr(self, "_force_reprofile", False))
        known = set() if force else self._urls_documented_in_md(project_dir)
        entries: list[_CompetitorEntry] = []
        urls_to_process = competitor_urls[:limit]
        skipped = 0

        for i, url in enumerate(urls_to_process, start=1):
            norm = self._normalise_url(url)
            if norm and self._url_is_documented(norm, known):
                skipped += 1
                log.info(
                    "Skipping competitor (already in .md): {u}",
                    u=norm,
                )
                entries.append(self._entry_from_existing_md(project_dir, norm))
                continue
            log.info(
                "Processing competitor {i}/{n}: {u}",
                i=i, n=len(urls_to_process), u=url,
            )
            entry = self._profile_competitor_url(url, client_ctx=client_ctx)
            entries.append(entry)

        if skipped:
            log.info("Skipped {n} competitor(s) already present in .md", n=skipped)

        newly_profiled = [
            e for e in entries
            if e.url
            and e.crawled
            and not self._url_is_documented(self._normalise_url(e.url), known)
        ]
        if newly_profiled:
            comparison = self._run_comparison(client_ctx, entries)
        else:
            comparison = self._load_or_empty_comparison(project_dir, entries)

        # Write JSON + merge markdown stories
        self._save_outputs(project_dir, entries, comparison)

        # Build shared AnalysisResult
        return self._build_analysis_result(client_ctx, entries, comparison)

    def find_competitors(
        self,
        industry: str,
        location: str,
        *,
        limit: int | None = None,
    ) -> list[str]:
        """Discover competitor URLs by industry and location.

        Automatic URL discovery is not yet implemented.  Provide competitor
        URLs manually to ``analyze_from_urls()``.

        Raises:
            NotImplementedError: Always — reserved for a future phase.
        """
        raise NotImplementedError(
            "Automatic competitor URL discovery is not yet implemented. "
            "Provide competitor URLs directly to analyze_from_urls().",
        )

    def profile_competitor(self, url: str) -> CompetitorInfo:
        """Crawl *url* and return a populated CompetitorInfo.

        Performs a full crawl via WebsiteCrawler and AI analysis.
        No client context is used; the profile is standalone.

        Args:
            url: Competitor website URL (must be http or https).

        Returns:
            CompetitorInfo with name, strengths, weaknesses, and keywords.

        Raises:
            CrawlerError: If the site cannot be fetched at all.
        """
        entry = self._profile_competitor_url(url, client_ctx=None)
        return self._entry_to_competitor_info(entry)

    def identify_content_gaps(
        self,
        business:    BusinessInfo,
        competitors: list[CompetitorInfo],
    ) -> list[str]:
        """Identify content and service areas present in competitors but not the client.

        Uses competitor strengths and service lists as signals.  Returns a
        deduplicated list of gap descriptions.

        Args:
            business:    Client's BusinessInfo (from BusinessAnalyzer).
            competitors: Profiled competitor list.

        Returns:
            Deduplicated list of gap descriptions.
        """
        client_terms: set[str] = set()
        for svc in business.services:
            for word in svc.lower().split():
                if len(word) > 3:
                    client_terms.add(word)

        gaps: list[str] = []
        seen: set[str]  = set()

        for comp in competitors:
            for strength in comp.strengths:
                key = strength.lower().strip()
                if key in seen:
                    continue
                # Gap: strength mentions something the client doesn't cover
                overlap = any(term in key for term in client_terms)
                if not overlap:
                    gaps.append(f"{comp.name or comp.url}: {strength}")
                    seen.add(key)

            # Service gaps
            for service in comp.keywords:
                key = service.lower().strip()
                if key in seen or any(term in key for term in client_terms):
                    continue
                gaps.append(f"Service opportunity: {service}")
                seen.add(key)

        return gaps

    def generate_recommendations(
        self,
        business:    BusinessInfo,
        competitors: list[CompetitorInfo],
        gaps:        list[str],
    ) -> list[str]:
        """Aggregate structural opportunities from competitor comparison.

        Merges content gaps with competitor weaknesses that represent
        opportunities for the client.  Full AI-powered recommendation
        generation is reserved for ContentOptimizer in Phase 6.

        Args:
            business:    Client's BusinessInfo.
            competitors: Profiled competitor list.
            gaps:        Content gaps from ``identify_content_gaps()``.

        Returns:
            Ordered list of opportunity strings.
        """
        opportunities: list[str] = list(gaps)
        seen: set[str] = {g.lower() for g in gaps}

        for comp in competitors:
            for weakness in comp.weaknesses:
                key = weakness.lower().strip()
                if key not in seen:
                    seen.add(key)
                    opportunities.append(
                        f"Opportunity (competitor weakness): {weakness}"
                    )

        return opportunities

    # ── Private: competitor profiling pipeline ─────────────────────────────────

    def _profile_competitor_url(
        self,
        url:        str,
        client_ctx: _ClientContext | None = None,
    ) -> _CompetitorEntry:
        """Fetch structure signals from *url* and analyse with DeepSeek.

        Does **not** download images or full page content corpora — only
        lightweight HTML structure (nav, headings, CTAs, page signals).

        All errors are captured in the returned entry rather than raised.
        """
        norm_url = self._normalise_url(url)
        if not norm_url:
            entry = _CompetitorEntry(url=url)
            entry.crawl_errors.append(f"Invalid URL: {url!r}")
            log.warning("Invalid competitor URL: {u}", u=url)
            return entry

        entry = _CompetitorEntry(
            url=norm_url,
            analyzed_at=datetime.now(timezone.utc).isoformat(),
        )

        # ── Step 0: full crawl into projects/competitors/<slug>/ ──────────────
        slug = self._url_to_slug(norm_url)
        comp_root = Path(self._settings.projects_dir) / "competitors" / slug
        try:
            if bool(getattr(self, "_force_reprofile", False)) and comp_root.exists():
                import shutil
                shutil.rmtree(comp_root, ignore_errors=True)
            from webmaker.modules.website_crawler import WebsiteCrawler

            crawler = WebsiteCrawler(self._settings)
            crawl_result = crawler.crawl(norm_url, output_dir=comp_root)
            entry.project_dir = str(comp_root)
            entry.crawled = True
            log.info(
                "Competitor crawl → {d} ({n} pages)",
                d=comp_root,
                n=getattr(crawl_result, "total_pages", 0),
            )
        except Exception as exc:
            entry.crawl_errors.append(f"Competitor crawl failed: {exc}")
            log.warning("Competitor crawl failed for {u}: {e}", u=norm_url, e=exc)

        # ── Step 1: lightweight structure snapshot (no images / deep crawl) ───
        snapshot: dict[str, Any] | None = None
        try:
            log.info("Fetching structure for competitor: {u}", u=norm_url)
            snapshot = self._fetch_structure_snapshot(norm_url)
            entry.crawled = True
            log.info(
                "Structure snapshot OK: {u} (nav={n}, headings={h})",
                u=norm_url,
                n=len(snapshot.get("nav_labels") or []),
                h=len(snapshot.get("headings") or []),
            )
        except Exception as exc:
            entry.crawl_errors.append(f"Structure fetch failed: {exc}")
            log.warning("Structure fetch failed for {u}: {e}", u=norm_url, e=exc)
            if not entry.project_dir:
                return entry

        # ── Step 2: DeepSeek structure analysis ───────────────────────────────
        if not self._ai_router.is_available(AIProvider.DEEPSEEK):
            entry.analysis_errors.append(
                "DeepSeek not configured — competitor structure analysis skipped"
            )
            log.warning("Skipping DeepSeek profiling for {u}", u=norm_url)
            return entry

        prompt = self._build_structure_prompt(snapshot, client_ctx=client_ctx)
        try:
            raw_response, provider = self._call_ai(prompt)
            log.info(
                "DeepSeek structure profile for {u} via {p}",
                u=norm_url, p=provider,
            )
            ai_data = self._parse_ai_json(raw_response)
            entry.profile = self._dict_to_competitor_profile(ai_data)
            md = ai_data.get("markdown_summary") or ai_data.get("structure_markdown")
            if isinstance(md, str) and md.strip():
                body = md.strip()
                if "competitor-url:" not in body:
                    body = f"<!-- competitor-url: {norm_url} -->\n{body}"
                entry.markdown_summary = body
            else:
                entry.markdown_summary = self._profile_to_markdown(entry)

        except AIError as exc:
            entry.analysis_errors.append(f"AI error: {exc}")
            log.error("DeepSeek profiling failed for {u}: {e}", u=norm_url, e=exc)
        except Exception as exc:
            entry.analysis_errors.append(f"Unexpected: {exc}")
            log.error("Unexpected AI error for {u}: {e}", u=norm_url, e=exc)

        return entry

    def _fetch_structure_snapshot(self, url: str) -> dict[str, Any]:
        """HTTP-fetch homepage HTML and extract structural signals only."""
        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; WebMakerStructureBot/1.0; +local)"
            ),
        }
        resp = requests.get(url, headers=headers, timeout=25, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Drop scripts/styles — we only keep structure labels
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        title = (soup.title.string or "").strip() if soup.title else ""
        nav_labels: list[str] = []
        for a in soup.select("nav a, header a, .menu a, .nav a"):
            t = a.get_text(" ", strip=True)
            if t and t not in nav_labels and len(t) < 80:
                nav_labels.append(t)
            if len(nav_labels) >= 40:
                break

        headings: list[str] = []
        for level in ("h1", "h2", "h3"):
            for h in soup.find_all(level):
                t = h.get_text(" ", strip=True)
                if t:
                    headings.append(f"{level.upper()}: {t[:120]}")
                if len(headings) >= 40:
                    break
            if len(headings) >= 40:
                break

        ctas: list[str] = []
        for el in soup.select("a, button"):
            t = el.get_text(" ", strip=True)
            low = t.lower()
            if t and any(k in low for k in (
                "kontakt", "anfrag", "call", "termin", "angebot", "jetzt",
                "contact", "book", "quote", "request",
            )):
                if t not in ctas:
                    ctas.append(t[:80])
            if len(ctas) >= 15:
                break

        footer_bits = []
        footer = soup.find("footer")
        if footer:
            footer_bits = [
                x.get_text(" ", strip=True)[:80]
                for x in footer.find_all(["a", "p", "li"])[:20]
                if x.get_text(" ", strip=True)
            ]

        return {
            "url": url,
            "final_url": str(resp.url),
            "title": title,
            "nav_labels": nav_labels,
            "headings": headings,
            "cta_labels": ctas,
            "footer_labels": footer_bits,
            "has_faq_signal": any(
                "faq" in (x or "").lower() or "häufig" in (x or "").lower()
                for x in nav_labels + headings
            ),
            "has_contact_signal": any(
                "kontakt" in (x or "").lower() or "contact" in (x or "").lower()
                for x in nav_labels + headings + ctas
            ),
        }

    def _build_structure_prompt(
        self,
        snapshot: dict[str, Any],
        *,
        client_ctx: _ClientContext | None = None,
    ) -> str:
        lines = [
            "Write a STRUCTURE STORY about this competitor website.",
            "Style: numbered narrative lines, each like:",
            '"1. example.com has a clear three-item top navigation which looks '
            'state of the art, fulfils customer needs, and presents an attractive '
            'view for an Entrümpelung / local service business."',
            "",
            "Focus ONLY on structure, UX patterns, navigation, trust, CTAs, "
            "service organisation — what works well and why.",
            "Do NOT copy marketing body text. Do NOT invent unverified company facts.",
            "",
        ]
        if client_ctx and client_ctx.company_name:
            lines += [
                "Client context (for contrast only):",
                f"- Company: {client_ctx.company_name}",
                f"- Industry: {client_ctx.industry}",
                f"- Services: {', '.join(client_ctx.main_services[:8])}",
                "",
            ]
        lines += [
            "Structure snapshot (JSON):",
            json.dumps(snapshot, ensure_ascii=False, indent=2)[:8000],
            "",
            "Return JSON with keys:",
            "company_name, main_services, strengths, weaknesses,",
            "navigation_structure, homepage_layout, cta_strategy,",
            "trust_signals, contact_methods, has_faq, content_quality,",
            "markdown_summary",
            "",
            "markdown_summary MUST be a numbered story (1. 2. 3. …) in German or English,",
            "each line describing one structural strength/idea useful for improving",
            "a local-service website. Start with the domain name in each line.",
        ]
        return "\n".join(lines)

    def documented_urls(self, project_dir: Path) -> set[str]:
        """Public: normalised competitor URLs already present in markdown files."""
        return self._urls_documented_in_md(project_dir)

    def undocumented_urls(self, project_dir: Path, urls: list[str]) -> list[str]:
        """Return competitor URLs that are not yet present in markdown (need AI)."""
        known = self.documented_urls(project_dir)
        missing: list[str] = []
        for raw in urls:
            norm = self._normalise_url(raw)
            if norm and not self._url_is_documented(norm, known):
                missing.append(norm)
        return missing

    def _urls_documented_in_md(self, project_dir: Path) -> set[str]:
        """Return normalised competitor URLs already present in markdown files."""
        found: set[str] = set()
        json_dir = Path(project_dir) / "json"
        paths = [json_dir / "competitor_structure.md"]
        comp_dir = json_dir / "competitors"
        if comp_dir.is_dir():
            paths.extend(sorted(comp_dir.glob("*.md")))
        for path in paths:
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            # Only explicit markers count — never re-crawl URLs already in .md
            for m in re.finditer(
                r"<!--\s*competitor-url:\s*(\S+)\s*-->", text, re.I
            ):
                u = self._normalise_url(m.group(1))
                if u:
                    found.add(u)
        return found

    def _url_is_documented(self, url: str, known: set[str]) -> bool:
        if not url:
            return False
        if url in known:
            return True
        # Match by host
        try:
            host = urlparse(url).netloc.lower().removeprefix("www.")
        except Exception:
            return False
        for k in known:
            try:
                kh = urlparse(k).netloc.lower().removeprefix("www.")
            except Exception:
                continue
            if host and host == kh:
                return True
        return False

    def _entry_from_existing_md(self, project_dir: Path, url: str) -> _CompetitorEntry:
        """Build a lightweight entry from existing markdown (no re-fetch)."""
        slug = self._url_to_slug(url)
        md_path = Path(project_dir) / "json" / "competitors" / f"{slug}.md"
        text = ""
        if md_path.is_file():
            try:
                text = md_path.read_text(encoding="utf-8")
            except OSError:
                text = ""
        if not text:
            # Pull section from aggregate file
            agg = Path(project_dir) / "json" / "competitor_structure.md"
            if agg.is_file():
                try:
                    full = agg.read_text(encoding="utf-8")
                    text = self._extract_md_section_for_url(full, url)
                except OSError:
                    text = ""
        entry = _CompetitorEntry(
            url=url,
            crawled=True,
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            markdown_summary=text.strip() or f"<!-- competitor-url: {url} -->\n(Previously analysed — skipped.)",
        )
        entry.profile.company_name = urlparse(url).netloc
        return entry

    def _extract_md_section_for_url(self, full: str, url: str) -> str:
        marker = f"<!-- competitor-url: {url}"
        # Also try without trailing slash variants
        idx = full.find(f"<!-- competitor-url: {url}")
        if idx < 0:
            alt = url.rstrip("/")
            idx = full.find(f"<!-- competitor-url: {alt}")
        if idx < 0:
            host = urlparse(url).netloc
            idx = full.find(host)
            if idx < 0:
                return ""
            # back up to previous heading
            start = full.rfind("\n#", 0, idx)
            start = 0 if start < 0 else start + 1
        else:
            start = idx
        end = full.find("<!-- competitor-url:", start + 10)
        if end < 0:
            end = full.find("\n---", start + 10)
        if end < 0:
            end = len(full)
        return full[start:end].strip()

    def _load_or_empty_comparison(
        self,
        project_dir: Path,
        entries: list[_CompetitorEntry],
    ) -> _ComparisonReport:
        path = Path(project_dir) / "json" / "comparison_report.json"
        data = self._load_json(path, default=None)
        if isinstance(data, dict) and data:
            try:
                return _ComparisonReport(**{
                    k: v for k, v in data.items()
                    if k in _ComparisonReport.model_fields
                })
            except Exception:
                pass
        return _ComparisonReport(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            competitors_analyzed=sum(1 for e in entries if e.crawled),
        )

    def _profile_to_markdown(self, entry: _CompetitorEntry) -> str:
        p = entry.profile
        lines = [
            f"<!-- competitor-url: {entry.url} -->",
            f"# Competitor structure — {p.company_name or entry.url}",
            "",
            f"- URL: {entry.url}",
            f"- Analysed: {entry.analyzed_at}",
            "",
        ]
        # Prefer story-style strengths as numbered lines
        domain = urlparse(entry.url).netloc or entry.url
        stories = p.strengths or []
        if stories:
            lines.append("## Structure story")
            for i, s in enumerate(stories, start=1):
                if s.strip().startswith(tuple(str(n) for n in range(10))):
                    lines.append(s if s[0].isdigit() else f"{i}. {domain} — {s}")
                else:
                    lines.append(
                        f"{i}. {domain} has {s} which supports a clear, "
                        f"customer-friendly local-service presentation."
                    )
            lines.append("")
        lines += [
            f"- Navigation: {p.navigation_structure or '—'}",
            f"- Homepage layout: {p.homepage_layout or '—'}",
            f"- CTA strategy: {p.cta_strategy or '—'}",
            "",
            "## Structural weaknesses",
            *[f"- {s}" for s in (p.weaknesses or ["(none detected)"])],
        ]
        return "\n".join(lines)

    def _run_comparison(
        self,
        client_ctx: _ClientContext | None,
        entries:    list[_CompetitorEntry],
    ) -> _ComparisonReport:
        """Compare all competitor entries against the client and produce a report.

        Args:
            client_ctx: Client business context (may be None).
            entries:    All processed competitor entries.

        Returns:
            Populated _ComparisonReport.
        """
        report = _ComparisonReport(
            client_url=client_ctx.source_url if client_ctx else "",
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            competitors_analyzed=sum(1 for e in entries if e.crawled),
        )

        crawled_entries = [e for e in entries if e.crawled]
        if not crawled_entries:
            report.errors.append("No competitors were successfully crawled")
            return report

        if not self._ai_router.available_providers():
            report.errors.append(
                "No AI providers configured — comparison skipped"
            )
            return report

        prompt = self._build_comparison_prompt(client_ctx, crawled_entries)
        try:
            raw, provider = self._call_ai(prompt)
            log.info("Comparison AI response received via {p}", p=provider)
            ai_data = self._parse_ai_json(raw)
            report = self._merge_comparison_report(report, ai_data)
            report.ai_provider_used = provider
        except AIError as exc:
            report.errors.append(f"AI comparison failed: {exc}")
            log.error("Comparison AI call failed: {e}", e=exc)
        except Exception as exc:
            report.errors.append(f"Unexpected comparison error: {exc}")
            log.error("Unexpected comparison error: {e}", e=exc)

        return report

    # ── Private: prompt builders ───────────────────────────────────────────────

    def _build_competitor_prompt(
        self,
        crawl_result: CrawlResult,
        *,
        client_ctx: _ClientContext | None = None,
    ) -> str:
        """Build the AI prompt for analysing a single competitor.

        Args:
            crawl_result: Crawler output for the competitor site.
            client_ctx:   Optional client context to frame the analysis.

        Returns:
            Assembled prompt string.
        """
        lines: list[str] = [
            "Analyse this competitor website and extract structured intelligence.",
            "",
        ]

        # Client context framing
        if client_ctx and client_ctx.company_name:
            lines += [
                "=== CLIENT CONTEXT (for framing, do NOT include in output) ===",
                f"Client: {client_ctx.company_name} | Industry: {client_ctx.industry}",
                f"Client services: {', '.join(client_ctx.main_services) or 'not specified'}",
                f"Client areas: {', '.join(client_ctx.service_areas) or 'not specified'}",
                "",
            ]

        lines += [
            f"=== COMPETITOR WEBSITE: {crawl_result.target_url} ===",
            "",
        ]

        # Page content (bounded)
        sorted_pages = sorted(
            crawl_result.pages,
            key=lambda p: _PAGE_PRIORITY.get(
                p.page_type.value if hasattr(p.page_type, "value") else str(p.page_type), 9,
            ),
        )

        chars_used = 0
        for page in sorted_pages[:_MAX_PAGES_IN_PROMPT]:
            if chars_used >= _MAX_CONTENT_CHARS:
                break

            pt = page.page_type.value if hasattr(page.page_type, "value") else str(page.page_type)
            lines.append(f"[{pt.upper()}] {page.url}")
            if page.title:
                lines.append(f"  Title: {page.title}")
            if page.description:
                lines.append(f"  Description: {page.description}")
            if page.headings:
                lines.append(f"  Headings: {' | '.join(page.headings[:6])}")

            text = page.text_content or ""
            limit = (
                2_000 if pt == "home" else
                1_500 if pt in ("about", "services") else
                500
            )
            excerpt = text[:limit].rstrip()
            if excerpt:
                lines.append(f"  Text: {excerpt}")
                chars_used += len(excerpt)
            lines.append("")

        # Response schema
        lines += [
            "=== REQUIRED JSON RESPONSE ===",
            (
                "Respond ONLY with a JSON object. Use empty string or [] "
                "for unknown fields. Be specific and factual."
            ),
        ]

        schema = {
            "company_name":         "competitor's official name",
            "business_category":    "e.g. 'local service', 'e-commerce'",
            "main_services":        ["service 1", "service 2"],
            "service_structure":    "single-page | multi-page | landing-page",
            "navigation_structure": "simple | standard | mega-menu | complex",
            "homepage_layout":      "description of homepage sections",
            "cta_strategy":         "main call-to-action approach",
            "trust_signals":        ["signal 1", "signal 2"],
            "contact_methods":      ["phone", "form", "chat", "email"],
            "service_areas":        ["city or region"],
            "has_faq":              False,
            "faq_topics":           ["topic if has_faq is true"],
            "image_usage":          "minimal | moderate | heavy",
            "content_quality":      "poor | basic | adequate | good | excellent",
            "content_depth":        "shallow | moderate | deep",
            "brand_tone":           "professional | casual | technical | friendly",
            "customer_focus":       "how they address customer needs",
            "strengths":            ["what this competitor does well vs typical competitors"],
            "weaknesses":           ["areas where this competitor falls short"],
        }
        lines.append(json.dumps(schema, indent=2, ensure_ascii=False))

        return "\n".join(lines)

    def _build_comparison_prompt(
        self,
        client_ctx: _ClientContext | None,
        entries:    list[_CompetitorEntry],
    ) -> str:
        """Build the AI prompt for comparing competitors against the client.

        Args:
            client_ctx: Client business context (may be None).
            entries:    Crawled and profiled competitor entries.

        Returns:
            Assembled prompt string.
        """
        lines: list[str] = [
            "You are a business strategist identifying improvement opportunities.",
            "Compare these competitor websites against the client business profile.",
            "IMPORTANT: Do NOT suggest copying competitor content, text, or design.",
            "ONLY identify structural, organisational, and presentational IDEAS.",
            "",
        ]

        # Client profile
        if client_ctx and client_ctx.company_name:
            lines += [
                "=== CLIENT BUSINESS ===",
                f"Name     : {client_ctx.company_name}",
                f"Industry : {client_ctx.industry}",
                f"Services : {', '.join(client_ctx.main_services) or 'not specified'}",
                f"Areas    : {', '.join(client_ctx.service_areas) or 'not specified'}",
                f"Tone     : {client_ctx.brand_tone or 'not specified'}",
                "",
            ]
        else:
            lines += [
                "=== CLIENT BUSINESS ===",
                "(No client profile available — provide generic improvement ideas)",
                "",
            ]

        # Competitor summaries
        lines.append("=== COMPETITORS ANALYSED ===")
        for i, entry in enumerate(entries, start=1):
            p = entry.profile
            lines += [
                f"\n[Competitor {i}] {p.company_name or entry.url}",
                f"  URL             : {entry.url}",
                f"  Services        : {', '.join(p.main_services) or '—'}",
                f"  Navigation      : {p.navigation_structure or '—'}",
                f"  CTA strategy    : {p.cta_strategy or '—'}",
                f"  Trust signals   : {', '.join(p.trust_signals[:4]) or '—'}",
                f"  Has FAQ         : {p.has_faq}",
                f"  Content quality : {p.content_quality or '—'}",
                f"  Image usage     : {p.image_usage or '—'}",
                f"  Strengths       : {', '.join(p.strengths[:4]) or '—'}",
                f"  Weaknesses      : {', '.join(p.weaknesses[:3]) or '—'}",
            ]
        lines.append("")

        # Response schema
        lines += [
            "=== REQUIRED JSON RESPONSE ===",
            (
                "Identify ONLY ideas and approaches — never suggest copying "
                "content, design, or branding directly from competitors."
            ),
        ]

        schema = {
            "features_competitors_have": [
                "feature/capability competitors have that client lacks"
            ],
            "navigation_ideas": [
                "navigation structure ideas inspired by competitors"
            ],
            "trust_building_elements": [
                "trust-building approaches observed in competitors"
            ],
            "homepage_structure_ideas": [
                "homepage section/layout ideas from competitors"
            ],
            "service_presentation_ideas": [
                "how competitors present their services better"
            ],
            "customer_journey_ideas": [
                "how competitors guide visitors more effectively"
            ],
            "content_organization_ideas": [
                "how competitors organise content more clearly"
            ],
            "cta_ideas": [
                "call-to-action approaches from competitors"
            ],
            "faq_ideas": [
                "FAQ structure or topics worth addressing"
            ],
            "image_presentation_ideas": [
                "how competitors use imagery more effectively"
            ],
            "local_business_ideas": [
                "local trust signals or local presence strategies"
            ],
            "overall_opportunities": [
                "top-level opportunities for the client website"
            ],
        }
        lines.append(json.dumps(schema, indent=2, ensure_ascii=False))

        return "\n".join(lines)

    # ── Private: AI call ───────────────────────────────────────────────────────

    def _call_ai(self, prompt: str) -> tuple[str, str]:
        """Send *prompt* to the best available provider via AIRouter.

        Args:
            prompt: Assembled prompt string.

        Returns:
            Tuple of (response_text, provider_name).

        Raises:
            AIError: If no provider is available or the call fails.
        """
        available = self._ai_router.available_providers()
        if not available:
            raise AIError("No AI providers have API keys configured")
        if not self._ai_router.is_available(AIProvider.DEEPSEEK):
            raise AIError(
                "DeepSeek API key required for competitor structure analysis. "
                "Set DEEPSEEK_API_KEY in .env",
            )

        log.info("DeepSeek structure request ({n:,} chars)", n=len(prompt))

        try:
            response = self._ai_router.complete(
                prompt,
                system=load_prompt_or_default(
                    "competitor_structure",
                    _FALLBACK_SYSTEM_PROMPT,
                ),
                provider=AIProvider.DEEPSEEK,
            )
        except NotImplementedError:
            raise AIError(
                "AIRouter.complete() is not yet implemented. "
                "Implement ai_router.py (Phase N) to enable AI analysis.",
            )
        except Exception as exc:
            raise AIError(f"AI provider call failed: {exc}") from exc

        if not response or not response.strip():
            raise AIError("AI returned an empty response")

        return response, AIProvider.DEEPSEEK.value

    def _parse_ai_json(self, response: str) -> dict:
        """Extract and parse the JSON object from an AI response.

        Handles raw JSON, code-fenced JSON, and JSON embedded in prose.

        Args:
            response: Raw AI response string.

        Returns:
            Parsed dict, or empty dict on failure.
        """
        text = response.strip()
        if not text:
            return {}

        # Strip ```json … ``` or ``` … ``` fences
        fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.DOTALL)
        if fence:
            text = fence.group(1)
        else:
            start = text.find("{")
            end   = text.rfind("}")
            if start != -1 and end > start:
                text = text[start : end + 1]

        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            log.warning("JSON parse error in AI response: {e}", e=exc)
            log.debug("AI response excerpt: {r}", r=response[:300])
            return {}

        if not isinstance(result, dict):
            log.warning("AI returned non-dict: {t}", t=type(result).__name__)
            return {}

        return result

    # ── Private: data transformations ─────────────────────────────────────────

    def _dict_to_competitor_profile(self, data: dict) -> _CompetitorProfile:
        """Build a _CompetitorProfile from a raw AI-parsed dict.

        Normalises all list fields and ignores unknown keys.

        Args:
            data: AI-parsed JSON dict.

        Returns:
            Validated _CompetitorProfile.
        """
        normalised: dict[str, Any] = {}

        for key, val in data.items():
            if key not in _CompetitorProfile.model_fields:
                continue
            if key in _LIST_FIELDS:
                if val is None:
                    normalised[key] = []
                elif isinstance(val, str):
                    normalised[key] = [val] if val.strip() else []
                elif isinstance(val, list):
                    normalised[key] = [str(v).strip() for v in val if str(v).strip()]
                else:
                    normalised[key] = []
            elif key == "has_faq":
                normalised[key] = bool(val)
            else:
                normalised[key] = str(val) if val is not None else ""

        return _CompetitorProfile(**normalised)

    def _merge_comparison_report(
        self,
        base:    _ComparisonReport,
        ai_data: dict,
    ) -> _ComparisonReport:
        """Merge AI comparison JSON into *base* _ComparisonReport.

        Args:
            base:    Initial report (client_url, analyzed_at, metadata).
            ai_data: Parsed AI JSON.

        Returns:
            Updated _ComparisonReport.
        """
        update: dict[str, Any] = {
            "client_url":             base.client_url,
            "analyzed_at":            base.analyzed_at,
            "competitors_analyzed":   base.competitors_analyzed,
            "errors":                 base.errors,
        }

        for field in _LIST_FIELDS:
            raw = ai_data.get(field, [])
            if isinstance(raw, list):
                update[field] = [str(v).strip() for v in raw if str(v).strip()]
            elif isinstance(raw, str) and raw.strip():
                update[field] = [raw.strip()]
            else:
                update[field] = []

        return _ComparisonReport(**update)

    def _entry_to_competitor_info(self, entry: _CompetitorEntry) -> CompetitorInfo:
        """Convert an internal _CompetitorEntry to the shared CompetitorInfo type.

        Args:
            entry: Processed competitor entry.

        Returns:
            CompetitorInfo for use by downstream modules.
        """
        p = entry.profile
        return CompetitorInfo(
            url        = entry.url,
            name       = p.company_name,
            strengths  = p.strengths,
            weaknesses = p.weaknesses,
            keywords   = p.main_services,
        )

    def _build_analysis_result(
        self,
        client_ctx:  _ClientContext | None,
        entries:     list[_CompetitorEntry],
        comparison:  _ComparisonReport,
    ) -> AnalysisResult:
        """Build the shared AnalysisResult from internal data.

        Args:
            client_ctx: Client context (may be None).
            entries:    All competitor entries.
            comparison: Finished comparison report.

        Returns:
            AnalysisResult for downstream modules.
        """
        from webmaker.core.types import BusinessInfo as BI

        client_info = BI(
            name    = client_ctx.company_name if client_ctx else "",
            industry = client_ctx.industry    if client_ctx else "",
            services = client_ctx.main_services if client_ctx else [],
        )

        competitors = [self._entry_to_competitor_info(e) for e in entries]

        gaps = self.identify_content_gaps(client_info, competitors)

        return AnalysisResult(
            business        = client_info,
            competitors     = competitors,
            content_gaps    = gaps,
            recommendations = comparison.overall_opportunities,
        )

    # ── Private: file I/O ──────────────────────────────────────────────────────

    def _load_client_context(self, project_dir: Path) -> _ClientContext | None:
        """Load and parse business_profile.json from *project_dir*.

        Returns None (with a debug log) if the file is absent or unreadable.

        Args:
            project_dir: Client's project directory.

        Returns:
            _ClientContext, or None if unavailable.
        """
        path = Path(project_dir) / "json" / "business_profile.json"
        data = self._load_json(path, default=None)
        if not data or not isinstance(data, dict):
            log.debug("No client context available from {p}", p=path)
            return None

        return _ClientContext(
            company_name     = data.get("company_name", ""),
            industry         = data.get("industry", ""),
            main_services    = data.get("main_services", []),
            target_customers = data.get("target_customers", ""),
            service_areas    = data.get("service_areas", []),
            brand_tone       = data.get("brand_tone", ""),
            source_url       = data.get("source_url", ""),
        )

    def _save_outputs(
        self,
        project_dir: Path,
        entries:     list[_CompetitorEntry],
        comparison:  _ComparisonReport,
    ) -> None:
        """Write all JSON output files to the client's project directory.

        Files written:
        - ``json/competitors.json``          — one-row-per-competitor summary
        - ``json/competitor_analysis.json``  — full profiles
        - ``json/comparison_report.json``    — client vs competitors report
        - ``json/competitors/<domain>.json`` — per-competitor detailed JSON

        Args:
            project_dir: Client's project directory.
            entries:     All processed competitor entries.
            comparison:  Finished comparison report.
        """
        json_dir = Path(project_dir) / "json"
        comp_dir = json_dir / "competitors"
        comp_dir.mkdir(parents=True, exist_ok=True)

        # ── competitors.json (summary table) ──────────────────────────────────
        summary = [
            {
                "url":             e.url,
                "company_name":    e.profile.company_name,
                "crawled":         e.crawled,
                "services_count":  len(e.profile.main_services),
                "strengths_count": len(e.profile.strengths),
                "content_quality": e.profile.content_quality,
                "has_faq":         e.profile.has_faq,
                "analyzed_at":     e.analyzed_at,
                "errors":          e.crawl_errors + e.analysis_errors,
            }
            for e in entries
        ]
        self._write_json(json_dir / "competitors.json", summary)

        # ── competitor_analysis.json (full profiles) ──────────────────────────
        full = [
            {
                "url":             e.url,
                "crawled":         e.crawled,
                "project_dir":     e.project_dir,
                "profile":         e.profile.model_dump(),
                "crawl_errors":    e.crawl_errors,
                "analysis_errors": e.analysis_errors,
                "analyzed_at":     e.analyzed_at,
            }
            for e in entries
        ]
        self._write_json(json_dir / "competitor_analysis.json", full)

        # ── comparison_report.json ─────────────────────────────────────────────
        self._write_json(
            json_dir / "comparison_report.json",
            comparison.model_dump(),
        )

        # ── per-competitor JSON files ──────────────────────────────────────────
        for entry in entries:
            slug = self._url_to_slug(entry.url)
            if slug:
                self._write_json(comp_dir / f"{slug}.json", {
                    "url":     entry.url,
                    "profile": entry.profile.model_dump(),
                    "errors":  entry.crawl_errors + entry.analysis_errors,
                })
                md_body = entry.markdown_summary or self._profile_to_markdown(entry)
                (comp_dir / f"{slug}.md").write_text(md_body + "\n", encoding="utf-8")

        # ── Aggregate markdown report (merge: keep old URLs, update current) ───
        md_path = json_dir / "competitor_structure.md"
        existing_text = ""
        if md_path.is_file():
            try:
                existing_text = md_path.read_text(encoding="utf-8")
            except OSError:
                existing_text = ""

        # Drop sections for URLs we are rewriting this run
        kept = existing_text
        for entry in entries:
            if not entry.markdown_summary:
                continue
            kept = self._remove_md_section_for_url(kept, entry.url)

        md_lines = [
            "# Competitor structure stories",
            "",
            f"Updated: {comparison.analyzed_at}",
            "",
        ]
        # Preserve leftover historical sections first
        leftover = kept.strip()
        if leftover and "competitor-url:" in leftover:
            # strip old title blocks
            leftover = re.sub(
                r"^# Competitor structure.*?\n+", "", leftover, flags=re.M
            )
            leftover = re.sub(r"^Updated:.*\n+", "", leftover, flags=re.M)
            md_lines.append(leftover.strip())
            md_lines += ["", "---", ""]

        for entry in entries:
            md_body = entry.markdown_summary or self._profile_to_markdown(entry)
            md_lines.append(md_body.strip())
            md_lines.append("")
            md_lines.append("---")
            md_lines.append("")

        dump = comparison.model_dump()
        for key, title in (
            ("navigation_ideas", "Navigation ideas"),
            ("homepage_structure_ideas", "Homepage structure ideas"),
            ("trust_building_elements", "Trust-building elements"),
            ("cta_ideas", "CTA ideas"),
            ("faq_ideas", "FAQ ideas"),
            ("overall_opportunities", "Overall opportunities"),
        ):
            vals = dump.get(key) or []
            if vals:
                md_lines += [f"## {title}", *[f"- {v}" for v in vals], ""]

        md_path.write_text("\n".join(md_lines).strip() + "\n", encoding="utf-8")

        log.info(
            "Saved: competitor_structure.md + competitors/*.md + JSON summaries → {d}",
            d=json_dir,
        )

    def _remove_md_section_for_url(self, full: str, url: str) -> str:
        if not full or not url:
            return full or ""
        section = self._extract_md_section_for_url(full, url)
        if section:
            return full.replace(section, "").replace("\n---\n\n---\n", "\n---\n")
        return full

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        """Serialise *data* to *path* as versioned UTF-8 JSON."""
        write_versioned_json(path, data)

    @staticmethod
    def _load_json(path: Path, *, default: Any) -> Any:
        """Load JSON from *path*, unwrapping versioned list envelopes."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.debug("Not found: {p}", p=path.name)
            return default
        except json.JSONDecodeError as exc:
            log.warning("Invalid JSON in {p}: {e}", p=path.name, e=exc)
            return default
        except OSError as exc:
            log.warning("Cannot read {p}: {e}", p=path.name, e=exc)
            return default
        data = unwrap_json(raw)
        if default is not None and type(default) is list and not isinstance(data, list):
            return default
        if default is not None and type(default) is dict and not isinstance(data, dict):
            return default
        return data

    # ── Private: URL utilities ─────────────────────────────────────────────────

    @staticmethod
    def _normalise_url(url: str) -> str:
        """Return a normalised http/https URL, or empty string if invalid."""
        if not url or not isinstance(url, str):
            return ""
        url = url.strip()
        try:
            from urllib.parse import urlparse, urlunparse, urldefrag
            url, _ = urldefrag(url)
            p = urlparse(url)
            if p.scheme not in ("http", "https"):
                return ""
            path = p.path if p.path else "/"
            if path != "/" and path.endswith("/"):
                path = path.rstrip("/")
            return urlunparse((p.scheme.lower(), p.netloc.lower(),
                               path, p.params, p.query, ""))
        except Exception:
            return ""

    @staticmethod
    def _url_to_slug(url: str) -> str:
        """Convert a URL to a filesystem-safe slug for filenames."""
        try:
            netloc = urlparse(url).netloc
            slug = re.sub(r"[.:]", "-", netloc.removeprefix("www."))
            slug = re.sub(r"-+", "-", slug).strip("-")
            return slug[:60] or "competitor"
        except Exception:
            return "competitor"

    @staticmethod
    def _infer_project_dir_for(url: str, projects_dir: Path) -> Path | None:
        """Guess the WebsiteCrawler project directory for *url*."""
        try:
            netloc = urlparse(url).netloc
            folder = re.sub(r"[.:]", "-", netloc.removeprefix("www."))
            folder = re.sub(r"-+", "-", folder).strip("-")
            return projects_dir / folder if folder else None
        except Exception:
            return None

    def _infer_project_dir(self, url: str) -> Path | None:
        """Instance wrapper for _infer_project_dir_for."""
        return self._infer_project_dir_for(url, self._settings.projects_dir)
