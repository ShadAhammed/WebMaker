"""
webmaker.modules.content_optimizer
====================================
Generates optimised, SEO-ready website content for the new WordPress site.

Pipeline
--------
1. Load all prior module outputs from the project directory:
   - business_profile.json  → client facts, services, tone, contacts
   - comparison_report.json → structural ideas from competitor analysis
   - competitor_analysis.json → per-competitor profiles
   - pages.json             → existing crawled page content
2. For each standard page slug (homepage, about, services, contact, faq):
   a. Build a Claude prompt that embeds client facts + existing content +
      competitor inspiration (clearly marked as ideas only, never copy)
   b. Call Claude via AIRouter → raw JSON page content
   c. Parse response → rich page dict
   d. Send to DeepSeek via AIRouter for independent review
   e. Store generated content + review separately
3. Generate meta_data.json with per-page SEO meta tags
4. Persist all outputs as structured JSON files

Strict content rules
--------------------
- NEVER invent services, certifications, pricing, guarantees, or locations
- NEVER fabricate customer reviews, company history, or statistics
- Use "[MISSING INFORMATION]" as placeholder for genuinely unknown facts
- Competitor insights are inspiration only — never copied verbatim
- All AI communication goes through AIRouter (Claude = generation, DeepSeek = review)

Primary class: ContentOptimizer
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from webmaker.core.exceptions import AIError, AnalysisError
from webmaker.core.logging import get_logger
from webmaker.core.prompts import load_prompt_or_default
from webmaker.core.schema import unwrap_json, write_versioned_json
from webmaker.core.types import AIProvider, AnalysisResult, BusinessInfo
from webmaker.modules.ai_router import AIRouter

if TYPE_CHECKING:
    from webmaker.config.settings import Settings

log = get_logger("content_optimizer")


# ── System prompts (fallbacks if prompts/*.md missing) ─────────────────────────

_FALLBACK_GENERATION_SYSTEM = (
    "You are a professional copywriter specialising in German local business websites. "
    "Write content that is professional, natural, and human — never AI-sounding. "
    "Preserve ALL factual information exactly as provided. "
    "Use [MISSING INFORMATION] for any facts not provided — never invent details. "
    "Respond ONLY with a single valid JSON object — no markdown, no code fences, "
    "no explanatory text before or after the JSON."
)

_FALLBACK_REVIEW_SYSTEM = (
    "You are a critical content reviewer for German local business websites. "
    "Identify factual inconsistencies, AI-sounding language, repetition, and "
    "logical problems. Do NOT rewrite the content — only provide review comments. "
    "Respond ONLY with a single valid JSON object."
)

# ── Content constants ──────────────────────────────────────────────────────────

_MAX_EXISTING_CHARS = 3_000    # existing page text chars to include per slug
_MAX_COMPETITOR_ITEMS = 4      # max ideas per competitor insight category

_AI_BUZZWORDS = frozenset({
    "synergy", "leverage", "holistic", "seamless", "empower", "stakeholder",
    "paradigm", "utilize", "actionable", "robust", "comprehensive",
    "cutting-edge", "state-of-the-art", "innovative", "transformative",
    "game-changer", "world-class", "best-in-class", "value-added",
    "thought leader", "ecosystem", "deep-dive", "bandwidth",
})

_STANDARD_PAGE_SLUGS = ("homepage", "about", "services", "contact", "faq")

# Crawled page_type → optimizer page slug
_PAGE_TYPE_MAP: dict[str, str] = {
    "home":     "homepage",
    "about":    "about",
    "services": "services",
    "contact":  "contact",
    "blog":     "faq",        # blog content often provides FAQ fodder
}

# ── Per-page JSON schemas (sent to Claude) ─────────────────────────────────────

_PAGE_SCHEMAS: dict[str, Any] = {
    "homepage": {
        "meta_title":       "Company – Industry | City (max 60 chars)",
        "meta_description": "Compelling description under 160 chars, includes location",
        "hero": {
            "heading":       "H1 — main benefit or company identity",
            "subheading":    "Supporting sentence (1-2 lines)",
            "cta_primary":   "Main button text",
            "cta_secondary": "Secondary button text or empty string",
        },
        "intro": "2-3 sentence company introduction paragraph",
        "services_overview": {
            "heading":  "H2 for the services section",
            "services": [{"name": "service name", "short_description": "1-2 sentences"}],
        },
        "why_choose_us": {
            "heading": "H2 — why choose us section title",
            "points":  [{"heading": "Point title", "text": "2-3 supporting sentences"}],
        },
        "cta_section": {
            "heading":    "CTA section heading",
            "text":       "1-2 motivating sentences",
            "cta_button": "Button label",
        },
        "footer_tagline": "Short one-sentence company tagline",
    },
    "about": {
        "meta_title":       "About Company – Industry | City (max 60 chars)",
        "meta_description": "About page description (max 160 chars)",
        "hero_heading":     "H1 heading for the about page",
        "company_story":    "3-5 sentences about who, what, and when. [MISSING INFORMATION] for unknown facts.",
        "mission_statement": "One sentence mission. [MISSING INFORMATION] if unknown.",
        "values": [{"name": "Value name", "description": "2-3 sentences"}],
        "team_intro":       "Brief team intro or [MISSING INFORMATION]",
        "cta_section": {
            "heading":    "CTA heading",
            "text":       "1-2 motivating sentences",
            "cta_button": "Button label",
        },
    },
    "services": {
        "meta_title":       "Services – Company | Industry (max 60 chars)",
        "meta_description": "Services overview (max 160 chars)",
        "hero_heading":     "H1 heading for the services page",
        "intro":            "2-3 sentence intro to the service range",
        "services": [
            {
                "name":          "Exact service name from business profile",
                "slug":          "url-friendly-slug",
                "heading":       "H2 heading for this service",
                "description":   "3-5 sentences. Preserve all factual details.",
                "benefits":      ["Benefit 1", "Benefit 2", "Benefit 3"],
                "process_steps": ["Step 1", "Step 2"],
                "cta":           "Service CTA button label",
            }
        ],
    },
    "contact": {
        "meta_title":       "Contact – Company | City (max 60 chars)",
        "meta_description": "Contact page description (max 160 chars)",
        "hero_heading":     "H1 heading for the contact page",
        "intro":            "1-2 welcoming sentences",
        "contact_section": {
            "heading":  "H2 for the contact form area",
            "text":     "Short supporting text",
            "form_cta": "Form submit button label",
        },
    },
    "faq": {
        "meta_title":   "FAQ – Company | Industry (max 60 chars)",
        "meta_description": "FAQ description (max 160 chars)",
        "hero_heading": "H1 heading",
        "intro":        "1-2 sentence intro to the FAQ",
        "faqs": [
            {
                "question": "A realistic question a customer would ask",
                "answer":   "Factual answer. [MISSING INFORMATION] for unknown details.",
            }
        ],
    },
}

_REVIEW_SCHEMA: dict[str, Any] = {
    "factual_consistency":  "good | issues_found | critical_issues",
    "invented_information": ["List any invented facts not in the business profile"],
    "missing_information":  ["List [MISSING INFORMATION] tags or obvious content gaps"],
    "readability":          "excellent | good | adequate | poor",
    "ai_sounding_phrases":  ["Phrases that sound unnatural or AI-generated"],
    "repetition_issues":    ["Repeated phrases or ideas across sections"],
    "logical_flow":         "excellent | good | adequate | poor",
    "clarity":              "excellent | good | adequate | poor",
    "overall_rating":       "excellent | good | adequate | needs_revision",
    "suggestions":          ["Specific actionable improvement suggestions"],
}


# ── Module-local data models ──────────────────────────────────────────────────

class _BusinessContext(BaseModel):
    """Client business data loaded from business_profile.json."""

    company_name:       str       = ""
    industry:           str       = ""
    main_services:      list[str] = Field(default_factory=list)
    secondary_services: list[str] = Field(default_factory=list)
    target_customers:   str       = ""
    service_areas:      list[str] = Field(default_factory=list)
    brand_tone:         str       = ""
    unique_value:       str       = ""
    trust_signals:      list[str] = Field(default_factory=list)
    contact_email:      str       = ""
    contact_phone:      str       = ""
    contact_address:    str       = ""
    source_url:         str       = ""
    languages:          list[str] = Field(default_factory=list)
    faq_topics:         list[str] = Field(default_factory=list)
    strengths:          list[str] = Field(default_factory=list)
    weaknesses:         list[str] = Field(default_factory=list)
    cta_strategy:       str       = ""


class _PageSource(BaseModel):
    """Existing crawled content for one page."""

    url:          str       = ""
    page_type:    str       = ""
    title:        str       = ""
    description:  str       = ""
    headings:     list[str] = Field(default_factory=list)
    text_content: str       = ""


class _CompetitorContext(BaseModel):
    """Distilled competitor insights for content inspiration."""

    navigation_ideas:           list[str] = Field(default_factory=list)
    trust_building_elements:    list[str] = Field(default_factory=list)
    homepage_structure_ideas:   list[str] = Field(default_factory=list)
    service_presentation_ideas: list[str] = Field(default_factory=list)
    customer_journey_ideas:     list[str] = Field(default_factory=list)
    cta_ideas:                  list[str] = Field(default_factory=list)
    faq_ideas:                  list[str] = Field(default_factory=list)
    overall_opportunities:      list[str] = Field(default_factory=list)
    structure_stories:          list[str] = Field(default_factory=list)
    structure_markdown:         str       = ""


class _ContentReview(BaseModel):
    """DeepSeek review result for a single page."""

    page_slug:             str       = ""
    reviewer:              str       = "deepseek"
    factual_consistency:   str       = ""
    invented_information:  list[str] = Field(default_factory=list)
    missing_information:   list[str] = Field(default_factory=list)
    readability:           str       = ""
    ai_sounding_phrases:   list[str] = Field(default_factory=list)
    repetition_issues:     list[str] = Field(default_factory=list)
    logical_flow:          str       = ""
    clarity:               str       = ""
    overall_rating:        str       = ""
    suggestions:           list[str] = Field(default_factory=list)
    review_skipped:        bool      = False
    skip_reason:           str       = ""


# ── Main class ────────────────────────────────────────────────────────────────

class ContentOptimizer:
    """Generates optimised page copy, meta tags, and SEO structure.

    Responsibilities
    ----------------
    - Load prior module outputs (business profile, competitor insights,
      crawled page content) from the project directory.
    - Generate per-page content via Claude (through AIRouter) following
      strict factual accuracy rules.
    - Review generated content with DeepSeek (through AIRouter).
    - Produce structured JSON output files for WordPressGenerator.
    - Provide in-memory generation helpers (generate_page_content,
      generate_meta_tags, suggest_headings, etc.) for pipeline use.

    Args:
        settings:  Application settings instance.
        ai_router: Optional pre-constructed AIRouter; created from
                   settings if omitted.
    """

    def __init__(
        self,
        settings:  "Settings",
        ai_router: AIRouter | None = None,
    ) -> None:
        self._settings  = settings
        self._ai_router = ai_router or AIRouter(settings)
        log.debug("ContentOptimizer initialised")

    # ── Primary entry point ────────────────────────────────────────────────────

    def optimize_pages(
        self,
        project_dir: Path,
        *,
        slugs: list[str] | tuple[str, ...] | None = None,
        skip_review: bool = False,
    ) -> dict[str, Any]:
        """Generate a subset of pages (used by the Job System)."""
        return self.optimize_from_directory(
            project_dir,
            page_slugs=tuple(slugs) if slugs else None,
            skip_review=skip_review,
        )

    def optimize_from_directory(
        self,
        project_dir:  Path,
        *,
        page_slugs:   tuple[str, ...] | None = None,
        skip_review:  bool = False,
    ) -> dict[str, Any]:
        """Load all prior outputs, generate content, review with DeepSeek.

        Args:
            project_dir: Client's WebsiteCrawler project directory.
            page_slugs:  Subset of page slugs to generate; defaults to all
                         standard pages.
            skip_review: If True, skip the DeepSeek review step (faster,
                         useful when DeepSeek key is not configured).

        Returns:
            Summary dict with ``pages_generated``, ``reviews_generated``,
            and ``errors``.
        """
        project_dir = Path(project_dir)
        slugs = page_slugs or _STANDARD_PAGE_SLUGS

        log.info(
            "=== Content optimisation started: {d} ===", d=project_dir.name
        )

        # ── 1. Load inputs ─────────────────────────────────────────────────────
        biz_ctx    = self._load_business_context(project_dir)
        comp_ctx   = self._load_competitor_context(project_dir)
        page_srcs  = self._load_page_sources(project_dir)

        if not biz_ctx:
            log.warning(
                "No business_profile.json found in {d} — "
                "content quality will be limited",
                d=project_dir / "json",
            )
            biz_ctx = _BusinessContext()

        # ── 2. Generate each page ──────────────────────────────────────────────
        generated:  dict[str, dict] = {}
        reviews:    dict[str, dict] = {}
        meta_pages: dict[str, dict] = {}
        errors:     list[str]       = []

        for slug in slugs:
            log.info("Generating content for page: {s}", s=slug)
            try:
                content = self._generate_page(
                    slug, biz_ctx, page_srcs, comp_ctx
                )
                generated[slug] = content
                log.info("Content generated for: {s}", s=slug)

                # Build meta tags
                meta_pages[slug] = {
                    "title":       content.get("meta_title", ""),
                    "description": content.get("meta_description", ""),
                }

                # DeepSeek review
                if not skip_review:
                    review = self._review_page(slug, content, biz_ctx)
                    reviews[slug] = review.model_dump()

            except AIError as exc:
                msg = f"{slug}: AI error — {exc}"
                errors.append(msg)
                log.error(msg)
            except Exception as exc:
                msg = f"{slug}: unexpected error — {exc}"
                errors.append(msg)
                log.error(msg)

        # ── 3. Save outputs ────────────────────────────────────────────────────
        self._save_outputs(project_dir, generated, reviews, meta_pages)

        result = {
            "pages_generated":  list(generated.keys()),
            "reviews_generated": list(reviews.keys()),
            "errors":           errors,
        }
        log.info(
            "Optimisation complete: {n} pages, {e} errors",
            n=len(generated), e=len(errors),
        )
        return result

    # ── Pipeline stub interface (from Phase 2) ─────────────────────────────────

    def optimize(self, analysis: AnalysisResult) -> dict[str, "PageContent"]:
        """Generate optimised content from an in-memory AnalysisResult.

        Uses the business profile and competitor data from *analysis* without
        any disk I/O.  For disk-based workflows use
        :meth:`optimize_from_directory`.

        Args:
            analysis: Output from CompetitorAnalyzer.

        Returns:
            Mapping of page slug → PageContent.

        Raises:
            AnalysisError: If the analysis result contains no business data.
        """
        if not analysis.business.name and not analysis.business.services:
            raise AnalysisError(
                "AnalysisResult contains no usable business data. "
                "Ensure BusinessAnalyzer ran successfully before ContentOptimizer.",
            )

        pages: dict[str, PageContent] = {}
        for slug in _STANDARD_PAGE_SLUGS:
            try:
                pages[slug] = self.generate_page_content(
                    slug, analysis.business, analysis
                )
            except Exception as exc:
                log.warning("Skipping {s} due to error: {e}", s=slug, e=exc)

        return pages

    def generate_page_content(
        self,
        page_slug: str,
        business:  BusinessInfo,
        analysis:  AnalysisResult,
    ) -> "PageContent":
        """Generate copy for a single page from in-memory data.

        Calls Claude (via AIRouter) and converts the response into a
        ``PageContent`` model.  If Claude is unavailable, returns a
        ``PageContent`` populated with rule-based defaults.

        Args:
            page_slug: Target page slug (``"homepage"``, ``"about"``, etc.).
            business:  Business profile.
            analysis:  Full analysis result (used for competitor context).

        Returns:
            PageContent with title, body, meta tags, and headings.
        """
        biz_ctx = self._business_info_to_context(business)

        # Build minimal competitor context from analysis recommendations
        comp_ctx = _CompetitorContext(
            overall_opportunities=analysis.recommendations[:_MAX_COMPETITOR_ITEMS],
            service_presentation_ideas=analysis.content_gaps[:_MAX_COMPETITOR_ITEMS],
        )

        raw_content: dict = {}
        try:
            raw_content = self._generate_page(page_slug, biz_ctx, {}, comp_ctx)
        except AIError as exc:
            log.warning("AI unavailable for {s}: {e} — using rule-based defaults",
                        s=page_slug, e=exc)

        meta  = self.generate_meta_tags(page_slug, business)
        heads = self.suggest_headings(page_slug, business)
        body  = self._content_dict_to_html(page_slug, raw_content)

        return PageContent(
            slug             = page_slug,
            title            = raw_content.get("hero", {}).get("heading", "") or heads[0] if heads else "",
            body_html        = body,
            meta_title       = raw_content.get("meta_title", meta["title"]),
            meta_description = raw_content.get("meta_description", meta["description"]),
            headings         = heads,
            structured_data  = self.suggest_structured_data(business),
        )

    def generate_meta_tags(
        self,
        page_slug: str,
        business:  BusinessInfo,
    ) -> dict[str, str]:
        """Produce SEO meta tags for a page (rule-based, no AI call).

        Args:
            page_slug: Target page slug.
            business:  Business profile.

        Returns:
            Dict with keys ``"title"`` and ``"description"``.
        """
        name     = business.name     or "[Company Name]"
        location = business.location or ""
        industry = business.industry or ""
        loc      = f" in {location}" if location else ""

        templates: dict[str, dict[str, str]] = {
            "homepage": {
                "title":       f"{name} – {industry}{loc}",
                "description": (
                    f"Professional {industry} services by {name}{loc}. "
                    + (business.unique_value or "Contact us for a free consultation.")
                ),
            },
            "about": {
                "title":       f"About {name} – {industry}{loc}",
                "description": (
                    f"Learn more about {name}, your trusted {industry} "
                    f"partner{loc}."
                ),
            },
            "services": {
                "title":       f"Services – {name} | {industry}",
                "description": (
                    f"Explore all {industry} services from {name}{loc}. "
                    + (", ".join(business.services[:3]) if business.services else "")
                ),
            },
            "contact": {
                "title":       f"Contact {name} – {industry}{loc}",
                "description": (
                    f"Get in touch with {name}{loc}. "
                    "Free consultation available — we respond quickly."
                ),
            },
            "faq": {
                "title":       f"FAQ – {name} | {industry}",
                "description": (
                    f"Answers to common questions about {name}'s {industry} services."
                ),
            },
        }

        result = templates.get(page_slug, {
            "title":       f"{name} – {page_slug.replace('-', ' ').title()}",
            "description": f"{name}: professional {industry} services{loc}.",
        })

        # Enforce meta limits
        if len(result["title"]) > 60:
            result["title"] = result["title"][:57] + "..."
        if len(result["description"]) > 160:
            result["description"] = result["description"][:157] + "..."

        return result

    def suggest_headings(
        self,
        page_slug: str,
        business:  BusinessInfo,
    ) -> list[str]:
        """Suggest an H1 → H3 heading hierarchy for *page_slug*.

        Rule-based; no AI call required.

        Args:
            page_slug: Target page slug.
            business:  Business profile.

        Returns:
            Ordered list of heading strings (H1 first).
        """
        name     = business.name     or "[Company Name]"
        location = business.location or ""
        industry = business.industry or ""
        loc      = f" in {location}" if location else ""
        svcs     = business.services[:5] if business.services else []

        templates: dict[str, list[str]] = {
            "homepage": [
                f"{name} – {industry}{loc}",
                f"Our {industry} Services",
                "Why Choose Us",
                "Request a Free Quote",
            ],
            "about": [
                f"About {name}",
                "Our Story",
                "Our Values",
                "Meet Our Team",
            ],
            "services": (
                [f"Our {industry} Services"]
                + [f"  {s}" for s in svcs]
                + ["Get in Touch"]
            ),
            "contact": [
                f"Contact {name}",
                "Get in Touch Today",
                "Our Contact Details",
            ],
            "faq": [
                f"Frequently Asked Questions – {name}",
                f"Questions About Our {industry} Services",
                "Still Have Questions?",
            ],
        }

        return templates.get(
            page_slug,
            [f"{name} – {page_slug.replace('-', ' ').title()}"]
        )

    def score_readability(self, text: str) -> float:
        """Compute a readability score for *text* (0.0 – 1.0).

        Uses sentence length, word length, and AI buzzword presence.
        Higher scores indicate more human-readable content.

        Args:
            text: Plain-text content to evaluate.

        Returns:
            Score where 1.0 = highly readable.
        """
        text = text.strip()
        if not text:
            return 0.0

        words = text.split()
        if not words:
            return 0.0

        sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
        n_sents   = max(len(sentences), 1)
        n_words   = len(words)

        avg_sent  = n_words / n_sents
        avg_word  = sum(len(w.strip(".,;:!?\"'")) for w in words) / n_words

        # Sentence length: 12-22 words is ideal
        sent_score = 1.0 - min(abs(avg_sent - 17) / 25.0, 1.0)
        # Word length: 4-5 chars is ideal
        word_score = 1.0 - min(max(avg_word - 5.0, 0.0) / 4.0, 1.0)

        # Buzzword penalty (-0.05 per buzzword, max -0.30)
        lower = text.lower()
        buzz  = sum(1 for w in _AI_BUZZWORDS if w in lower)
        buzz_penalty = min(buzz * 0.05, 0.30)

        # Repetition penalty: detect repeated consecutive sentences
        rep_penalty = 0.0
        for i in range(len(sentences) - 1):
            a = sentences[i].strip().lower()
            b = sentences[i + 1].strip().lower()
            if a and b and (a[:40] == b[:40]):
                rep_penalty += 0.10

        score = (sent_score * 0.5 + word_score * 0.5) - buzz_penalty - rep_penalty
        return max(0.0, min(1.0, round(score, 3)))

    def suggest_structured_data(self, business: BusinessInfo) -> dict:
        """Generate a JSON-LD LocalBusiness schema for *business*.

        Args:
            business: Business profile.

        Returns:
            Dict ready for JSON serialisation as a ``<script>`` tag.
        """
        name     = business.name     or "[Company Name]"
        location = business.location or ""
        industry = business.industry or ""

        data: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type":    "LocalBusiness",
            "name":     name,
        }

        if industry:
            data["description"] = (
                f"{industry} services"
                + (f" in {location}" if location else "")
            )

        if business.contact_phone:
            data["telephone"] = business.contact_phone
        if business.contact_email:
            data["email"] = business.contact_email
        if location:
            data["address"] = {
                "@type":           "PostalAddress",
                "addressLocality": location,
                "addressCountry":  "DE",
            }

        if business.services:
            data["hasOfferCatalog"] = {
                "@type": "OfferCatalog",
                "name":  "Services",
                "itemListElement": [
                    {
                        "@type":       "Offer",
                        "itemOffered": {"@type": "Service", "name": s},
                    }
                    for s in business.services[:10]
                ],
            }

        return data

    def review_content(
        self,
        content:    dict,
        page_slug:  str,
        biz_ctx:    _BusinessContext | None = None,
    ) -> _ContentReview:
        """Ask DeepSeek to review *content* for a named page.

        Calls DeepSeek via AIRouter.  On failure or missing provider, returns
        a ``_ContentReview`` with ``review_skipped=True``.

        Args:
            content:   Generated content dict for one page.
            page_slug: Page identifier for logging and the review record.
            biz_ctx:   Client context for factual consistency check.

        Returns:
            _ContentReview with DeepSeek's findings.
        """
        return self._review_page(page_slug, content, biz_ctx or _BusinessContext())

    # ── Private: AI generation ─────────────────────────────────────────────────

    def _generate_page(
        self,
        slug:      str,
        biz_ctx:   _BusinessContext,
        page_srcs: dict[str, list[_PageSource]],
        comp_ctx:  _CompetitorContext,
    ) -> dict:
        """Build a Claude prompt, call Claude, and return the parsed dict.

        Args:
            slug:      Page slug to generate.
            biz_ctx:   Client business context.
            page_srcs: Existing crawled pages grouped by slug.
            comp_ctx:  Competitor insights.

        Returns:
            Parsed content dict from Claude.

        Raises:
            AIError: If Claude is unavailable or returns an unusable response.
        """
        prompt = self._build_generation_prompt(slug, biz_ctx, page_srcs, comp_ctx)
        raw, provider = self._call_claude(prompt, page_slug=slug)

        log.info("Claude response received for {s} via {p}", s=slug, p=provider)

        content = self._parse_content_json(raw)
        if not content:
            raise AIError(
                f"Claude returned empty or unparseable content for {slug!r}",
            )
        return content

    def _review_page(
        self,
        slug:    str,
        content: dict,
        biz_ctx: _BusinessContext,
    ) -> _ContentReview:
        """Ask Claude to review *content* and return structured feedback.

        Args:
            slug:    Page slug (for logging).
            content: Generated content dict.
            biz_ctx: Client context for factual check.

        Returns:
            _ContentReview (review_skipped=True if Claude is unavailable).
        """
        if not self._ai_router.is_available(AIProvider.CLAUDE):
            log.debug("Claude not configured — skipping review for {s}", s=slug)
            return _ContentReview(
                page_slug      = slug,
                review_skipped = True,
                skip_reason    = "Claude API key not configured",
            )

        prompt = self._build_review_prompt(slug, content, biz_ctx)
        try:
            raw, provider = self._call_claude(prompt, page_slug="review")
            log.info("Claude review received for {s} via {p}", s=slug, p=provider)
            review_data = self._parse_content_json(raw)
            return self._dict_to_review(slug, review_data)
        except AIError as exc:
            log.warning("Claude review failed for {s}: {e}", s=slug, e=exc)
            return _ContentReview(
                page_slug      = slug,
                review_skipped = True,
                skip_reason    = str(exc),
            )

    # ── Private: prompt builders ───────────────────────────────────────────────

    def _build_generation_prompt(
        self,
        slug:      str,
        biz_ctx:   _BusinessContext,
        page_srcs: dict[str, list[_PageSource]],
        comp_ctx:  _CompetitorContext,
    ) -> str:
        """Assemble the Claude generation prompt for *slug*.

        Args:
            slug:      Page slug.
            biz_ctx:   Client facts.
            page_srcs: Existing crawled pages grouped by slug.
            comp_ctx:  Competitor insights.

        Returns:
            Assembled prompt string.
        """
        lines: list[str] = [
            f"Generate professional website content for the {slug.upper()} page.",
            "",
            "=== THINK FIRST (then write JSON) ===",
            "Design a modern local-business site that is clearly BETTER than a cluttered",
            "legacy brochure site: clean IA, strong SEO, visual hierarchy, trust, conversion.",
            "",
            "=== STRICT RULES (follow exactly) ===",
            "1. Preserve ALL factual information provided below.",
            "2. NEVER invent: services, certifications, experience, awards, "
            "pricing, locations, guarantees, statistics, or customer reviews.",
            "3. NEVER fabricate company history or team details.",
            "4. Use [MISSING INFORMATION] as a placeholder for unknown facts.",
            "5. Write in a professional, natural, human tone.",
            "6. Match the language of the existing website content (typically German).",
            "7. Avoid AI-sounding phrases, keyword stuffing, and exaggerated language.",
            "8. Keep primary navigation simple: only Home, Über uns, Leistungen, Kontakt, FAQ "
            "(do not invent extra top-nav items).",
            "9. Plan concrete image placements (hero, service cards, trust strip) using "
            "placeholders like [IMAGE: hero team/truck] — WordPress will map real media later.",
            "10. SEO: one clear H1, logical H2s, natural local keywords, helpful meta title/"
            "description fields when present in the schema.",
            "",
        ]

        # Client business facts
        lines += [
            "=== CLIENT BUSINESS FACTS ===",
            f"Company       : {biz_ctx.company_name or '[MISSING INFORMATION]'}",
            f"Industry      : {biz_ctx.industry or '[MISSING INFORMATION]'}",
            f"Main services : {', '.join(biz_ctx.main_services) or '[MISSING INFORMATION]'}",
        ]
        if biz_ctx.secondary_services:
            lines.append(
                f"Other services: {', '.join(biz_ctx.secondary_services)}"
            )
        lines += [
            f"Service areas : {', '.join(biz_ctx.service_areas) or '[MISSING INFORMATION]'}",
            f"Target clients: {biz_ctx.target_customers or '[MISSING INFORMATION]'}",
            f"Brand tone    : {biz_ctx.brand_tone or 'professional'}",
            f"Unique value  : {biz_ctx.unique_value or '[MISSING INFORMATION]'}",
        ]
        if biz_ctx.trust_signals:
            lines.append(f"Trust signals : {', '.join(biz_ctx.trust_signals[:5])}")
        if biz_ctx.contact_phone:
            lines.append(f"Phone         : {biz_ctx.contact_phone}")
        if biz_ctx.contact_email:
            lines.append(f"Email         : {biz_ctx.contact_email}")
        if biz_ctx.cta_strategy:
            lines.append(f"CTA strategy  : {biz_ctx.cta_strategy}")
        if biz_ctx.faq_topics:
            lines.append(
                f"FAQ topics    : {', '.join(biz_ctx.faq_topics[:8])}"
            )
        lines.append("")

        # Existing website content for this slug
        existing_pages = page_srcs.get(slug, [])
        if existing_pages:
            lines.append("=== EXISTING WEBSITE CONTENT (preserve factual details) ===")
            chars = 0
            for page in existing_pages[:3]:
                if chars >= _MAX_EXISTING_CHARS:
                    break
                if page.title:
                    lines.append(f"Page title: {page.title}")
                if page.headings:
                    lines.append(f"Headings: {' | '.join(page.headings[:5])}")
                excerpt = page.text_content[:800].rstrip()
                if excerpt:
                    lines.append(f"Text excerpt:\n{excerpt}")
                    chars += len(excerpt)
                lines.append("")

        # Competitor inspiration
        comp_ideas = self._gather_comp_ideas(slug, comp_ctx)
        if comp_ideas:
            lines += [
                "=== COMPETITOR STRUCTURE STORIES (inspiration only — never copy wording) ===",
            ]
            for story in (comp_ctx.structure_stories or [])[:12]:
                lines.append(f"  {story}")
            if not comp_ctx.structure_stories and comp_ctx.structure_markdown:
                lines.append(comp_ctx.structure_markdown[:4000])
            lines.append("")
            lines += [
                "=== COMPETITOR INSPIRATION (structural IDEAS only — never copy wording) ===",
            ]
            for idea in comp_ideas[:_MAX_COMPETITOR_ITEMS]:
                lines.append(f"  - {idea}")
            lines.append("")

        # Response schema
        schema = _PAGE_SCHEMAS.get(slug, {"content": "page content"})
        lines += [
            "=== REQUIRED JSON RESPONSE ===",
            "Respond ONLY with the following JSON structure.",
            "Use empty string for unknown optional fields.",
            json.dumps(schema, indent=2, ensure_ascii=False),
        ]

        return "\n".join(lines)

    def _build_review_prompt(
        self,
        slug:    str,
        content: dict,
        biz_ctx: _BusinessContext,
    ) -> str:
        """Assemble the DeepSeek review prompt for *content*.

        Args:
            slug:    Page slug.
            content: Generated content dict from Claude.
            biz_ctx: Client context for factual cross-check.

        Returns:
            Assembled review prompt string.
        """
        lines: list[str] = [
            f"Review this generated website content for the {slug.upper()} page.",
            "Do NOT rewrite the content. Only provide review comments.",
            "",
            "=== BUSINESS FACTS (for factual consistency check) ===",
            f"Company   : {biz_ctx.company_name or '(not specified)'}",
            f"Industry  : {biz_ctx.industry or '(not specified)'}",
            f"Services  : {', '.join(biz_ctx.main_services) or '(not specified)'}",
            f"Areas     : {', '.join(biz_ctx.service_areas) or '(not specified)'}",
            "",
            "=== GENERATED CONTENT TO REVIEW ===",
            json.dumps(content, indent=2, ensure_ascii=False),
            "",
            "=== REVIEW TASKS ===",
            "1. factual_consistency: Does it match the business facts above?",
            "2. invented_information: List any invented facts not from the profile.",
            "3. missing_information: List [MISSING INFORMATION] tags or clear gaps.",
            "4. readability: Is it easy to read? Natural or AI-sounding?",
            "5. ai_sounding_phrases: List phrases that sound unnatural.",
            "6. repetition_issues: Identify repeated phrases or ideas.",
            "7. logical_flow: Does the content flow naturally?",
            "8. clarity: Are services and value clearly communicated?",
            "9. overall_rating: excellent | good | adequate | needs_revision",
            "10. suggestions: Specific actionable improvements.",
            "",
            "=== REQUIRED JSON RESPONSE ===",
            json.dumps(_REVIEW_SCHEMA, indent=2, ensure_ascii=False),
        ]
        return "\n".join(lines)

    # ── Private: AI calls ──────────────────────────────────────────────────────

    def _call_claude(self, prompt: str, *, page_slug: str = "") -> tuple[str, str]:
        """Send *prompt* to Claude via AIRouter.

        Args:
            prompt: Assembled generation prompt.
            page_slug: Optional page slug used to select ``prompts/<slug>.md``.

        Returns:
            Tuple of (response_text, provider_name).

        Raises:
            AIError: If Claude is unavailable or the call fails.
        """
        if not self._ai_router.is_available(AIProvider.CLAUDE):
            raise AIError(
                "Claude API key not configured. "
                "Set CLAUDE_API_KEY in .env to enable content generation.",
            )

        log.info("Claude request: {n:,} chars", n=len(prompt))
        try:
            if page_slug == "review":
                system = load_prompt_or_default("review", _FALLBACK_REVIEW_SYSTEM)
            else:
                system = load_prompt_or_default("content", _FALLBACK_GENERATION_SYSTEM)
                if page_slug:
                    system = load_prompt_or_default(page_slug, system)
            response = self._ai_router.complete(
                prompt,
                system   = system,
                provider = AIProvider.CLAUDE,
            )
        except NotImplementedError:
            raise AIError(
                "AIRouter.complete() is not yet implemented. "
                "Implement ai_router.py to enable Claude content generation.",
            )
        except Exception as exc:
            raise AIError(f"Claude call failed: {exc}") from exc

        if not response or not response.strip():
            raise AIError("Claude returned an empty response")

        return response, "claude"

    def _call_deepseek(self, prompt: str) -> tuple[str, str]:
        """Send *prompt* to DeepSeek via AIRouter.

        Args:
            prompt: Assembled review prompt.

        Returns:
            Tuple of (response_text, provider_name).

        Raises:
            AIError: If DeepSeek is unavailable or the call fails.
        """
        if not self._ai_router.is_available(AIProvider.DEEPSEEK):
            raise AIError("DeepSeek API key not configured.")

        log.info("DeepSeek review request: {n:,} chars", n=len(prompt))
        try:
            response = self._ai_router.complete(
                prompt,
                system   = load_prompt_or_default("review", _FALLBACK_REVIEW_SYSTEM),
                provider = AIProvider.DEEPSEEK,
            )
        except NotImplementedError:
            raise AIError(
                "AIRouter.complete() is not yet implemented. "
                "Implement ai_router.py to enable DeepSeek review.",
            )
        except Exception as exc:
            raise AIError(f"DeepSeek call failed: {exc}") from exc

        if not response or not response.strip():
            raise AIError("DeepSeek returned an empty response")

        return response, "deepseek"

    # ── Private: response parsing ──────────────────────────────────────────────

    def _parse_content_json(self, response: str) -> dict:
        """Extract and parse the JSON object from an AI response.

        Handles raw JSON, code-fenced blocks, and JSON embedded in prose.

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
            log.debug("Response excerpt: {r}", r=response[:200])
            return {}

        if not isinstance(result, dict):
            log.warning("AI returned non-dict: {t}", t=type(result).__name__)
            return {}

        return result

    def _dict_to_review(self, slug: str, data: dict) -> _ContentReview:
        """Build a _ContentReview from a raw AI-parsed dict.

        Args:
            slug: Page slug.
            data: AI-parsed review dict.

        Returns:
            Validated _ContentReview.
        """

        def _as_list(val: Any) -> list[str]:
            if val is None:
                return []
            if isinstance(val, list):
                return [str(v).strip() for v in val if str(v).strip()]
            if isinstance(val, str) and val.strip():
                return [val.strip()]
            return []

        return _ContentReview(
            page_slug            = slug,
            factual_consistency  = str(data.get("factual_consistency", "")),
            invented_information = _as_list(data.get("invented_information")),
            missing_information  = _as_list(data.get("missing_information")),
            readability          = str(data.get("readability", "")),
            ai_sounding_phrases  = _as_list(data.get("ai_sounding_phrases")),
            repetition_issues    = _as_list(data.get("repetition_issues")),
            logical_flow         = str(data.get("logical_flow", "")),
            clarity              = str(data.get("clarity", "")),
            overall_rating       = str(data.get("overall_rating", "")),
            suggestions          = _as_list(data.get("suggestions")),
        )

    # ── Private: content helpers ───────────────────────────────────────────────

    def _gather_comp_ideas(
        self,
        slug:     str,
        comp_ctx: _CompetitorContext,
    ) -> list[str]:
        """Pick the most relevant competitor ideas for *slug*.

        Args:
            slug:     Page slug.
            comp_ctx: Competitor insights.

        Returns:
            Flat list of idea strings.
        """
        mapping: dict[str, list[str]] = {
            "homepage": comp_ctx.homepage_structure_ideas + comp_ctx.overall_opportunities,
            "about":    comp_ctx.trust_building_elements,
            "services": comp_ctx.service_presentation_ideas,
            "contact":  comp_ctx.customer_journey_ideas + comp_ctx.cta_ideas,
            "faq":      comp_ctx.faq_ideas,
        }
        ideas = mapping.get(slug, comp_ctx.overall_opportunities)
        ideas = list(comp_ctx.structure_stories[:8]) + list(ideas)
        # Deduplicate while preserving order
        seen:   set[str] = set()
        result: list[str] = []
        for item in ideas:
            key = item.lower().strip()
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result

    def _content_dict_to_html(self, slug: str, content: dict) -> str:
        """Convert a structured content dict to minimal HTML body.

        This is a simple conversion for PageContent.body_html — the actual
        rendering is handled by WordPressGenerator.

        Args:
            slug:    Page slug.
            content: Parsed content dict from Claude.

        Returns:
            Basic HTML string.
        """
        parts: list[str] = []

        # Hero
        hero = content.get("hero", {})
        if isinstance(hero, dict):
            if hero.get("heading"):
                parts.append(f"<h1>{hero['heading']}</h1>")
            if hero.get("subheading"):
                parts.append(f"<p>{hero['subheading']}</p>")

        # Intro / company_story
        for key in ("intro", "company_story"):
            val = content.get(key, "")
            if val and isinstance(val, str):
                parts.append(f"<p>{val}</p>")

        # Services overview
        svc_overview = content.get("services_overview", {})
        if isinstance(svc_overview, dict):
            if svc_overview.get("heading"):
                parts.append(f"<h2>{svc_overview['heading']}</h2>")
            for svc in svc_overview.get("services", [])[:10]:
                if isinstance(svc, dict):
                    parts.append(f"<h3>{svc.get('name', '')}</h3>")
                    parts.append(f"<p>{svc.get('short_description', '')}</p>")

        # Services list (for services page)
        for svc in content.get("services", [])[:20]:
            if isinstance(svc, dict):
                parts.append(f"<h2>{svc.get('heading', svc.get('name', ''))}</h2>")
                parts.append(f"<p>{svc.get('description', '')}</p>")

        # Why choose us
        wcu = content.get("why_choose_us", {})
        if isinstance(wcu, dict):
            if wcu.get("heading"):
                parts.append(f"<h2>{wcu['heading']}</h2>")
            for pt in wcu.get("points", []):
                if isinstance(pt, dict):
                    parts.append(f"<h3>{pt.get('heading', '')}</h3>")
                    parts.append(f"<p>{pt.get('text', '')}</p>")

        # FAQ
        for faq_item in content.get("faqs", []):
            if isinstance(faq_item, dict):
                parts.append(f"<h3>{faq_item.get('question', '')}</h3>")
                parts.append(f"<p>{faq_item.get('answer', '')}</p>")

        # CTA
        cta = content.get("cta_section", {})
        if isinstance(cta, dict) and cta.get("heading"):
            parts.append(f"<h2>{cta['heading']}</h2>")
            if cta.get("text"):
                parts.append(f"<p>{cta['text']}</p>")

        return "\n".join(p for p in parts if p.strip())

    def _business_info_to_context(self, business: BusinessInfo) -> _BusinessContext:
        """Convert a shared BusinessInfo into a _BusinessContext.

        Args:
            business: Shared type from core.types.

        Returns:
            Module-local _BusinessContext.
        """
        return _BusinessContext(
            company_name    = business.name,
            industry        = business.industry,
            main_services   = business.services,
            target_customers = business.target_audience,
            brand_tone      = business.tone_of_voice,
            unique_value    = business.unique_value,
            contact_email   = business.contact_email,
            contact_phone   = business.contact_phone,
        )

    # ── Private: file I/O ──────────────────────────────────────────────────────

    def _load_business_context(self, project_dir: Path) -> _BusinessContext | None:
        """Load business_profile.json from *project_dir*.

        Args:
            project_dir: Client's project directory.

        Returns:
            _BusinessContext, or None if the file is absent/unreadable.
        """
        path = project_dir / "json" / "business_profile.json"
        data = self._load_json(path, default=None)
        if not data or not isinstance(data, dict):
            return None

        return _BusinessContext(
            company_name       = data.get("company_name", ""),
            industry           = data.get("industry", ""),
            main_services      = data.get("main_services", []),
            secondary_services = data.get("secondary_services", []),
            target_customers   = data.get("target_customers", ""),
            service_areas      = data.get("service_areas", []),
            brand_tone         = data.get("brand_tone", ""),
            unique_value       = data.get("unique_value", ""),
            trust_signals      = data.get("trust_signals", []),
            contact_email      = data.get("contact_email", ""),
            contact_phone      = data.get("contact_phone", ""),
            contact_address    = data.get("contact_address", ""),
            source_url         = data.get("source_url", ""),
            languages          = data.get("languages", []),
            faq_topics         = data.get("faq_topics", []),
            strengths          = data.get("strengths", []),
            weaknesses         = data.get("weaknesses", []),
            cta_strategy       = data.get("cta_strategy", ""),
        )

    def _load_competitor_context(self, project_dir: Path) -> _CompetitorContext:
        """Load comparison_report.json + competitor_structure.md stories."""
        path = project_dir / "json" / "comparison_report.json"
        data = self._load_json(path, default={})
        if not isinstance(data, dict):
            data = {}

        def _lst(key: str) -> list[str]:
            v = data.get(key, [])
            if isinstance(v, list):
                return [str(x).strip() for x in v if str(x).strip()]
            return []

        md_text = ""
        md_path = project_dir / "json" / "competitor_structure.md"
        if md_path.is_file():
            try:
                md_text = md_path.read_text(encoding="utf-8")
            except OSError:
                md_text = ""

        stories: list[str] = []
        for line in md_text.splitlines():
            s = line.strip()
            if re.match(r"^\d+\.\s+", s):
                stories.append(s)

        return _CompetitorContext(
            navigation_ideas           = _lst("navigation_ideas"),
            trust_building_elements    = _lst("trust_building_elements"),
            homepage_structure_ideas   = _lst("homepage_structure_ideas"),
            service_presentation_ideas = _lst("service_presentation_ideas"),
            customer_journey_ideas     = _lst("customer_journey_ideas"),
            cta_ideas                  = _lst("cta_ideas"),
            faq_ideas                  = _lst("faq_ideas"),
            overall_opportunities      = _lst("overall_opportunities"),
            structure_stories          = stories[:40],
            structure_markdown         = md_text[:12000],
        )

    def _load_page_sources(
        self,
        project_dir: Path,
    ) -> dict[str, list[_PageSource]]:
        """Load crawled page content from pages.json, grouped by slug.

        Args:
            project_dir: Client's project directory.

        Returns:
            Dict mapping page slug → list of _PageSource.
        """
        path  = project_dir / "json" / "pages.json"
        pages = self._load_json(path, default=[])
        if not isinstance(pages, list):
            return {}

        grouped: dict[str, list[_PageSource]] = {}
        for page in pages:
            if not isinstance(page, dict):
                continue
            raw_type = page.get("page_type", "unknown")
            if hasattr(raw_type, "value"):
                raw_type = raw_type.value
            slug = _PAGE_TYPE_MAP.get(str(raw_type), str(raw_type))
            src  = _PageSource(
                url          = page.get("url", ""),
                page_type    = slug,
                title        = page.get("title", ""),
                description  = page.get("description", ""),
                headings     = page.get("headings", []),
                text_content = page.get("text_content", ""),
            )
            grouped.setdefault(slug, []).append(src)

        return grouped

    def _save_outputs(
        self,
        project_dir: Path,
        generated:   dict[str, dict],
        reviews:     dict[str, dict],
        meta_pages:  dict[str, dict],
    ) -> None:
        """Write all JSON output files to *project_dir*.

        Files written:
        - ``json/optimized_<slug>.json``  — per-page content
        - ``json/meta_data.json``          — all meta tags
        - ``json/content_review.json``     — all DeepSeek reviews

        Args:
            project_dir: Client's project directory.
            generated:   Slug → content dict.
            reviews:     Slug → review dict.
            meta_pages:  Slug → meta tag dict.
        """
        json_dir = project_dir / "json"
        json_dir.mkdir(parents=True, exist_ok=True)

        for slug, content in generated.items():
            out = json_dir / f"optimized_{slug}.json"
            self._write_json(out, content)
            log.info("Saved: {f}", f=out.name)

        if meta_pages:
            # Merge so page-by-page runs keep other pages' meta
            existing_meta = self._load_json(json_dir / "meta_data.json", default={})
            if not isinstance(existing_meta, dict):
                existing_meta = {}
            existing_meta.update(meta_pages)
            self._write_json(json_dir / "meta_data.json", existing_meta)
            log.info("Saved: meta_data.json")

        if reviews:
            existing_review = self._load_json(
                json_dir / "content_review.json", default={}
            )
            existing_pages: dict = {}
            if isinstance(existing_review, dict):
                prev = existing_review.get("pages")
                if isinstance(prev, dict):
                    existing_pages = dict(prev)
            existing_pages.update(reviews)
            review_output = {
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "reviewer":    "deepseek",
                "pages":       existing_pages,
            }
            self._write_json(json_dir / "content_review.json", review_output)
            log.info("Saved: content_review.json")

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


# ── Supporting data model (from Phase 2 architecture) ────────────────────────

from pydantic import BaseModel, Field  # noqa: E402  (re-import for clarity)


class PageContent(BaseModel):
    """Content payload for a single WordPress page."""

    slug:             str
    title:            str       = ""
    body_html:        str       = ""
    meta_title:       str       = ""
    meta_description: str       = ""
    headings:         list[str] = Field(default_factory=list)
    structured_data:  dict      = Field(default_factory=dict)
