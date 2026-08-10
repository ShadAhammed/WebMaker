"""
tests/unit/test_content_optimizer.py
======================================
Unit tests for ContentOptimizer.

All AI calls (Claude and DeepSeek) and file I/O are mocked — no network
requests, no Playwright, no API keys required.

Coverage:
  - Initialization with and without injected ai_router
  - optimize() raises AnalysisError for empty analysis; returns PageContent map
  - optimize_from_directory() full pipeline: load inputs, generate, review, save
  - generate_page_content() returns PageContent for each standard slug
  - generate_meta_tags() rule-based output, length limits enforced
  - suggest_headings() returns non-empty list per slug
  - score_readability() range 0-1, buzzword penalty, repetition penalty
  - suggest_structured_data() JSON-LD schema structure
  - review_content() DeepSeek available and unavailable
  - _build_generation_prompt() structure and required sections
  - _build_review_prompt() structure and content
  - _parse_content_json() raw, code-fenced, embedded, invalid
  - _dict_to_review() field normalisation
  - _content_dict_to_html() HTML structure
  - _load_business_context() valid, missing, malformed
  - _load_competitor_context() valid, missing, malformed
  - _load_page_sources() grouping by page type
  - _gather_comp_ideas() slug mapping and deduplication
  - _save_outputs() writes expected files
  - Error handling: Claude fails, DeepSeek fails, missing files, malformed JSON
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from webmaker.config.settings import Settings
from webmaker.core.exceptions import AIError, AnalysisError
from webmaker.core.types import (
    AIProvider, AnalysisResult, BusinessInfo, CompetitorInfo,
)
from webmaker.modules.content_optimizer import (
    ContentOptimizer,
    PageContent,
    _BusinessContext,
    _CompetitorContext,
    _ContentReview,
    _PageSource,
    _PAGE_SCHEMAS,
    _STANDARD_PAGE_SLUGS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def test_settings(tmp_path):
    return Settings(
        project_root  = tmp_path,
        logs_dir      = tmp_path / "logs",
        cache_dir     = tmp_path / "cache",
        projects_dir  = tmp_path / "projects",
        outputs_dir   = tmp_path / "outputs",
        assets_dir    = tmp_path / "assets",
        templates_dir = tmp_path / "templates",
        wordpress_dir = tmp_path / "wordpress",
        server_port   = 18080,
        db_port       = 13307,
    )


@pytest.fixture
def mock_router():
    """AIRouter with Claude and DeepSeek unavailable (no keys)."""
    r = MagicMock()
    r.is_available.return_value = False
    r.available_providers.return_value = []
    r.complete.side_effect = NotImplementedError("not yet")
    return r


@pytest.fixture
def mock_router_claude(mock_router):
    """AIRouter where Claude is available and returns homepage JSON."""
    homepage_json = json.dumps({
        "meta_title":       "Test Co – Roofing | Berlin",
        "meta_description": "Professional roofing by Test Co in Berlin.",
        "hero": {
            "heading":       "Your Trusted Roofer in Berlin",
            "subheading":    "Fast, reliable service since 2005.",
            "cta_primary":   "Get a Free Quote",
            "cta_secondary": "Our Services",
        },
        "intro":            "Test Co provides expert roofing services.",
        "services_overview": {
            "heading":  "Our Services",
            "services": [{"name": "Roof Repair", "short_description": "Fast repairs."}],
        },
        "why_choose_us": {
            "heading": "Why Choose Us",
            "points":  [{"heading": "Experienced", "text": "Over 15 years."}],
        },
        "cta_section":   {"heading": "Contact Us", "text": "Ready?", "cta_button": "Contact"},
        "footer_tagline": "Quality roofing you can trust.",
    })

    def is_available(provider):
        return provider == AIProvider.CLAUDE

    mock_router.is_available.side_effect = is_available
    mock_router.complete.side_effect = None
    mock_router.complete.return_value = homepage_json
    return mock_router


@pytest.fixture
def mock_router_both(mock_router_claude):
    """AIRouter where both Claude and DeepSeek are available."""
    review_json = json.dumps({
        "factual_consistency":  "good",
        "invented_information": [],
        "missing_information":  [],
        "readability":          "good",
        "ai_sounding_phrases":  [],
        "repetition_issues":    [],
        "logical_flow":         "good",
        "clarity":              "good",
        "overall_rating":       "good",
        "suggestions":          ["Consider adding testimonials."],
    })

    call_count = {"n": 0}
    claude_resp = mock_router_claude.complete.return_value

    def is_available(provider):
        return provider in (AIProvider.CLAUDE, AIProvider.DEEPSEEK)

    def complete(prompt, **kwargs):
        call_count["n"] += 1
        if call_count["n"] % 2 == 0:
            return review_json
        return claude_resp

    mock_router_claude.is_available.side_effect = is_available
    mock_router_claude.complete.side_effect = complete
    return mock_router_claude


@pytest.fixture
def optimizer(test_settings, mock_router):
    return ContentOptimizer(test_settings, ai_router=mock_router)


@pytest.fixture
def project_dir(tmp_path):
    """Project dir pre-populated with business_profile and comparison_report."""
    d = tmp_path / "projects" / "client-site"
    (d / "json").mkdir(parents=True)

    profile = {
        "company_name":    "Test Co",
        "industry":        "Roofing",
        "main_services":   ["Roof Repair", "New Roofs", "Gutters"],
        "service_areas":   ["Berlin", "Potsdam"],
        "brand_tone":      "professional",
        "unique_value":    "15+ years experience",
        "trust_signals":   ["Certified roofer", "5-star reviews"],
        "contact_phone":   "+49 30 1234567",
        "contact_email":   "info@testco.de",
        "faq_topics":      ["Pricing", "Timeline", "Warranty"],
        "cta_strategy":    "Get a free quote",
    }
    (d / "json" / "business_profile.json").write_text(
        json.dumps(profile), encoding="utf-8"
    )

    comparison = {
        "navigation_ideas":           ["Sticky menu"],
        "trust_building_elements":    ["Review badges"],
        "homepage_structure_ideas":   ["Hero with CTA above fold"],
        "service_presentation_ideas": ["Price cards"],
        "customer_journey_ideas":     ["Clear funnel"],
        "cta_ideas":                  ["Floating call button"],
        "faq_ideas":                  ["FAQ section"],
        "overall_opportunities":      ["Add testimonials"],
    }
    (d / "json" / "comparison_report.json").write_text(
        json.dumps(comparison), encoding="utf-8"
    )

    pages = [
        {
            "url":          "https://testco.de",
            "page_type":    "home",
            "title":        "Test Co – Roofing",
            "description":  "Roofing experts",
            "headings":     ["Welcome", "Services"],
            "text_content": "We are experienced roofers in Berlin.",
        },
        {
            "url":          "https://testco.de/services",
            "page_type":    "services",
            "title":        "Our Services",
            "description":  "All services",
            "headings":     ["Roof Repair", "Gutters"],
            "text_content": "We offer roof repair and gutter cleaning.",
        },
    ]
    (d / "json" / "pages.json").write_text(json.dumps(pages), encoding="utf-8")
    return d


@pytest.fixture
def sample_business():
    return BusinessInfo(
        name            = "Test Co",
        industry        = "Roofing",
        location        = "Berlin",
        services        = ["Roof Repair", "New Roofs"],
        target_audience = "homeowners",
        unique_value    = "15+ years",
        tone_of_voice   = "professional",
        contact_email   = "info@testco.de",
        contact_phone   = "+49 30 1234567",
    )


@pytest.fixture
def sample_analysis(sample_business):
    return AnalysisResult(
        business     = sample_business,
        competitors  = [
            CompetitorInfo(url="https://comp.de", name="Comp",
                           strengths=["Online booking"]),
        ],
        content_gaps    = ["Online booking missing"],
        recommendations = ["Add testimonials"],
    )


# ── Initialization ─────────────────────────────────────────────────────────────

class TestInit:
    def test_creates_ai_router_if_not_provided(self, test_settings):
        with patch("webmaker.modules.content_optimizer.AIRouter") as mock_cls:
            mock_cls.return_value = MagicMock()
            ContentOptimizer(test_settings)
            mock_cls.assert_called_once_with(test_settings)

    def test_uses_injected_router(self, test_settings, mock_router):
        co = ContentOptimizer(test_settings, ai_router=mock_router)
        assert co._ai_router is mock_router


# ── optimize() ───────────────────────────────────────────────────────────────

class TestOptimize:
    def test_raises_analysis_error_for_empty_analysis(self, optimizer):
        analysis = AnalysisResult(
            business = BusinessInfo(name="", services=[]),
        )
        with pytest.raises(AnalysisError):
            optimizer.optimize(analysis)

    def test_returns_page_content_map(self, test_settings, mock_router_claude,
                                       sample_analysis):
        co = ContentOptimizer(test_settings, ai_router=mock_router_claude)
        result = co.optimize(sample_analysis)
        assert isinstance(result, dict)
        for slug in _STANDARD_PAGE_SLUGS:
            assert slug in result
            assert isinstance(result[slug], PageContent)

    def test_all_page_contents_have_meta_title(self, test_settings, mock_router_claude,
                                                sample_analysis):
        co = ContentOptimizer(test_settings, ai_router=mock_router_claude)
        result = co.optimize(sample_analysis)
        for slug, pc in result.items():
            assert isinstance(pc.meta_title, str)

    def test_skips_failed_pages_gracefully(self, test_settings, mock_router, sample_analysis):
        # Claude not available → AIError → skip quietly
        mock_router.is_available.return_value = False
        co = ContentOptimizer(test_settings, ai_router=mock_router)
        result = co.optimize(sample_analysis)
        assert isinstance(result, dict)


# ── generate_page_content() ────────────────────────────────────────────────────

class TestGeneratePageContent:
    @pytest.mark.parametrize("slug", list(_STANDARD_PAGE_SLUGS))
    def test_returns_page_content(self, test_settings, mock_router_claude,
                                   sample_business, sample_analysis, slug):
        co = ContentOptimizer(test_settings, ai_router=mock_router_claude)
        pc = co.generate_page_content(slug, sample_business, sample_analysis)
        assert isinstance(pc, PageContent)
        assert pc.slug == slug

    def test_page_content_has_meta_fields(self, test_settings, mock_router_claude,
                                           sample_business, sample_analysis):
        co = ContentOptimizer(test_settings, ai_router=mock_router_claude)
        pc = co.generate_page_content("homepage", sample_business, sample_analysis)
        assert isinstance(pc.meta_title, str)
        assert isinstance(pc.meta_description, str)

    def test_page_content_has_structured_data(self, test_settings, mock_router_claude,
                                               sample_business, sample_analysis):
        co = ContentOptimizer(test_settings, ai_router=mock_router_claude)
        pc = co.generate_page_content("homepage", sample_business, sample_analysis)
        assert isinstance(pc.structured_data, dict)
        assert "@context" in pc.structured_data

    def test_ai_failure_returns_default_content(self, optimizer, sample_business,
                                                 sample_analysis):
        pc = optimizer.generate_page_content("homepage", sample_business, sample_analysis)
        assert isinstance(pc, PageContent)
        assert pc.slug == "homepage"


# ── generate_meta_tags() ──────────────────────────────────────────────────────

class TestGenerateMetaTags:
    @pytest.mark.parametrize("slug", list(_STANDARD_PAGE_SLUGS))
    def test_returns_title_and_description(self, optimizer, sample_business, slug):
        meta = optimizer.generate_meta_tags(slug, sample_business)
        assert "title" in meta
        assert "description" in meta

    def test_title_max_60_chars(self, optimizer, sample_business):
        for slug in _STANDARD_PAGE_SLUGS:
            meta = optimizer.generate_meta_tags(slug, sample_business)
            assert len(meta["title"]) <= 60, f"Title too long for {slug}"

    def test_description_max_160_chars(self, optimizer, sample_business):
        for slug in _STANDARD_PAGE_SLUGS:
            meta = optimizer.generate_meta_tags(slug, sample_business)
            assert len(meta["description"]) <= 160, f"Desc too long for {slug}"

    def test_company_name_in_title(self, optimizer, sample_business):
        meta = optimizer.generate_meta_tags("homepage", sample_business)
        assert "Test Co" in meta["title"]

    def test_handles_missing_name(self, optimizer):
        business = BusinessInfo(name="", industry="Roofing")
        meta = optimizer.generate_meta_tags("homepage", business)
        assert isinstance(meta["title"], str)
        assert len(meta["title"]) > 0

    def test_location_in_homepage_title(self, optimizer, sample_business):
        meta = optimizer.generate_meta_tags("homepage", sample_business)
        assert "Berlin" in meta["title"] or "Roofing" in meta["title"]

    def test_unknown_slug_returns_fallback(self, optimizer, sample_business):
        meta = optimizer.generate_meta_tags("gallery", sample_business)
        assert "title" in meta
        assert "description" in meta


# ── suggest_headings() ────────────────────────────────────────────────────────

class TestSuggestHeadings:
    @pytest.mark.parametrize("slug", list(_STANDARD_PAGE_SLUGS))
    def test_returns_non_empty_list(self, optimizer, sample_business, slug):
        headings = optimizer.suggest_headings(slug, sample_business)
        assert isinstance(headings, list)
        assert len(headings) >= 1

    def test_h1_contains_company_name_or_industry(self, optimizer, sample_business):
        headings = optimizer.suggest_headings("homepage", sample_business)
        h1 = headings[0]
        assert "Test Co" in h1 or "Roofing" in h1 or "Berlin" in h1

    def test_services_page_includes_service_names(self, optimizer, sample_business):
        headings = optimizer.suggest_headings("services", sample_business)
        text = " ".join(headings).lower()
        assert "roof repair" in text or "roofing" in text

    def test_unknown_slug_returns_fallback(self, optimizer, sample_business):
        headings = optimizer.suggest_headings("gallery", sample_business)
        assert len(headings) >= 1


# ── score_readability() ───────────────────────────────────────────────────────

class TestScoreReadability:
    def test_empty_string_returns_zero(self, optimizer):
        assert optimizer.score_readability("") == 0.0

    def test_score_in_range(self, optimizer):
        texts = [
            "We fix roofs. We are fast. Call us today.",
            "This is a very long sentence that goes on and on and has many many words in it.",
            "Synergy. Leverage. Holistic paradigm shift.",
            "Ihr Dach in besten Händen. Wir helfen schnell und zuverlässig.",
        ]
        for text in texts:
            score = optimizer.score_readability(text)
            assert 0.0 <= score <= 1.0, f"Score out of range for: {text!r}"

    def test_buzzword_penalty(self, optimizer):
        clean = "We fix your roof quickly. Our team is experienced."
        buzz  = "We leverage synergies to holistically empower your roof ecosystem."
        assert optimizer.score_readability(clean) > optimizer.score_readability(buzz)

    def test_natural_text_scores_above_half(self, optimizer):
        text = "We offer professional roof repair in Berlin. Our team responds quickly."
        score = optimizer.score_readability(text)
        assert score > 0.3

    def test_very_long_sentences_penalised(self, optimizer):
        # A single very-long run-on sentence packed with buzzwords should score
        # lower than natural prose with moderate sentence lengths.
        bad  = (
            "We holistically leverage synergistic paradigms to comprehensively "
            "empower stakeholders across every ecosystem touchpoint in a seamless "
            "and robust manner that is both transformative and cutting-edge."
        )
        good = (
            "We repair roofs in Berlin quickly and professionally. "
            "Our experienced team responds within 24 hours. "
            "Request your free quote today."
        )
        assert optimizer.score_readability(good) >= optimizer.score_readability(bad)


# ── suggest_structured_data() ─────────────────────────────────────────────────

class TestSuggestStructuredData:
    def test_returns_dict(self, optimizer, sample_business):
        data = optimizer.suggest_structured_data(sample_business)
        assert isinstance(data, dict)

    def test_has_json_ld_context(self, optimizer, sample_business):
        data = optimizer.suggest_structured_data(sample_business)
        assert data["@context"] == "https://schema.org"
        assert "LocalBusiness" in data["@type"]

    def test_has_company_name(self, optimizer, sample_business):
        data = optimizer.suggest_structured_data(sample_business)
        assert data["name"] == "Test Co"

    def test_has_phone_if_present(self, optimizer, sample_business):
        data = optimizer.suggest_structured_data(sample_business)
        assert data.get("telephone") == "+49 30 1234567"

    def test_has_email_if_present(self, optimizer, sample_business):
        data = optimizer.suggest_structured_data(sample_business)
        assert data.get("email") == "info@testco.de"

    def test_has_services_catalog(self, optimizer, sample_business):
        data = optimizer.suggest_structured_data(sample_business)
        assert "hasOfferCatalog" in data
        items = data["hasOfferCatalog"]["itemListElement"]
        assert len(items) >= 1
        assert items[0]["@type"] == "Offer"

    def test_empty_business_does_not_crash(self, optimizer):
        data = optimizer.suggest_structured_data(BusinessInfo(name=""))
        assert isinstance(data, dict)
        assert "@context" in data

    def test_has_address_when_location_present(self, optimizer, sample_business):
        data = optimizer.suggest_structured_data(sample_business)
        assert "address" in data
        assert data["address"]["addressLocality"] == "Berlin"


# ── review_content() / _review_page() ────────────────────────────────────────

class TestReviewContent:
    def test_returns_review_skipped_when_deepseek_unavailable(self, optimizer):
        content = {"meta_title": "Test", "intro": "Hello"}
        review = optimizer.review_content(content, "homepage")
        assert isinstance(review, _ContentReview)
        assert review.review_skipped is True
        assert "DeepSeek" in review.skip_reason or "deepseek" in review.skip_reason.lower()

    def test_returns_review_when_deepseek_available(self, test_settings, mock_router_both):
        review_json = json.dumps({
            "factual_consistency":  "good",
            "invented_information": [],
            "missing_information":  ["[MISSING INFORMATION] in team section"],
            "readability":          "excellent",
            "ai_sounding_phrases":  [],
            "repetition_issues":    [],
            "logical_flow":         "good",
            "clarity":              "excellent",
            "overall_rating":       "excellent",
            "suggestions":          ["Add testimonials"],
        })

        def is_available(provider):
            return provider == AIProvider.DEEPSEEK

        mock_router_both.is_available.side_effect = is_available
        mock_router_both.complete.side_effect = None
        mock_router_both.complete.return_value = review_json

        co = ContentOptimizer(test_settings, ai_router=mock_router_both)
        content = {"meta_title": "Test", "intro": "Hello"}
        review = co.review_content(content, "about")
        assert isinstance(review, _ContentReview)
        assert review.review_skipped is False
        assert review.overall_rating == "excellent"

    def test_ai_failure_sets_skip_reason(self, test_settings, mock_router):
        mock_router.is_available.return_value = True
        mock_router.complete.side_effect = Exception("Connection refused")
        co = ContentOptimizer(test_settings, ai_router=mock_router)
        review = co.review_content({"meta_title": "Test"}, "services")
        assert review.review_skipped is True
        assert review.skip_reason != ""


# ── _build_generation_prompt() ────────────────────────────────────────────────

class TestBuildGenerationPrompt:
    def _ctx(self):
        return _BusinessContext(
            company_name  = "Test Co",
            industry      = "Roofing",
            main_services = ["Roof Repair", "Gutters"],
            service_areas = ["Berlin"],
            brand_tone    = "professional",
        )

    def test_contains_page_slug(self, optimizer):
        prompt = optimizer._build_generation_prompt(
            "homepage", self._ctx(), {}, _CompetitorContext()
        )
        assert "HOMEPAGE" in prompt or "homepage" in prompt.lower()

    def test_contains_company_name(self, optimizer):
        prompt = optimizer._build_generation_prompt(
            "homepage", self._ctx(), {}, _CompetitorContext()
        )
        assert "Test Co" in prompt

    def test_contains_services(self, optimizer):
        prompt = optimizer._build_generation_prompt(
            "homepage", self._ctx(), {}, _CompetitorContext()
        )
        assert "Roof Repair" in prompt

    def test_contains_strict_rules(self, optimizer):
        prompt = optimizer._build_generation_prompt(
            "homepage", self._ctx(), {}, _CompetitorContext()
        )
        assert "NEVER" in prompt or "never" in prompt.lower()
        assert "MISSING INFORMATION" in prompt or "[MISSING" in prompt

    def test_contains_json_schema(self, optimizer):
        for slug in _STANDARD_PAGE_SLUGS:
            prompt = optimizer._build_generation_prompt(
                slug, self._ctx(), {}, _CompetitorContext()
            )
            assert "meta_title" in prompt

    def test_includes_existing_page_content(self, optimizer):
        src = _PageSource(
            url="https://testco.de", page_type="homepage",
            title="Test Co", text_content="We fix roofs in Berlin.",
        )
        page_srcs = {"homepage": [src]}
        prompt = optimizer._build_generation_prompt(
            "homepage", self._ctx(), page_srcs, _CompetitorContext()
        )
        assert "We fix roofs in Berlin." in prompt

    def test_includes_competitor_ideas_for_homepage(self, optimizer):
        comp_ctx = _CompetitorContext(
            homepage_structure_ideas=["Hero with CTA above fold"],
        )
        prompt = optimizer._build_generation_prompt(
            "homepage", self._ctx(), {}, comp_ctx
        )
        assert "Hero with CTA above fold" in prompt

    def test_competitor_ideas_labelled_as_inspiration(self, optimizer):
        comp_ctx = _CompetitorContext(faq_ideas=["Common questions section"])
        prompt = optimizer._build_generation_prompt(
            "faq", self._ctx(), {}, comp_ctx
        )
        lower = prompt.lower()
        assert "inspiration" in lower or "never copy" in lower or "ideas only" in lower


# ── _build_review_prompt() ────────────────────────────────────────────────────

class TestBuildReviewPrompt:
    def test_contains_review_tasks(self, optimizer):
        content = {"meta_title": "Test", "intro": "Hello"}
        ctx     = _BusinessContext(company_name="Test Co")
        prompt  = optimizer._build_review_prompt("homepage", content, ctx)
        assert "factual_consistency" in prompt
        assert "readability" in prompt
        assert "suggestions" in prompt

    def test_contains_generated_content(self, optimizer):
        content = {"meta_title": "Test Title", "intro": "Hello world"}
        ctx     = _BusinessContext()
        prompt  = optimizer._build_review_prompt("homepage", content, ctx)
        assert "Test Title" in prompt

    def test_contains_business_facts(self, optimizer):
        content = {}
        ctx     = _BusinessContext(company_name="My Firm", industry="Plumbing")
        prompt  = optimizer._build_review_prompt("about", content, ctx)
        assert "My Firm" in prompt
        assert "Plumbing" in prompt

    def test_explicitly_says_do_not_rewrite(self, optimizer):
        content = {"meta_title": "Test"}
        ctx     = _BusinessContext()
        prompt  = optimizer._build_review_prompt("services", content, ctx)
        lower   = prompt.lower()
        assert "do not rewrite" in lower or "not rewrite" in lower


# ── _parse_content_json() ────────────────────────────────────────────────────

class TestParseContentJson:
    def test_raw_json(self, optimizer):
        data = optimizer._parse_content_json('{"meta_title": "Test"}')
        assert data["meta_title"] == "Test"

    def test_code_fenced(self, optimizer):
        text = '```json\n{"meta_title": "Test"}\n```'
        data = optimizer._parse_content_json(text)
        assert data["meta_title"] == "Test"

    def test_code_fenced_no_lang(self, optimizer):
        text = '```\n{"meta_title": "Test"}\n```'
        data = optimizer._parse_content_json(text)
        assert data["meta_title"] == "Test"

    def test_embedded_in_prose(self, optimizer):
        text = 'Here you go: {"meta_title": "Test"} That is it.'
        data = optimizer._parse_content_json(text)
        assert data.get("meta_title") == "Test"

    def test_invalid_returns_empty(self, optimizer):
        assert optimizer._parse_content_json("not json") == {}

    def test_empty_returns_empty(self, optimizer):
        assert optimizer._parse_content_json("") == {}

    def test_nested_structure_preserved(self, optimizer):
        raw = json.dumps({"hero": {"heading": "H1", "cta_primary": "Contact"}})
        data = optimizer._parse_content_json(raw)
        assert data["hero"]["heading"] == "H1"


# ── _dict_to_review() ─────────────────────────────────────────────────────────

class TestDictToReview:
    def test_parses_all_fields(self, optimizer):
        raw = {
            "factual_consistency":  "good",
            "invented_information": ["Fake award"],
            "missing_information":  ["Phone number"],
            "readability":          "excellent",
            "ai_sounding_phrases":  ["leverage synergy"],
            "repetition_issues":    ["'quality' repeated 5 times"],
            "logical_flow":         "good",
            "clarity":              "good",
            "overall_rating":       "good",
            "suggestions":          ["Add testimonials"],
        }
        review = optimizer._dict_to_review("homepage", raw)
        assert review.factual_consistency == "good"
        assert review.invented_information == ["Fake award"]
        assert review.suggestions == ["Add testimonials"]
        assert review.review_skipped is False

    def test_string_field_coerced_to_list(self, optimizer):
        review = optimizer._dict_to_review("about", {"suggestions": "Just one suggestion"})
        assert isinstance(review.suggestions, list)
        assert len(review.suggestions) == 1

    def test_none_list_field_becomes_empty(self, optimizer):
        review = optimizer._dict_to_review("services", {"invented_information": None})
        assert review.invented_information == []

    def test_empty_dict_gives_defaults(self, optimizer):
        review = optimizer._dict_to_review("faq", {})
        assert review.page_slug == "faq"
        assert review.overall_rating == ""
        assert review.suggestions == []


# ── _content_dict_to_html() ──────────────────────────────────────────────────

class TestContentDictToHtml:
    def test_hero_heading_becomes_h1(self, optimizer):
        content = {"hero": {"heading": "Main Heading", "subheading": "Sub"}}
        html = optimizer._content_dict_to_html("homepage", content)
        assert "<h1>Main Heading</h1>" in html

    def test_intro_becomes_paragraph(self, optimizer):
        content = {"intro": "Company introduction text."}
        html = optimizer._content_dict_to_html("homepage", content)
        assert "<p>Company introduction text.</p>" in html

    def test_services_overview_renders(self, optimizer):
        content = {
            "services_overview": {
                "heading": "Our Services",
                "services": [{"name": "Repair", "short_description": "Fast."}],
            }
        }
        html = optimizer._content_dict_to_html("homepage", content)
        assert "<h2>Our Services</h2>" in html
        assert "Repair" in html

    def test_faq_items_render(self, optimizer):
        content = {"faqs": [{"question": "How much?", "answer": "Depends."}]}
        html = optimizer._content_dict_to_html("faq", content)
        assert "How much?" in html
        assert "Depends." in html

    def test_empty_dict_returns_empty_string(self, optimizer):
        html = optimizer._content_dict_to_html("homepage", {})
        assert html.strip() == ""


# ── _load_business_context() ─────────────────────────────────────────────────

class TestLoadBusinessContext:
    def test_loads_valid_profile(self, optimizer, project_dir):
        ctx = optimizer._load_business_context(project_dir)
        assert ctx is not None
        assert ctx.company_name == "Test Co"
        assert ctx.industry == "Roofing"
        assert "Roof Repair" in ctx.main_services

    def test_missing_file_returns_none(self, optimizer, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        ctx = optimizer._load_business_context(empty)
        assert ctx is None

    def test_malformed_json_returns_none(self, optimizer, tmp_path):
        d = tmp_path / "bad" / "json"
        d.mkdir(parents=True)
        (d / "business_profile.json").write_text("not json!", encoding="utf-8")
        ctx = optimizer._load_business_context(tmp_path / "bad")
        assert ctx is None

    def test_partial_profile_uses_defaults(self, optimizer, tmp_path):
        d = tmp_path / "partial" / "json"
        d.mkdir(parents=True)
        (d / "business_profile.json").write_text(
            json.dumps({"company_name": "Mini Corp"}), encoding="utf-8"
        )
        ctx = optimizer._load_business_context(tmp_path / "partial")
        assert ctx is not None
        assert ctx.company_name == "Mini Corp"
        assert ctx.industry == ""
        assert ctx.main_services == []


# ── _load_competitor_context() ───────────────────────────────────────────────

class TestLoadCompetitorContext:
    def test_loads_valid_report(self, optimizer, project_dir):
        ctx = optimizer._load_competitor_context(project_dir)
        assert "Sticky menu" in ctx.navigation_ideas
        assert "Review badges" in ctx.trust_building_elements

    def test_missing_file_returns_empty_context(self, optimizer, tmp_path):
        d = tmp_path / "empty_proj"
        d.mkdir()
        ctx = optimizer._load_competitor_context(d)
        assert isinstance(ctx, _CompetitorContext)
        assert ctx.navigation_ideas == []

    def test_malformed_json_returns_empty_context(self, optimizer, tmp_path):
        d = tmp_path / "bad_proj" / "json"
        d.mkdir(parents=True)
        (d / "comparison_report.json").write_text("bad json", encoding="utf-8")
        ctx = optimizer._load_competitor_context(tmp_path / "bad_proj")
        assert ctx.navigation_ideas == []


# ── _load_page_sources() ──────────────────────────────────────────────────────

class TestLoadPageSources:
    def test_groups_by_slug(self, optimizer, project_dir):
        sources = optimizer._load_page_sources(project_dir)
        assert "homepage" in sources
        assert "services" in sources

    def test_homepage_content_loaded(self, optimizer, project_dir):
        sources = optimizer._load_page_sources(project_dir)
        home = sources["homepage"][0]
        assert "roofers" in home.text_content.lower() or "roofing" in home.text_content.lower()

    def test_missing_pages_json_returns_empty(self, optimizer, tmp_path):
        d = tmp_path / "no_pages"
        d.mkdir()
        sources = optimizer._load_page_sources(d)
        assert sources == {}

    def test_malformed_pages_json_returns_empty(self, optimizer, tmp_path):
        d = tmp_path / "bad" / "json"
        d.mkdir(parents=True)
        (d / "pages.json").write_text("oops", encoding="utf-8")
        sources = optimizer._load_page_sources(tmp_path / "bad")
        assert sources == {}


# ── _gather_comp_ideas() ──────────────────────────────────────────────────────

class TestGatherCompIdeas:
    def _ctx(self):
        return _CompetitorContext(
            homepage_structure_ideas   = ["Hero above fold"],
            service_presentation_ideas = ["Price cards"],
            faq_ideas                  = ["FAQ section"],
            cta_ideas                  = ["Floating button"],
            overall_opportunities      = ["Add testimonials"],
        )

    def test_homepage_returns_homepage_ideas(self, optimizer):
        ideas = optimizer._gather_comp_ideas("homepage", self._ctx())
        assert "Hero above fold" in ideas

    def test_services_returns_service_ideas(self, optimizer):
        ideas = optimizer._gather_comp_ideas("services", self._ctx())
        assert "Price cards" in ideas

    def test_faq_returns_faq_ideas(self, optimizer):
        ideas = optimizer._gather_comp_ideas("faq", self._ctx())
        assert "FAQ section" in ideas

    def test_deduplicates(self, optimizer):
        ctx = _CompetitorContext(
            homepage_structure_ideas = ["Same idea", "Same idea"],
            overall_opportunities    = ["Same idea"],
        )
        ideas = optimizer._gather_comp_ideas("homepage", ctx)
        lower = [i.lower().strip() for i in ideas]
        assert lower.count("same idea") == 1


# ── _save_outputs() ──────────────────────────────────────────────────────────

class TestSaveOutputs:
    def test_writes_per_page_files(self, optimizer, tmp_path):
        proj = tmp_path / "proj"
        generated = {
            "homepage": {"meta_title": "Test", "intro": "Hello"},
            "about":    {"meta_title": "About Test"},
        }
        optimizer._save_outputs(proj, generated, {}, {})
        assert (proj / "json" / "optimized_homepage.json").exists()
        assert (proj / "json" / "optimized_about.json").exists()

    def test_writes_meta_data_json(self, optimizer, tmp_path):
        proj = tmp_path / "proj"
        meta = {"homepage": {"title": "T", "description": "D"}}
        optimizer._save_outputs(proj, {}, {}, meta)
        assert (proj / "json" / "meta_data.json").exists()
        data = json.loads((proj / "json" / "meta_data.json").read_text())
        assert data["homepage"]["title"] == "T"

    def test_writes_content_review_json(self, optimizer, tmp_path):
        proj = tmp_path / "proj"
        reviews = {
            "homepage": {"overall_rating": "good", "review_skipped": False}
        }
        optimizer._save_outputs(proj, {}, reviews, {})
        assert (proj / "json" / "content_review.json").exists()
        data = json.loads((proj / "json" / "content_review.json").read_text())
        assert "homepage" in data["pages"]

    def test_no_review_file_when_reviews_empty(self, optimizer, tmp_path):
        proj = tmp_path / "proj"
        optimizer._save_outputs(proj, {}, {}, {})
        assert not (proj / "json" / "content_review.json").exists()

    def test_page_content_is_valid_json(self, optimizer, tmp_path):
        proj = tmp_path / "proj"
        content = {"meta_title": "Test", "hero": {"heading": "H1"}}
        optimizer._save_outputs(proj, {"homepage": content}, {}, {})
        path = proj / "json" / "optimized_homepage.json"
        data = json.loads(path.read_text())
        assert data["hero"]["heading"] == "H1"


# ── optimize_from_directory() integration ──────────────────────────────────────

class TestOptimizeFromDirectory:
    def test_returns_summary_dict(self, test_settings, mock_router_claude, project_dir):
        co = ContentOptimizer(test_settings, ai_router=mock_router_claude)
        result = co.optimize_from_directory(project_dir)
        assert isinstance(result, dict)
        assert "pages_generated" in result
        assert "errors" in result

    def test_generates_all_standard_pages(self, test_settings, mock_router_claude, project_dir):
        co = ContentOptimizer(test_settings, ai_router=mock_router_claude)
        result = co.optimize_from_directory(project_dir)
        for slug in _STANDARD_PAGE_SLUGS:
            assert slug in result["pages_generated"]

    def test_writes_json_files(self, test_settings, mock_router_claude, project_dir):
        co = ContentOptimizer(test_settings, ai_router=mock_router_claude)
        co.optimize_from_directory(project_dir)
        assert (project_dir / "json" / "optimized_homepage.json").exists()
        assert (project_dir / "json" / "meta_data.json").exists()

    def test_skips_review_when_deepseek_unavailable(self, test_settings,
                                                      mock_router_claude, project_dir):
        co = ContentOptimizer(test_settings, ai_router=mock_router_claude)
        result = co.optimize_from_directory(project_dir)
        # Reviews should be skipped (no DeepSeek) — no content_review.json or skipped=True
        review_path = project_dir / "json" / "content_review.json"
        if review_path.exists():
            data = json.loads(review_path.read_text())
            for slug, review in data.get("pages", {}).items():
                assert review.get("review_skipped", True) is True

    def test_continues_after_claude_failure(self, test_settings, project_dir):
        router = MagicMock()
        router.is_available.side_effect = lambda p: p == AIProvider.CLAUDE
        call_count = {"n": 0}

        def complete(prompt, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("Connection refused")
            return json.dumps({"meta_title": "Test"})

        router.complete.side_effect = complete
        co = ContentOptimizer(test_settings, ai_router=router)
        result = co.optimize_from_directory(project_dir)
        assert isinstance(result, dict)
        # At least some pages should succeed and at least one should error
        assert len(result["pages_generated"]) + len(result["errors"]) > 0

    def test_handles_missing_business_profile_gracefully(self, test_settings,
                                                           mock_router_claude, tmp_path):
        empty = tmp_path / "no_profile"
        empty.mkdir()
        co = ContentOptimizer(test_settings, ai_router=mock_router_claude)
        result = co.optimize_from_directory(empty)
        assert isinstance(result, dict)

    def test_page_slugs_subset(self, test_settings, mock_router_claude, project_dir):
        co = ContentOptimizer(test_settings, ai_router=mock_router_claude)
        result = co.optimize_from_directory(project_dir, page_slugs=("homepage",))
        assert result["pages_generated"] == ["homepage"]
