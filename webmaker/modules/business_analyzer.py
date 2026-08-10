"""
webmaker.modules.business_analyzer
===================================
Converts the structured output of WebsiteCrawler into a rich, structured
business profile using a combination of deterministic rule-based extraction
and AI-powered reasoning via AIRouter.

Pipeline
--------
1. Load crawler JSON output (pages.json, navigation.json, images.json,
   crawl_summary.json, per-page rich JSON)
2. Deterministic pass  — regex: emails, phones; heuristics: industry, tone,
   social links, languages, service keywords
3. AI pass             — send structured context to Claude (via AIRouter);
   request a JSON business profile
4. Merge               — combine deterministic evidence with AI reasoning;
   deterministic data always wins for factual fields (email, phone)
5. Persist             — write business_profile.json to the project directory
6. Return              — convert to shared BusinessInfo for downstream modules

No crawling. No SEO optimisation. No content rewriting. No WordPress.

Primary class: BusinessAnalyzer
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from webmaker.core.exceptions import AIError, AnalysisError
from webmaker.core.logging import get_logger
from webmaker.core.prompts import load_prompt_or_default
from webmaker.core.schema import unwrap_json, write_versioned_json
from webmaker.core.types import AIProvider, BusinessInfo, CrawlResult
from webmaker.modules.ai_router import AIRouter

if TYPE_CHECKING:
    from webmaker.config.settings import Settings

log = get_logger("business_analyzer")


# ── Constants ─────────────────────────────────────────────────────────────────

_FALLBACK_SYSTEM_PROMPT = (
    "You are an expert business analyst specialising in website intelligence. "
    "Analyse the provided website content and return structured business data. "
    "Respond ONLY with a single valid JSON object — no markdown, no code fences, "
    "no explanatory text before or after."
)

#: Mapping from domain suffix → social platform name
_SOCIAL_DOMAINS: dict[str, str] = {
    "facebook.com":  "facebook",
    "instagram.com": "instagram",
    "twitter.com":   "twitter",
    "x.com":         "twitter",
    "linkedin.com":  "linkedin",
    "youtube.com":   "youtube",
    "tiktok.com":    "tiktok",
    "pinterest.com": "pinterest",
    "xing.com":      "xing",
    "kununu.com":    "kununu",
}

_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w.\-]+\.[a-zA-Z]{2,10}", re.ASCII)
_PHONE_RE = re.compile(
    r"(?:"
    r"\+?\d{1,3}[\s\-.]?\(?\d{1,4}\)?[\s\-.]?\d{1,4}[\s\-.]?\d{1,9}"   # international
    r"|"
    r"\(?\d{2,5}\)?[\s\-.]?\d{2,5}[\s\-.]?\d{2,8}"                        # local
    r")"
)

# AI response fields that must be lists (normalised before merging)
_LIST_FIELDS = frozenset({
    "main_services", "secondary_services", "products",
    "service_areas", "unique_selling_points", "trust_signals",
    "website_goals", "existing_faq_topics",
    "business_strengths", "business_weaknesses",
})

# Page type importance for context prioritisation
_PAGE_PRIORITY: dict[str, int] = {
    "home": 0, "about": 1, "services": 2, "contact": 3,
    "blog": 5, "product": 4, "gallery": 6, "unknown": 9,
}

_MAX_CONTENT_CHARS = 12_000   # total text chars forwarded to AI
_MAX_IMAGE_RECORDS = 25       # image records included in prompt


# ── Module-local data models ──────────────────────────────────────────────────

class _PageRecord(BaseModel):
    """Compact page representation used internally during analysis."""
    url:              str
    title:            str       = ""
    meta_description: str       = ""
    page_type:        str       = "unknown"
    h1:               list[str] = Field(default_factory=list)
    h2:               list[str] = Field(default_factory=list)
    text_excerpt:     str       = ""
    word_count:       int       = 0
    language:         str       = ""
    external_links:   list[str] = Field(default_factory=list)


class _CrawlerOutput(BaseModel):
    """All data gathered from a crawler project directory."""
    project_dir:   Path
    target_url:    str               = ""
    pages:         list[_PageRecord] = Field(default_factory=list)
    navigation:    list[dict]        = Field(default_factory=list)
    images:        list[dict]        = Field(default_factory=list)
    crawl_summary: dict              = Field(default_factory=dict)


class BusinessProfile(BaseModel):
    """Extended, persistence-ready business analysis result.

    Saved to ``<project_dir>/json/business_profile.json``.
    All AI-powered fields default to empty; deterministic fields are always
    populated even when AI is unavailable.
    """

    # ── Identity ───────────────────────────────────────────────────────────────
    company_name:      str = ""
    business_category: str = ""
    industry:          str = ""

    # ── Services / products ────────────────────────────────────────────────────
    main_services:      list[str] = Field(default_factory=list)
    secondary_services: list[str] = Field(default_factory=list)
    products:           list[str] = Field(default_factory=list)

    # ── Market positioning ────────────────────────────────────────────────────
    target_customers:     str       = ""
    service_areas:        list[str] = Field(default_factory=list)
    unique_selling_points: list[str] = Field(default_factory=list)
    trust_signals:        list[str] = Field(default_factory=list)

    # ── Brand ────────────────────────────────────────────────────────────────
    languages_used: list[str] = Field(default_factory=list)
    brand_tone:     str       = ""
    business_style: str       = ""

    # ── Contact ───────────────────────────────────────────────────────────────
    contact_email:   str            = ""
    contact_phone:   str            = ""
    contact_address: str            = ""
    social_links:    dict[str, str] = Field(default_factory=dict)

    # ── Content analysis ──────────────────────────────────────────────────────
    existing_content_quality: str       = ""
    existing_faq_topics:      list[str] = Field(default_factory=list)
    call_to_action_strategy:  str       = ""
    website_goals:            list[str] = Field(default_factory=list)
    customer_journey:         str       = ""

    # ── Assessment ────────────────────────────────────────────────────────────
    business_strengths:  list[str] = Field(default_factory=list)
    business_weaknesses: list[str] = Field(default_factory=list)
    overall_summary:     str       = ""

    # ── Metadata ──────────────────────────────────────────────────────────────
    source_url:        str       = ""
    pages_analyzed:    int       = 0
    analyzed_at:       str       = ""
    ai_provider_used:  str       = ""
    analysis_errors:   list[str] = Field(default_factory=list)


# ── Main class ────────────────────────────────────────────────────────────────

class BusinessAnalyzer:
    """Derives a structured business profile from WebsiteCrawler output.

    All AI reasoning is routed through AIRouter — no provider SDK is imported
    or called directly in this module.

    Deterministic extraction (emails, phones, social links, languages, page-
    type distribution, tone heuristics, industry keywords) runs independently
    of AI and always produces results.

    AI extraction is skipped gracefully when:
    - No API keys are configured in .env
    - AIRouter.complete() raises an exception

    Args:
        settings:  Application settings instance.
        ai_router: Optional pre-constructed AIRouter; created from settings
                   if omitted.
    """

    def __init__(
        self,
        settings:  "Settings",
        ai_router: AIRouter | None = None,
    ) -> None:
        self._settings  = settings
        self._ai_router = ai_router or AIRouter(settings)
        log.debug("BusinessAnalyzer initialised")

    # ── Public API ─────────────────────────────────────────────────────────────

    def analyze(self, crawl_result: CrawlResult, *, force_ai: bool = False) -> BusinessInfo:
        """Analyse a live CrawlResult and return a BusinessInfo.

        Attempts to load richer data from the project directory on disk
        (written by WebsiteCrawler).  Falls back to the CrawlResult directly
        if no project directory is found.

        If a saved business profile / ``target_business.md`` already exists and
        *force_ai* is False, Claude is **not** called — the cached profile is
        returned to avoid wasting tokens on restart.

        Args:
            crawl_result: Output from WebsiteCrawler.crawl().
            force_ai:     If True, re-run Claude even when a profile exists.

        Returns:
            Populated BusinessInfo.

        Raises:
            AnalysisError: If the crawl result contains zero pages.
        """
        if not crawl_result.pages:
            raise AnalysisError(
                "CrawlResult is empty — nothing to analyse",
                target_url=crawl_result.target_url,
            )

        log.info("Analysing: {url}", url=crawl_result.target_url)

        # Try to load richer on-disk data (per-page JSON with all meta fields)
        project_dir = self._infer_project_dir(crawl_result.target_url)
        if project_dir and project_dir.exists() and not force_ai:
            existing = self.load_saved_profile(project_dir)
            if existing is not None:
                log.info(
                    "Reusing existing business profile / target_business.md — "
                    "skipping Claude (tokens saved)"
                )
                return self._profile_to_business_info(existing)

        if project_dir and project_dir.exists():
            try:
                data = self.load_crawler_output(project_dir)
            except Exception as exc:
                log.warning(
                    "Falling back to CrawlResult data ({e})", e=exc,
                )
                data = self._crawl_result_to_output(crawl_result, project_dir)
        else:
            data = self._crawl_result_to_output(
                crawl_result,
                project_dir or (self._settings.projects_dir / "unknown"),
            )

        profile = self._run_analysis(data)

        if project_dir and project_dir.exists():
            try:
                self.save_profile(profile, project_dir)
            except Exception as exc:
                log.warning("Could not save business_profile.json: {e}", e=exc)

        return self._profile_to_business_info(profile)

    def analyze_from_directory(
        self,
        project_dir: Path,
        *,
        force_ai: bool = False,
    ) -> BusinessInfo:
        """Load crawler output from *project_dir* and run business analysis.

        Use this to re-analyse an already crawled site without re-crawling.
        Skips Claude when a saved profile / ``target_business.md`` exists
        unless *force_ai* is True.

        Args:
            project_dir: Root directory of a WebsiteCrawler project.
            force_ai:    If True, re-run Claude even when a profile exists.

        Returns:
            Populated BusinessInfo.

        Raises:
            AnalysisError: If the directory or json/ subdirectory is missing.
        """
        project_dir = Path(project_dir)
        if not project_dir.exists():
            raise AnalysisError(
                "Project directory not found", path=str(project_dir),
            )

        if not force_ai:
            existing = self.load_saved_profile(project_dir)
            if existing is not None:
                log.info(
                    "Reusing existing business profile / target_business.md — "
                    "skipping Claude (tokens saved)"
                )
                return self._profile_to_business_info(existing)

        log.info("Analysing from directory: {d}", d=project_dir)
        data    = self.load_crawler_output(project_dir)
        profile = self._run_analysis(data)
        self.save_profile(profile, project_dir)
        return self._profile_to_business_info(profile)

    def load_crawler_output(self, project_dir: Path) -> _CrawlerOutput:
        """Load and merge all JSON files from a crawler project directory.

        Files read:
        - ``json/pages.json``
        - ``json/navigation.json``
        - ``json/images.json``
        - ``json/crawl_summary.json``
        - ``json/pages/*.json``  (per-page rich data, up to max_pages)

        Args:
            project_dir: Root of a WebsiteCrawler project.

        Returns:
            Populated _CrawlerOutput ready for analysis.

        Raises:
            AnalysisError: If json/ directory does not exist.
        """
        project_dir = Path(project_dir)
        json_dir    = project_dir / "json"

        if not json_dir.exists():
            raise AnalysisError(
                "json/ directory not found — run WebsiteCrawler first",
                project_dir=str(project_dir),
            )

        log.info("Loading crawler output from {d}", d=project_dir)

        pages_summary = self._load_json(json_dir / "pages.json",         default=[])
        navigation    = self._load_json(json_dir / "navigation.json",    default=[])
        images        = self._load_json(json_dir / "images.json",        default=[])
        crawl_summary = self._load_json(json_dir / "crawl_summary.json", default={})

        target_url = (
            crawl_summary.get("target_url")
            or (pages_summary[0].get("url") if pages_summary else "")
        )

        # Load per-page rich JSON (bounded to avoid memory issues)
        rich_pages: list[dict] = []
        pages_dir = json_dir / "pages"
        if pages_dir.exists():
            for path in sorted(pages_dir.glob("*.json"))[:self._settings.crawler_max_pages]:
                rp = self._load_json(path, default=None)
                if rp and isinstance(rp, dict):
                    rich_pages.append(rp)

        log.info(
            "Loaded {np} page summaries + {rp} rich pages + {ni} images",
            np=len(pages_summary), rp=len(rich_pages), ni=len(images),
        )

        pages = self._build_page_records(
            pages_summary if isinstance(pages_summary, list) else [],
            rich_pages,
        )

        return _CrawlerOutput(
            project_dir   = project_dir,
            target_url    = target_url,
            pages         = pages,
            navigation    = navigation    if isinstance(navigation, list) else [],
            images        = images        if isinstance(images, list) else [],
            crawl_summary = crawl_summary if isinstance(crawl_summary, dict) else {},
        )

    def extract_name(self, crawl_result: CrawlResult) -> str:
        """Attempt to determine the company or brand name deterministically.

        Checks the home page <title> tag for a plausible company name by
        stripping common suffixes ("Home", "Welcome", "–", "|" etc.).

        Args:
            crawl_result: Source data from the crawler.

        Returns:
            Detected name string, or empty string if undetermined.
        """
        home_pages = [
            p for p in crawl_result.pages
            if (p.page_type.value if hasattr(p.page_type, "value") else p.page_type) == "home"
        ]
        candidates = home_pages or crawl_result.pages[:1]
        for page in candidates:
            title = page.title.strip()
            if not title:
                continue
            # Strip common title suffixes:  "Company – Home" → "Company"
            clean = re.split(r"\s*[\|\–\-—:]\s*", title)[0].strip()
            if len(clean) >= 2:
                return clean
        return ""

    def extract_services(self, crawl_result: CrawlResult) -> list[str]:
        """Heuristically extract service names from page headings.

        Args:
            crawl_result: Source data from the crawler.

        Returns:
            List of service/product strings found in H1 and H2 headings.
        """
        data = self._crawl_result_to_output(crawl_result)
        return self._extract_services_heuristic(data)

    def extract_contact(self, crawl_result: CrawlResult) -> dict[str, str]:
        """Parse contact details from all page text via regex.

        Args:
            crawl_result: Source data from the crawler.

        Returns:
            Dict with keys ``email``, ``phone``, ``all_emails``, ``all_phones``.
        """
        data = self._crawl_result_to_output(crawl_result)
        return self._extract_contact_from_output(data)

    def extract_social_links(self, crawl_result: CrawlResult) -> dict[str, str]:
        """Collect social network profile URLs from external links.

        Args:
            crawl_result: Source data from the crawler.

        Returns:
            Dict mapping platform name → profile URL.
        """
        data = self._crawl_result_to_output(crawl_result)
        return self._extract_social_from_pages(data)

    def infer_tone(self, crawl_result: CrawlResult) -> str:
        """Classify the brand tone of voice from keyword signals.

        Args:
            crawl_result: Source data from the crawler.

        Returns:
            One of: ``"professional"``, ``"casual"``, ``"technical"``,
            ``"formal"``, ``"friendly"``.
        """
        data = self._crawl_result_to_output(crawl_result)
        text = self._collect_text(data)
        return self._infer_tone_heuristic(text)

    def infer_industry(self, crawl_result: CrawlResult) -> str:
        """Classify the business industry from content keyword signals.

        Args:
            crawl_result: Source data from the crawler.

        Returns:
            Industry label string, or empty string if undetermined.
        """
        data = self._crawl_result_to_output(crawl_result)
        return self._infer_industry_heuristic(data)

    def extract_deterministic(
        self, data: "_CrawlerOutput | CrawlResult",
    ) -> dict:
        """Run all rule-based extractors and return a combined dict.

        No AI is called.  The result is used to:
        - populate fields that don't need reasoning (emails, phones)
        - provide pre-extracted context to the AI prompt
        - serve as a fallback when AI is unavailable

        Args:
            data: Either a _CrawlerOutput (preferred) or a CrawlResult.

        Returns:
            Dict with keys: emails, phones, social_links, languages,
            page_type_counts, total_pages, total_images, inferred_tone,
            inferred_industry, potential_services, has_contact_page, has_blog.
        """
        if isinstance(data, CrawlResult):
            data = self._crawl_result_to_output(data)

        all_text = self._collect_text(data)

        emails = list(dict.fromkeys(
            e.lower() for e in _EMAIL_RE.findall(all_text)
            if not any(e.endswith(ext) for ext in (".png", ".jpg", ".gif", ".svg", ".css"))
        ))
        phones  = self._extract_phones(all_text)
        social  = self._extract_social_from_pages(data)
        langs   = self._extract_languages(data)

        type_counts: dict[str, int] = {}
        for p in data.pages:
            type_counts[p.page_type] = type_counts.get(p.page_type, 0) + 1

        return {
            "emails":            emails,
            "phones":            phones,
            "social_links":      social,
            "languages":         langs,
            "page_type_counts":  type_counts,
            "total_pages":       len(data.pages),
            "total_images":      len(data.images),
            "inferred_tone":     self._infer_tone_heuristic(all_text),
            "inferred_industry": self._infer_industry_heuristic(data),
            "potential_services": self._extract_services_heuristic(data),
            "has_contact_page":  any(
                "contact" in p.page_type or "contact" in p.url or "kontakt" in p.url
                for p in data.pages
            ),
            "has_blog":          type_counts.get("blog", 0) > 0,
        }

    def save_profile(self, profile: BusinessProfile, project_dir: Path) -> Path:
        """Serialise *profile* to JSON and ``target_business.md``.

        Args:
            profile:     BusinessProfile to persist.
            project_dir: Root of a WebsiteCrawler project.

        Returns:
            Path of the written JSON file.
        """
        out = Path(project_dir) / "json" / "business_profile.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        write_versioned_json(out, profile.model_dump())
        log.info("Business profile → {p}", p=out)

        md_path = Path(project_dir) / "json" / "target_business.md"
        try:
            md_path.write_text(self._profile_to_markdown(profile), encoding="utf-8")
            log.info("Target business markdown → {p}", p=md_path)
        except OSError as exc:
            log.warning("Could not write target_business.md: {e}", e=exc)

        return out

    def load_saved_profile(self, project_dir: Path) -> BusinessProfile | None:
        """Load a previously saved profile if JSON or target_business.md exists.

        Returns:
            BusinessProfile or None when nothing reusable is on disk.
        """
        project_dir = Path(project_dir)
        json_path = project_dir / "json" / "business_profile.json"
        md_path = project_dir / "json" / "target_business.md"

        if json_path.is_file():
            try:
                raw = self._load_json(json_path, default=None)
                if isinstance(raw, dict) and (
                    raw.get("company_name") or raw.get("industry") or raw.get("main_services")
                ):
                    return BusinessProfile.model_validate(raw)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not load business_profile.json: {e}", e=exc)

        # Markdown alone signals "already analysed" — rebuild a minimal profile
        # so callers skip Claude without needing a perfect JSON parse.
        if md_path.is_file():
            try:
                text = md_path.read_text(encoding="utf-8").strip()
            except OSError:
                text = ""
            if text:
                return BusinessProfile(
                    company_name="(from target_business.md)",
                    unique_selling_points=[text[:500]],
                    source_url="",
                    pages_analyzed=0,
                )
        return None

    @staticmethod
    def _profile_to_markdown(profile: BusinessProfile) -> str:
        """Render a durable markdown snapshot of the target business analysis."""
        services = ", ".join(profile.main_services + profile.secondary_services) or "—"
        areas = ", ".join(profile.service_areas) or "—"
        usps = "\n".join(f"- {u}" for u in profile.unique_selling_points) or "- —"
        return (
            f"<!-- target-url: {profile.source_url} -->\n"
            f"# Target business profile\n\n"
            f"- **Company:** {profile.company_name or '—'}\n"
            f"- **Industry:** {profile.industry or '—'}\n"
            f"- **Services:** {services}\n"
            f"- **Service areas:** {areas}\n"
            f"- **Tone:** {profile.brand_tone or '—'}\n"
            f"- **Email:** {profile.contact_email or '—'}\n"
            f"- **Phone:** {profile.contact_phone or '—'}\n\n"
            f"## Unique selling points\n{usps}\n"
        )

    # ── Private: analysis pipeline ─────────────────────────────────────────────

    def _run_analysis(self, data: _CrawlerOutput) -> BusinessProfile:
        """Execute the full deterministic → AI → merge pipeline.

        Args:
            data: Loaded crawler output.

        Returns:
            Populated BusinessProfile.
        """
        log.info("=== Business analysis: {u} ===", u=data.target_url)

        deterministic = self.extract_deterministic(data)
        log.debug(
            "Deterministic: {e} email(s), {p} phone(s), {s} social, "
            "tone={t}, industry={i}",
            e=len(deterministic["emails"]),
            p=len(deterministic["phones"]),
            s=len(deterministic["social_links"]),
            t=deterministic["inferred_tone"],
            i=deterministic["inferred_industry"] or "—",
        )

        ai_result:   dict = {}
        ai_provider: str  = ""
        errors:      list[str] = []

        available = self._ai_router.available_providers()
        if not available:
            msg = "No AI provider configured — skipping AI analysis (set an API key in .env)"
            log.warning(msg)
            errors.append(msg)
        else:
            prompt = self._build_prompt(data, deterministic)
            try:
                raw_response, ai_provider = self._call_ai(prompt)
                ai_result = self._parse_ai_json(raw_response)
                log.info(
                    "AI analysis complete via {p} ({n} chars)",
                    p=ai_provider, n=len(raw_response),
                )
            except AIError as exc:
                log.error("AI analysis failed: {e}", e=exc)
                errors.append(f"AI error: {exc}")
            except Exception as exc:
                log.error("Unexpected error in AI analysis: {e}", e=exc)
                errors.append(f"Unexpected: {exc}")

        profile = self._merge_into_profile(deterministic, ai_result, data)
        profile.ai_provider_used = ai_provider
        profile.analysis_errors.extend(errors)
        profile.analyzed_at = datetime.now(timezone.utc).isoformat()

        log.info(
            "Profile created — company={c}, industry={i}",
            c=profile.company_name or "(unknown)",
            i=profile.industry or "(unknown)",
        )
        return profile

    # ── Private: prompt construction ───────────────────────────────────────────

    def _build_prompt(self, data: _CrawlerOutput, deterministic: dict) -> str:
        """Construct the AI analysis prompt from crawler data and pre-extractions.

        The prompt is structured in clearly labelled sections so Claude can
        locate information quickly.  Total content is bounded by
        ``_MAX_CONTENT_CHARS`` to respect context window limits.

        Args:
            data:          Loaded _CrawlerOutput.
            deterministic: Pre-extracted facts from extract_deterministic().

        Returns:
            Prompt string ready to send to AIRouter.
        """
        lines: list[str] = [
            "Analyse the following website information and extract structured business intelligence.",
            "",
            f"WEBSITE: {data.target_url}",
            "",
        ]

        # ── Navigation ────────────────────────────────────────────────────────
        if data.navigation:
            lines.append("=== NAVIGATION ===")
            for item in data.navigation[:25]:
                text = item.get("text", "").strip()
                url  = item.get("url", "")
                if text:
                    lines.append(f"  • {text}  [{url}]")
            lines.append("")

        # ── Pages ─────────────────────────────────────────────────────────────
        lines.append("=== PAGE CONTENT ===")
        chars_used = 0

        # Priority: home → about → services → others
        sorted_pages = sorted(data.pages, key=lambda p: _PAGE_PRIORITY.get(p.page_type, 9))

        for page in sorted_pages:
            if chars_used >= _MAX_CONTENT_CHARS:
                lines.append(f"  ... ({len(data.pages) - sorted_pages.index(page)} more pages not shown)")
                break

            lines.append(f"\n[{page.page_type.upper()}] {page.url}")
            if page.title:
                lines.append(f"  Title: {page.title}")
            if page.meta_description:
                lines.append(f"  Description: {page.meta_description}")
            if page.h1:
                lines.append(f"  H1: {' | '.join(page.h1[:3])}")
            if page.h2:
                lines.append(f"  H2: {' | '.join(page.h2[:6])}")
            if page.text_excerpt:
                # Key pages get more context
                limit = (
                    2_500 if page.page_type == "home" else
                    1_800 if page.page_type in ("about", "services") else
                    600
                )
                excerpt = page.text_excerpt[:limit].rstrip()
                if excerpt:
                    lines.append(f"  Text: {excerpt}")
                    chars_used += len(excerpt)

        lines.append("")

        # ── Pre-extracted facts ────────────────────────────────────────────────
        lines.append("=== PRE-EXTRACTED FACTS ===")
        emails = deterministic.get("emails", [])
        phones = deterministic.get("phones", [])
        langs  = deterministic.get("languages", [])
        lines.append(f"  Emails  : {', '.join(emails[:5]) or 'none found'}")
        lines.append(f"  Phones  : {', '.join(phones[:5]) or 'none found'}")
        lines.append(f"  Lang(s) : {', '.join(langs) or 'not detected'}")
        lines.append(f"  Pages   : {deterministic.get('total_pages', 0)}")
        lines.append(f"  Images  : {deterministic.get('total_images', 0)}")
        lines.append(f"  Has blog: {deterministic.get('has_blog', False)}")
        lines.append("")

        # ── Image metadata (as brand signals) ─────────────────────────────────
        if data.images:
            lines.append("=== IMAGE METADATA (sample) ===")
            for img in data.images[:_MAX_IMAGE_RECORDS]:
                fn  = img.get("filename", "")
                alt = img.get("alt_text", "")
                if fn or alt:
                    lines.append(f"  {fn}: '{alt}'")
            lines.append("")

        # ── Required JSON response schema ─────────────────────────────────────
        lines.append("=== RESPONSE FORMAT ===")
        lines.append(
            "Return ONLY a JSON object with exactly these keys. "
            "Use empty string \"\" or [] for unknown fields. Be specific — "
            "base every answer strictly on the content above."
        )
        schema = {
            "company_name":            "official business name",
            "business_category":       "e.g. 'local service', 'e-commerce', 'B2B software'",
            "industry":                "specific industry sector",
            "main_services":           ["primary service 1", "primary service 2"],
            "secondary_services":      ["additional service"],
            "products":                ["product name if any"],
            "target_customers":        "who they serve",
            "service_areas":           ["city, region, or country"],
            "unique_selling_points":   ["what differentiates them"],
            "trust_signals":           ["certifications, awards, years in business, testimonials"],
            "brand_tone":              "professional | friendly | technical | casual | formal",
            "business_style":          "traditional | modern | premium | budget | innovative",
            "call_to_action_strategy": "main call-to-action approach",
            "website_goals":           ["goal 1", "goal 2"],
            "customer_journey":        "how visitors move through the site",
            "existing_content_quality": "poor | basic | adequate | good | excellent",
            "existing_faq_topics":     ["FAQ topic if found"],
            "business_strengths":      ["strength visible from website"],
            "business_weaknesses":     ["gap or weakness visible from website"],
            "overall_summary":         "2-3 sentence business summary",
        }
        lines.append(json.dumps(schema, indent=2, ensure_ascii=False))

        return "\n".join(lines)

    # ── Private: AI call ───────────────────────────────────────────────────────

    def _call_ai(self, prompt: str) -> tuple[str, str]:
        """Send *prompt* to the best available AI provider via AIRouter.

        Args:
            prompt: Fully assembled analysis prompt.

        Returns:
            Tuple of (response_text, provider_name).

        Raises:
            AIError: If no provider is available, or the call fails.
        """
        available = self._ai_router.available_providers()
        if not available:
            raise AIError("No AI providers have API keys configured")
        if not self._ai_router.is_available(AIProvider.CLAUDE):
            raise AIError(
                "Claude API key required for business analysis (V2). "
                "Set CLAUDE_API_KEY in .env",
            )

        log.info(
            "Claude business request ({n:,} chars)",
            n=len(prompt),
        )

        try:
            response = self._ai_router.complete(
                prompt,
                system=load_prompt_or_default("business", _FALLBACK_SYSTEM_PROMPT),
                provider=AIProvider.CLAUDE,
            )
        except NotImplementedError:
            raise AIError(
                "AIRouter.complete() is not yet implemented. "
                "Phase 5 will implement AI provider calls in ai_router.py.",
            )
        except Exception as exc:
            raise AIError(f"AI call failed: {exc}") from exc

        if not response or not response.strip():
            raise AIError("AI returned an empty response")

        return response, AIProvider.CLAUDE.value

    def _parse_ai_json(self, response: str) -> dict:
        """Extract and parse the JSON object from an AI response string.

        Handles three common response formats:
        - Raw JSON object
        - JSON wrapped in ````json … ```` code fences
        - JSON somewhere within a longer text response

        Args:
            response: Raw string from the AI provider.

        Returns:
            Parsed dict, or empty dict if parsing fails.
        """
        text = response.strip()
        if not text:
            return {}

        # Strip ```json … ``` code fences
        fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.DOTALL)
        if fence:
            text = fence.group(1)
        else:
            # Extract first {…} block (handles leading/trailing prose)
            start = text.find("{")
            end   = text.rfind("}")
            if start != -1 and end > start:
                text = text[start : end + 1]

        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            log.warning("JSON parse error in AI response: {e}", e=exc)
            log.debug("AI response (first 500 chars): {r}", r=response[:500])
            return {}

        if not isinstance(result, dict):
            log.warning("AI returned non-dict JSON (type={t})", t=type(result).__name__)
            return {}

        return result

    # ── Private: merging ──────────────────────────────────────────────────────

    def _merge_into_profile(
        self,
        deterministic: dict,
        ai_result:     dict,
        data:          _CrawlerOutput,
    ) -> BusinessProfile:
        """Combine deterministic evidence and AI results into a BusinessProfile.

        Rule: deterministic data wins for factual fields (email, phone).
        AI data is used for reasoning-required fields.

        Args:
            deterministic: Output of extract_deterministic().
            ai_result:     Parsed AI JSON (may be empty if AI failed).
            data:          Source _CrawlerOutput.

        Returns:
            Fully populated BusinessProfile.
        """
        # Normalise AI list fields so we never get unexpected types
        for field in _LIST_FIELDS:
            raw = ai_result.get(field)
            if raw is None:
                ai_result[field] = []
            elif isinstance(raw, str):
                ai_result[field] = [raw] if raw.strip() else []
            elif not isinstance(raw, list):
                ai_result[field] = []
            else:
                # Filter empty strings from lists
                ai_result[field] = [str(v).strip() for v in raw if str(v).strip()]

        emails = deterministic.get("emails", [])
        phones = deterministic.get("phones", [])

        return BusinessProfile(
            # Identity — AI
            company_name      = ai_result.get("company_name", ""),
            business_category = ai_result.get("business_category", ""),
            industry          = ai_result.get("industry",
                                deterministic.get("inferred_industry", "")),

            # Services — AI
            main_services      = ai_result.get("main_services",
                                 deterministic.get("potential_services", [])),
            secondary_services = ai_result.get("secondary_services", []),
            products           = ai_result.get("products", []),

            # Market — AI
            target_customers      = ai_result.get("target_customers", ""),
            service_areas         = ai_result.get("service_areas", []),
            unique_selling_points = ai_result.get("unique_selling_points", []),
            trust_signals         = ai_result.get("trust_signals", []),

            # Brand — AI with heuristic fallback
            brand_tone     = ai_result.get("brand_tone",
                             deterministic.get("inferred_tone", "professional")),
            business_style = ai_result.get("business_style", ""),

            # Deterministic — always from rule-based extraction
            contact_email  = emails[0] if emails else "",
            contact_phone  = phones[0] if phones else "",
            social_links   = deterministic.get("social_links", {}),
            languages_used = deterministic.get("languages", []),

            # Content — AI
            existing_content_quality = ai_result.get("existing_content_quality", ""),
            existing_faq_topics      = ai_result.get("existing_faq_topics", []),
            call_to_action_strategy  = ai_result.get("call_to_action_strategy", ""),
            website_goals            = ai_result.get("website_goals", []),
            customer_journey         = ai_result.get("customer_journey", ""),

            # Assessment — AI
            business_strengths  = ai_result.get("business_strengths", []),
            business_weaknesses = ai_result.get("business_weaknesses", []),
            overall_summary     = ai_result.get("overall_summary", ""),

            # Metadata
            source_url     = data.target_url,
            pages_analyzed = len(data.pages),
        )

    def _profile_to_business_info(self, profile: BusinessProfile) -> BusinessInfo:
        """Convert the extended BusinessProfile to the shared BusinessInfo type.

        Args:
            profile: Completed BusinessProfile.

        Returns:
            BusinessInfo suitable for downstream modules.
        """
        return BusinessInfo(
            name            = profile.company_name,
            industry        = profile.industry,
            location        = ", ".join(profile.service_areas) if profile.service_areas else "",
            services        = (profile.main_services + profile.secondary_services),
            target_audience = profile.target_customers,
            unique_value    = profile.unique_selling_points[0]
                              if profile.unique_selling_points else "",
            tone_of_voice   = profile.brand_tone,
            contact_email   = profile.contact_email,
            contact_phone   = profile.contact_phone,
            social_links    = profile.social_links,
        )

    # ── Private: data loading ──────────────────────────────────────────────────

    @staticmethod
    def _load_json(path: Path, *, default: Any) -> Any:
        """Load JSON, unwrapping versioned list envelopes when present."""
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Cannot load JSON {p}: {e}", p=path, e=exc)
            return default
        data = unwrap_json(raw)
        if default is not None and type(default) is list and not isinstance(data, list):
            return default
        if default is not None and type(default) is dict and not isinstance(data, dict):
            return default
        return data

    def _build_page_records(
        self,
        pages_summary: list[dict],
        rich_pages:    list[dict],
    ) -> list[_PageRecord]:
        """Merge pages.json entries with per-page rich JSON into _PageRecord list.

        Rich pages provide h1/h2, language, full text, and external links.
        Pages not found in the rich set are populated from the summary only.

        Args:
            pages_summary: Entries from pages.json.
            rich_pages:    Per-page JSON objects from json/pages/*.json.

        Returns:
            Merged, priority-sorted list of _PageRecord.
        """
        rich_by_url: dict[str, dict] = {
            rp["url"]: rp for rp in rich_pages if rp.get("url")
        }

        records: list[_PageRecord] = []
        seen_urls: set[str] = set()

        # Sort summaries by page type priority
        summaries = sorted(
            pages_summary,
            key=lambda p: _PAGE_PRIORITY.get(p.get("page_type", "unknown"), 9),
        )

        for summary in summaries:
            url = summary.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            rich = rich_by_url.get(url, {})
            text  = rich.get("text_content") or ""

            records.append(_PageRecord(
                url              = url,
                title            = rich.get("title") or summary.get("title", ""),
                meta_description = rich.get("meta_description", ""),
                page_type        = summary.get("page_type", "unknown"),
                h1               = rich.get("h1") or [],
                h2               = rich.get("h2") or [],
                text_excerpt     = text,
                word_count       = summary.get("word_count", 0),
                language         = rich.get("language", ""),
                external_links   = rich.get("external_links") or [],
            ))

        return records

    # ── Private: deterministic extractors ─────────────────────────────────────

    def _collect_text(self, data: _CrawlerOutput) -> str:
        """Concatenate all available text from pages for regex extraction."""
        parts: list[str] = [data.target_url]
        for p in data.pages:
            parts.append(p.title)
            parts.append(p.meta_description)
            parts.append(" ".join(p.h1 + p.h2))
            parts.append(p.text_excerpt[:2_000])
        return "\n".join(parts)

    def _extract_contact_from_output(self, data: _CrawlerOutput) -> dict[str, str]:
        """Extract contact information from all page text."""
        text   = self._collect_text(data)
        emails = list(dict.fromkeys(
            e.lower() for e in _EMAIL_RE.findall(text)
            if not any(e.endswith(x) for x in (".png", ".jpg", ".gif", ".svg", ".css"))
        ))
        phones = self._extract_phones(text)
        return {
            "email":      emails[0] if emails else "",
            "phone":      phones[0] if phones else "",
            "all_emails": emails,
            "all_phones": phones,
        }

    def _extract_phones(self, text: str) -> list[str]:
        """Return deduplicated phone strings from *text*."""
        seen_digits: set[str] = set()
        result: list[str] = []
        for raw in _PHONE_RE.findall(text):
            phone  = re.sub(r"\s+", " ", raw).strip()
            digits = re.sub(r"\D", "", phone)
            if len(digits) >= 6 and digits not in seen_digits:
                seen_digits.add(digits)
                result.append(phone)
        return result[:10]

    def _extract_social_from_pages(self, data: _CrawlerOutput) -> dict[str, str]:
        """Scan navigation and external links for social media profile URLs."""
        social: dict[str, str] = {}

        # Navigation first (highest quality source)
        for item in data.navigation:
            url = item.get("url", "")
            for domain, platform in _SOCIAL_DOMAINS.items():
                if domain in url and platform not in social:
                    social[platform] = url

        # External links from pages
        for page in data.pages:
            for ext_url in page.external_links:
                for domain, platform in _SOCIAL_DOMAINS.items():
                    if domain in ext_url and platform not in social:
                        social[platform] = ext_url

        return social

    def _extract_languages(self, data: _CrawlerOutput) -> list[str]:
        """Collect HTML lang attribute values from all pages."""
        seen:  set[str]  = set()
        langs: list[str] = []
        for p in data.pages:
            if p.language and p.language not in seen:
                seen.add(p.language)
                langs.append(p.language)
        return langs

    def _infer_tone_heuristic(self, text: str) -> str:
        """Keyword-based brand tone classification.

        Args:
            text: Combined page text.

        Returns:
            One of: professional, casual, technical, formal, friendly.
        """
        t = text.lower()

        scores: dict[str, int] = {
            "technical":    sum(1 for w in [
                "solution", "platform", "infrastructure", "integration",
                "api", "system", "algorithm", "framework", "architecture",
            ] if w in t),
            "formal":       sum(1 for w in [
                "herewith", "pursuant", "aforementioned", "hereby",
                "henceforth", "notwithstanding",
            ] if w in t),
            "casual":       sum(1 for w in [
                "hey", "awesome", "cool", "love", "fun", "great", "happy",
                "hi there", "let's",
            ] if w in t),
            "friendly":     sum(1 for w in [
                "welcome", "team", "community", "together", "help you",
                "here for you", "we care",
            ] if w in t),
            "professional": sum(1 for w in [
                "expertise", "certified", "licensed", "experienced",
                "qualified", "established", "dedicated", "commitment",
            ] if w in t),
        }

        best = max(scores, key=lambda k: scores[k])
        return best if scores[best] > 0 else "professional"

    def _infer_industry_heuristic(self, data: _CrawlerOutput) -> str:
        """Keyword-based industry classification.

        Args:
            data: _CrawlerOutput with page text.

        Returns:
            Industry label, or empty string if no clear match.
        """
        text = self._collect_text(data).lower()

        _categories: dict[str, list[str]] = {
            "construction":  ["bau", "renovierung", "construction", "renovation",
                              "contractor", "building", "dachdecke", "sanitär", "elektro"],
            "healthcare":    ["health", "medical", "clinic", "dental", "therapy",
                              "arzt", "gesundheit", "praxis", "therapie"],
            "hospitality":   ["restaurant", "café", "hotel", "food", "menu",
                              "dining", "essen", "gastronomie", "catering"],
            "technology":    ["software", "app", "digital", "tech", "cloud",
                              "coding", "developer", "saas", "api"],
            "retail":        ["shop", "store", "buy", "product", "price",
                              "cart", "kaufen", "bestellen", "lieferung"],
            "consulting":    ["consulting", "beratung", "strategy", "management",
                              "advisory", "unternehmensberatung"],
            "education":     ["education", "training", "course", "school",
                              "learning", "lernen", "weiterbildung", "kurs"],
            "finance":       ["finance", "insurance", "investment", "bank",
                              "finanz", "versicherung", "kredit", "anlage"],
            "legal":         ["law", "lawyer", "attorney", "legal",
                              "rechts", "anwalt", "kanzlei", "juristisch"],
            "real_estate":   ["immobilien", "real estate", "property",
                              "wohnung", "haus", "miete", "makler"],
            "automotive":    ["auto", "car", "vehicle", "kfz", "fahrzeug",
                              "werkstatt", "reifen", "tuning"],
            "beauty":        ["beauty", "salon", "hair", "nail", "spa",
                              "friseur", "kosmetik", "wellness"],
        }

        scores: dict[str, int] = {}
        for industry, keywords in _categories.items():
            scores[industry] = sum(1 for kw in keywords if kw in text)

        best = max(scores, key=lambda k: scores[k])
        return best.replace("_", " ") if scores[best] > 0 else ""

    def _extract_services_heuristic(self, data: _CrawlerOutput) -> list[str]:
        """Extract service candidate strings from headings on relevant pages.

        Args:
            data: _CrawlerOutput with page records.

        Returns:
            Deduplicated list of heading strings from services/home/about pages.
        """
        services: list[str] = []
        seen:     set[str]  = set()

        for page in data.pages:
            if page.page_type not in ("services", "home", "about"):
                continue
            for heading in page.h1 + page.h2:
                clean = heading.strip()
                if clean and clean.lower() not in seen and 3 <= len(clean) <= 80:
                    seen.add(clean.lower())
                    services.append(clean)

        return services[:20]

    # ── Private: conversion helpers ────────────────────────────────────────────

    def _crawl_result_to_output(
        self,
        crawl_result: CrawlResult,
        project_dir:  Path | None = None,
    ) -> _CrawlerOutput:
        """Convert a CrawlResult to a _CrawlerOutput for analysis.

        Args:
            crawl_result: Live CrawlResult from WebsiteCrawler.crawl().
            project_dir:  Optional project directory path for the model.

        Returns:
            _CrawlerOutput ready for analysis.
        """
        pdir = project_dir or (self._settings.projects_dir / "unknown")

        sorted_pages = sorted(
            crawl_result.pages,
            key=lambda p: _PAGE_PRIORITY.get(
                p.page_type.value if hasattr(p.page_type, "value") else str(p.page_type), 9,
            ),
        )

        records: list[_PageRecord] = []
        for pd in sorted_pages:
            pt = pd.page_type.value if hasattr(pd.page_type, "value") else str(pd.page_type)
            records.append(_PageRecord(
                url              = pd.url,
                title            = pd.title,
                meta_description = pd.description,
                page_type        = pt,
                h1               = [h for h in pd.headings[:3]],
                text_excerpt     = pd.text_content[:3_000] if pd.text_content else "",
            ))

        return _CrawlerOutput(
            project_dir = pdir,
            target_url  = crawl_result.target_url,
            pages       = records,
        )

    def _infer_project_dir(self, target_url: str) -> Path | None:
        """Guess the project directory from a target URL (mirrors crawler logic).

        Args:
            target_url: Root URL of the crawled site.

        Returns:
            Expected project directory Path, or None if URL is unparseable.
        """
        try:
            netloc = urlparse(target_url).netloc
            folder = re.sub(r"[.:]", "-", netloc.removeprefix("www."))
            folder = re.sub(r"-+", "-", folder).strip("-")
            return self._settings.projects_dir / folder if folder else None
        except Exception:
            return None
