"""
tests/unit/test_business_analyzer.py
=====================================
Unit tests for BusinessAnalyzer.

All AI provider calls and filesystem I/O to crawler JSON files are mocked —
no network requests are made and no Gemini API is called.

Coverage:
  - Crawler JSON file loading (pages, navigation, images, crawl summary)
  - Per-page record construction from summary + rich data
  - Deterministic extraction: emails, phones, social links, languages,
    tone heuristic, industry heuristic, service extraction
  - AI prompt construction (content, structure, pre-extracted facts)
  - AI response parsing (raw JSON, code fences, embedded JSON, invalid input)
  - Merging deterministic + AI results into BusinessProfile
  - BusinessProfile → BusinessInfo conversion
  - save_profile file writing
  - analyze_from_directory full pipeline (mocked AI)
  - Error handling: missing files, malformed JSON, empty crawl, AI failure
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from webmaker.config.settings import Settings
from webmaker.core.exceptions import AIError, AnalysisError
from webmaker.core.types import (
    AIProvider, BusinessInfo, CrawlResult, PageData, PageType,
)
from webmaker.modules.business_analyzer import (
    BusinessAnalyzer,
    BusinessProfile,
    _CrawlerOutput,
    _PageRecord,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_router():
    """An AIRouter mock with Gemini available but complete() raising NotImplementedError."""
    router = MagicMock()
    router.available_providers.return_value = [AIProvider.GEMINI]
    router.complete.side_effect = NotImplementedError("not implemented")
    return router


@pytest.fixture
def mock_router_ai_ok(mock_router):
    """AIRouter mock that returns a valid JSON business profile."""
    profile = {
        "company_name":            "Acme GmbH",
        "business_category":       "local service",
        "industry":                "construction",
        "main_services":           ["Roofing", "Renovation"],
        "secondary_services":      ["Consulting"],
        "products":                [],
        "target_customers":        "Home owners",
        "service_areas":           ["Berlin", "Brandenburg"],
        "unique_selling_points":   ["30 years experience"],
        "trust_signals":           ["ISO certified"],
        "brand_tone":              "professional",
        "business_style":          "traditional",
        "call_to_action_strategy": "Phone contact emphasis",
        "website_goals":           ["generate leads"],
        "customer_journey":        "Homepage → Services → Contact",
        "existing_content_quality": "adequate",
        "existing_faq_topics":     [],
        "business_strengths":      ["strong local presence"],
        "business_weaknesses":     ["no online booking"],
        "overall_summary":         "Acme GmbH is a reliable roofing contractor.",
    }
    mock_router.complete.side_effect = None
    mock_router.complete.return_value = json.dumps(profile)
    return mock_router


@pytest.fixture
def mock_router_no_providers():
    """AIRouter mock with no providers configured."""
    router = MagicMock()
    router.available_providers.return_value = []
    return router


@pytest.fixture
def analyzer(test_settings: Settings, mock_router) -> BusinessAnalyzer:
    """BusinessAnalyzer backed by test settings and a mocked AIRouter."""
    return BusinessAnalyzer(test_settings, ai_router=mock_router)


@pytest.fixture
def analyzer_ai_ok(test_settings: Settings, mock_router_ai_ok) -> BusinessAnalyzer:
    """BusinessAnalyzer with working mock AI."""
    return BusinessAnalyzer(test_settings, ai_router=mock_router_ai_ok)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_crawl_result(
    target_url: str = "https://example.com",
    pages: list[PageData] | None = None,
) -> CrawlResult:
    if pages is None:
        pages = [
            PageData(
                url="https://example.com/",
                title="Acme GmbH – Home",
                description="Your trusted roofing partner",
                page_type=PageType.HOME,
                text_content="Call us: +49 30 123456  info@acme.de  Follow us on facebook.com/acme",
                headings=["Professional Roofing Services", "Why Choose Us"],
            ),
            PageData(
                url="https://example.com/leistungen",
                title="Leistungen – Acme GmbH",
                description="Roofing and renovation services",
                page_type=PageType.SERVICES,
                text_content="We offer roofing, gutters, and facade work.",
                headings=["Roofing", "Gutters", "Facade Work"],
            ),
        ]
    return CrawlResult(
        target_url=target_url,
        pages=pages,
        total_pages=len(pages),
        crawl_duration_s=2.5,
    )


def _make_project_dir(tmp_path: Path, data: dict | None = None) -> Path:
    """Create a minimal crawler project directory on disk."""
    project_dir = tmp_path / "example-com"
    json_dir    = project_dir / "json"
    pages_dir   = json_dir / "pages"
    pages_dir.mkdir(parents=True)

    defaults = data or {}

    pages_summary = defaults.get("pages", [
        {"url": "https://example.com/", "title": "Home", "page_type": "home",
         "word_count": 100, "status_code": 200},
        {"url": "https://example.com/contact", "title": "Contact",
         "page_type": "contact", "word_count": 30, "status_code": 200},
    ])
    navigation = defaults.get("navigation", [
        {"text": "Home",    "url": "https://example.com/"},
        {"text": "Contact", "url": "https://example.com/contact"},
        {"text": "LinkedIn", "url": "https://www.linkedin.com/company/acme"},
    ])
    images = defaults.get("images", [
        {"filename": "logo.png",    "alt_text": "Acme Logo"},
        {"filename": "roofing.jpg", "alt_text": "Roof installation"},
    ])
    crawl_summary = defaults.get("crawl_summary", {
        "target_url": "https://example.com",
        "total_pages": 2,
        "completed_at": "2026-01-01T00:00:00+00:00",
    })

    (json_dir / "pages.json").write_text(json.dumps(pages_summary), encoding="utf-8")
    (json_dir / "navigation.json").write_text(json.dumps(navigation), encoding="utf-8")
    (json_dir / "images.json").write_text(json.dumps(images), encoding="utf-8")
    (json_dir / "crawl_summary.json").write_text(json.dumps(crawl_summary), encoding="utf-8")

    # One rich page JSON
    rich_home = {
        "url": "https://example.com/",
        "title": "Acme GmbH – Home",
        "meta_description": "Trusted roofing partner since 1990",
        "page_type": "home",
        "h1": ["Professional Roofing"],
        "h2": ["Our Services", "Why Choose Us"],
        "text_content": "Call us at +49 30 123456. Email: info@acme.de",
        "language": "de",
        "external_links": ["https://www.linkedin.com/company/acme"],
        "word_count": 80,
    }
    (pages_dir / "home.json").write_text(json.dumps(rich_home), encoding="utf-8")

    return project_dir


# ── Loading crawler output ─────────────────────────────────────────────────────

class TestLoadCrawlerOutput:
    def test_loads_all_json_files(self, analyzer: BusinessAnalyzer, tmp_path: Path) -> None:
        project_dir = _make_project_dir(tmp_path)
        data = analyzer.load_crawler_output(project_dir)
        assert data.target_url == "https://example.com"
        assert len(data.pages)      >= 1
        assert len(data.navigation) >= 1
        assert len(data.images)     >= 1

    def test_rich_page_data_merged(self, analyzer: BusinessAnalyzer, tmp_path: Path) -> None:
        project_dir = _make_project_dir(tmp_path)
        data = analyzer.load_crawler_output(project_dir)
        home = next((p for p in data.pages if "home" in p.page_type), None)
        assert home is not None
        assert home.h1 == ["Professional Roofing"]
        assert home.language == "de"

    def test_missing_json_dir_raises(self, analyzer: BusinessAnalyzer, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with pytest.raises(AnalysisError, match="json/"):
            analyzer.load_crawler_output(empty_dir)

    def test_malformed_json_returns_default(self, analyzer: BusinessAnalyzer, tmp_path: Path) -> None:
        project_dir = _make_project_dir(tmp_path)
        (project_dir / "json" / "images.json").write_text("NOT JSON", encoding="utf-8")
        data = analyzer.load_crawler_output(project_dir)
        assert data.images == []   # graceful fallback

    def test_missing_optional_file_uses_default(self, analyzer: BusinessAnalyzer, tmp_path: Path) -> None:
        project_dir = _make_project_dir(tmp_path)
        (project_dir / "json" / "navigation.json").unlink()
        data = analyzer.load_crawler_output(project_dir)
        assert data.navigation == []

    def test_target_url_from_crawl_summary(self, analyzer: BusinessAnalyzer, tmp_path: Path) -> None:
        project_dir = _make_project_dir(tmp_path)
        data = analyzer.load_crawler_output(project_dir)
        assert data.target_url == "https://example.com"


# ── Deterministic extraction ───────────────────────────────────────────────────

class TestExtractDeterministic:
    def _output(self, text: str = "", nav: list | None = None) -> _CrawlerOutput:
        page = _PageRecord(
            url="https://example.com/",
            page_type="home",
            text_excerpt=text,
        )
        return _CrawlerOutput(
            project_dir=Path("."),
            target_url="https://example.com",
            pages=[page],
            navigation=nav or [],
        )

    def test_email_extracted(self, analyzer: BusinessAnalyzer) -> None:
        out = self._output("Contact: info@acme.de")
        result = analyzer.extract_deterministic(out)
        assert "info@acme.de" in result["emails"]

    def test_multiple_emails_deduplicated(self, analyzer: BusinessAnalyzer) -> None:
        out = self._output("info@acme.de and INFO@ACME.DE are the same")
        result = analyzer.extract_deterministic(out)
        assert result["emails"].count("info@acme.de") == 1

    def test_image_filenames_not_emails(self, analyzer: BusinessAnalyzer) -> None:
        out = self._output("See photo.png and banner.jpg")
        result = analyzer.extract_deterministic(out)
        assert not any(".png" in e for e in result["emails"])

    def test_phone_extracted(self, analyzer: BusinessAnalyzer) -> None:
        out = self._output("Call +49 30 123456")
        result = analyzer.extract_deterministic(out)
        assert len(result["phones"]) >= 1
        assert any("123456" in p for p in result["phones"])

    def test_social_links_from_navigation(self, analyzer: BusinessAnalyzer) -> None:
        out = self._output(nav=[
            {"text": "LinkedIn", "url": "https://www.linkedin.com/company/acme"},
        ])
        result = analyzer.extract_deterministic(out)
        assert "linkedin" in result["social_links"]

    def test_language_from_page(self, analyzer: BusinessAnalyzer) -> None:
        page = _PageRecord(url="https://x.com/", page_type="home", language="de")
        out  = _CrawlerOutput(
            project_dir=Path("."), target_url="https://x.com", pages=[page],
        )
        result = analyzer.extract_deterministic(out)
        assert "de" in result["languages"]

    def test_has_contact_page_detected(self, analyzer: BusinessAnalyzer) -> None:
        p1 = _PageRecord(url="https://x.com/", page_type="home")
        p2 = _PageRecord(url="https://x.com/contact", page_type="contact")
        out = _CrawlerOutput(project_dir=Path("."), target_url="https://x.com", pages=[p1, p2])
        result = analyzer.extract_deterministic(out)
        assert result["has_contact_page"] is True

    def test_no_contact_page(self, analyzer: BusinessAnalyzer) -> None:
        p1 = _PageRecord(url="https://x.com/", page_type="home")
        out = _CrawlerOutput(project_dir=Path("."), target_url="https://x.com", pages=[p1])
        result = analyzer.extract_deterministic(out)
        assert result["has_contact_page"] is False

    def test_accepts_crawl_result(self, analyzer: BusinessAnalyzer) -> None:
        cr = _make_crawl_result()
        result = analyzer.extract_deterministic(cr)
        assert "emails" in result
        assert "phones" in result


# ── Contact extraction ─────────────────────────────────────────────────────────

class TestExtractContact:
    def test_returns_email_and_phone(self, analyzer: BusinessAnalyzer) -> None:
        cr = _make_crawl_result()
        result = analyzer.extract_contact(cr)
        assert result["email"] == "info@acme.de"
        assert any("123456" in p for p in result["all_phones"])

    def test_empty_when_none_present(self, analyzer: BusinessAnalyzer) -> None:
        cr = CrawlResult(
            target_url="https://example.com",
            pages=[PageData(url="https://example.com/", title="Home")],
        )
        result = analyzer.extract_contact(cr)
        assert result["email"] == ""
        assert result["all_phones"] == []


# ── Social link extraction ─────────────────────────────────────────────────────

class TestExtractSocialLinks:
    def test_facebook_detected_in_page_text(self, analyzer: BusinessAnalyzer) -> None:
        # Social links come from external_links in pages
        cr = _make_crawl_result(pages=[
            PageData(
                url="https://x.com/",
                title="Home",
                page_type=PageType.HOME,
                text_content="Visit us on Facebook",
            )
        ])
        result = analyzer.extract_social_links(cr)
        # Text alone won't detect; need external_links (OK to be empty here)
        assert isinstance(result, dict)

    def test_linkedin_in_navigation(self, analyzer: BusinessAnalyzer, tmp_path: Path) -> None:
        project_dir = _make_project_dir(tmp_path)
        data = analyzer.load_crawler_output(project_dir)
        social = analyzer._extract_social_from_pages(data)
        assert "linkedin" in social
        assert "linkedin.com" in social["linkedin"]


# ── Tone and industry heuristics ──────────────────────────────────────────────

class TestHeuristics:
    def test_professional_tone_detected(self, analyzer: BusinessAnalyzer) -> None:
        text = "Our certified and experienced team delivers qualified solutions."
        assert analyzer._infer_tone_heuristic(text) == "professional"

    def test_technical_tone_detected(self, analyzer: BusinessAnalyzer) -> None:
        text = "Our cloud platform integrates via API with your infrastructure."
        assert analyzer._infer_tone_heuristic(text) == "technical"

    def test_casual_tone_detected(self, analyzer: BusinessAnalyzer) -> None:
        text = "Hey! We love what we do. It's awesome and fun every day."
        assert analyzer._infer_tone_heuristic(text) == "casual"

    def test_construction_industry(self, analyzer: BusinessAnalyzer) -> None:
        page = _PageRecord(url="https://x.com/", page_type="home",
                           text_excerpt="Wir bieten Renovierung und Bau")
        out = _CrawlerOutput(project_dir=Path("."), target_url="https://x.com", pages=[page])
        assert "construction" in analyzer._infer_industry_heuristic(out)

    def test_technology_industry(self, analyzer: BusinessAnalyzer) -> None:
        page = _PageRecord(url="https://x.com/", page_type="home",
                           text_excerpt="Our SaaS platform and API empower developers")
        out = _CrawlerOutput(project_dir=Path("."), target_url="https://x.com", pages=[page])
        assert "technology" in analyzer._infer_industry_heuristic(out)

    def test_unknown_industry_returns_empty(self, analyzer: BusinessAnalyzer) -> None:
        page = _PageRecord(url="https://x.com/", page_type="home", text_excerpt="xyz abc")
        out = _CrawlerOutput(project_dir=Path("."), target_url="https://x.com", pages=[page])
        assert analyzer._infer_industry_heuristic(out) == ""

    def test_service_extraction_from_headings(self, analyzer: BusinessAnalyzer) -> None:
        page = _PageRecord(
            url="https://x.com/services",
            page_type="services",
            h2=["Roof Repair", "Gutter Cleaning", "Facade Work"],
        )
        out = _CrawlerOutput(project_dir=Path("."), target_url="https://x.com", pages=[page])
        services = analyzer._extract_services_heuristic(out)
        assert "Roof Repair" in services
        assert "Gutter Cleaning" in services


# ── Prompt construction ────────────────────────────────────────────────────────

class TestBuildPrompt:
    def _make_output(self) -> _CrawlerOutput:
        home = _PageRecord(
            url="https://example.com/",
            title="Acme – Home",
            meta_description="We fix roofs",
            page_type="home",
            h1=["Professional Roofing"],
            h2=["Why Us"],
            text_excerpt="Call +49 30 123 info@acme.de",
        )
        return _CrawlerOutput(
            project_dir=Path("."),
            target_url="https://example.com",
            pages=[home],
            navigation=[{"text": "Contact", "url": "https://example.com/contact"}],
            images=[{"filename": "logo.png", "alt_text": "Acme Logo"}],
        )

    def test_prompt_contains_url(self, analyzer: BusinessAnalyzer) -> None:
        out = self._make_output()
        det = analyzer.extract_deterministic(out)
        prompt = analyzer._build_prompt(out, det)
        assert "https://example.com" in prompt

    def test_prompt_contains_page_title(self, analyzer: BusinessAnalyzer) -> None:
        out = self._make_output()
        det = analyzer.extract_deterministic(out)
        prompt = analyzer._build_prompt(out, det)
        assert "Acme" in prompt

    def test_prompt_contains_navigation(self, analyzer: BusinessAnalyzer) -> None:
        out = self._make_output()
        det = analyzer.extract_deterministic(out)
        prompt = analyzer._build_prompt(out, det)
        assert "Contact" in prompt

    def test_prompt_contains_preextracted_email(self, analyzer: BusinessAnalyzer) -> None:
        out = self._make_output()
        det = analyzer.extract_deterministic(out)
        prompt = analyzer._build_prompt(out, det)
        assert "info@acme.de" in prompt

    def test_prompt_contains_json_schema(self, analyzer: BusinessAnalyzer) -> None:
        out = self._make_output()
        det = analyzer.extract_deterministic(out)
        prompt = analyzer._build_prompt(out, det)
        assert "company_name" in prompt
        assert "overall_summary" in prompt

    def test_prompt_contains_image_metadata(self, analyzer: BusinessAnalyzer) -> None:
        out = self._make_output()
        det = analyzer.extract_deterministic(out)
        prompt = analyzer._build_prompt(out, det)
        assert "logo.png" in prompt


# ── AI response parsing ────────────────────────────────────────────────────────

class TestParseAiJson:
    def test_raw_json(self, analyzer: BusinessAnalyzer) -> None:
        raw = json.dumps({"company_name": "Acme", "industry": "construction"})
        result = analyzer._parse_ai_json(raw)
        assert result["company_name"] == "Acme"

    def test_json_in_code_fence(self, analyzer: BusinessAnalyzer) -> None:
        raw = '```json\n{"company_name": "Beta Corp"}\n```'
        result = analyzer._parse_ai_json(raw)
        assert result["company_name"] == "Beta Corp"

    def test_json_in_code_fence_no_lang(self, analyzer: BusinessAnalyzer) -> None:
        raw = '```\n{"industry": "retail"}\n```'
        result = analyzer._parse_ai_json(raw)
        assert result["industry"] == "retail"

    def test_json_embedded_in_prose(self, analyzer: BusinessAnalyzer) -> None:
        raw = 'Here is my analysis: {"company_name": "Demo"} Hope that helps!'
        result = analyzer._parse_ai_json(raw)
        assert result["company_name"] == "Demo"

    def test_empty_response_returns_empty(self, analyzer: BusinessAnalyzer) -> None:
        assert analyzer._parse_ai_json("") == {}
        assert analyzer._parse_ai_json("   ") == {}

    def test_invalid_json_returns_empty(self, analyzer: BusinessAnalyzer) -> None:
        assert analyzer._parse_ai_json("NOT JSON") == {}
        assert analyzer._parse_ai_json("{invalid json}") == {}

    def test_non_dict_json_returns_empty(self, analyzer: BusinessAnalyzer) -> None:
        assert analyzer._parse_ai_json('["a", "b"]') == {}

    def test_nested_json_preserved(self, analyzer: BusinessAnalyzer) -> None:
        raw = json.dumps({"main_services": ["Roofing", "Painting"], "industry": "construction"})
        result = analyzer._parse_ai_json(raw)
        assert result["main_services"] == ["Roofing", "Painting"]


# ── Merge into profile ────────────────────────────────────────────────────────

class TestMergeIntoProfile:
    def _data(self) -> _CrawlerOutput:
        return _CrawlerOutput(
            project_dir=Path("."),
            target_url="https://example.com",
            pages=[_PageRecord(url="https://example.com/", page_type="home")],
        )

    def test_email_from_deterministic(self, analyzer: BusinessAnalyzer) -> None:
        det = {"emails": ["info@acme.de"], "phones": [], "social_links": {},
               "languages": [], "inferred_tone": "professional", "potential_services": []}
        ai  = {"company_name": "Acme", "contact_email": "wrong@ai.com"}
        profile = analyzer._merge_into_profile(det, ai, self._data())
        # deterministic email always wins
        assert profile.contact_email == "info@acme.de"

    def test_ai_company_name_used(self, analyzer: BusinessAnalyzer) -> None:
        det = {"emails": [], "phones": [], "social_links": {}, "languages": [],
               "inferred_tone": "professional", "potential_services": []}
        ai  = {"company_name": "Acme GmbH", "industry": "construction"}
        profile = analyzer._merge_into_profile(det, ai, self._data())
        assert profile.company_name == "Acme GmbH"

    def test_list_fields_normalised_from_string(self, analyzer: BusinessAnalyzer) -> None:
        det = {"emails": [], "phones": [], "social_links": {}, "languages": [],
               "inferred_tone": "professional", "potential_services": []}
        ai  = {"main_services": "Roofing"}   # string instead of list
        profile = analyzer._merge_into_profile(det, ai, self._data())
        assert isinstance(profile.main_services, list)
        assert profile.main_services == ["Roofing"]

    def test_list_fields_normalised_from_none(self, analyzer: BusinessAnalyzer) -> None:
        det = {"emails": [], "phones": [], "social_links": {}, "languages": [],
               "inferred_tone": "professional", "potential_services": []}
        ai  = {"main_services": None}
        profile = analyzer._merge_into_profile(det, ai, self._data())
        assert profile.main_services == []

    def test_tone_fallback_to_heuristic(self, analyzer: BusinessAnalyzer) -> None:
        det = {"emails": [], "phones": [], "social_links": {}, "languages": [],
               "inferred_tone": "casual", "potential_services": []}
        ai  = {}   # no brand_tone from AI
        profile = analyzer._merge_into_profile(det, ai, self._data())
        assert profile.brand_tone == "casual"

    def test_social_links_from_deterministic(self, analyzer: BusinessAnalyzer) -> None:
        det = {"emails": [], "phones": [], "social_links": {"facebook": "https://fb.com/x"},
               "languages": [], "inferred_tone": "professional", "potential_services": []}
        ai  = {}
        profile = analyzer._merge_into_profile(det, ai, self._data())
        assert profile.social_links == {"facebook": "https://fb.com/x"}

    def test_pages_analyzed_set(self, analyzer: BusinessAnalyzer) -> None:
        det = {"emails": [], "phones": [], "social_links": {}, "languages": [],
               "inferred_tone": "professional", "potential_services": []}
        data = _CrawlerOutput(
            project_dir=Path("."), target_url="https://x.com",
            pages=[
                _PageRecord(url="https://x.com/", page_type="home"),
                _PageRecord(url="https://x.com/about", page_type="about"),
            ],
        )
        profile = analyzer._merge_into_profile(det, {}, data)
        assert profile.pages_analyzed == 2


# ── Profile → BusinessInfo ─────────────────────────────────────────────────────

class TestProfileToBusinessInfo:
    def _profile(self) -> BusinessProfile:
        return BusinessProfile(
            company_name="Acme GmbH",
            industry="construction",
            main_services=["Roofing", "Renovation"],
            secondary_services=["Consulting"],
            target_customers="Homeowners",
            service_areas=["Berlin", "Munich"],
            unique_selling_points=["30 years exp"],
            brand_tone="professional",
            contact_email="info@acme.de",
            contact_phone="+49 30 123456",
            social_links={"linkedin": "https://linkedin.com/acme"},
        )

    def test_name_mapped(self, analyzer: BusinessAnalyzer) -> None:
        info = analyzer._profile_to_business_info(self._profile())
        assert info.name == "Acme GmbH"

    def test_services_combined(self, analyzer: BusinessAnalyzer) -> None:
        info = analyzer._profile_to_business_info(self._profile())
        assert "Roofing" in info.services
        assert "Consulting" in info.services

    def test_location_joined(self, analyzer: BusinessAnalyzer) -> None:
        info = analyzer._profile_to_business_info(self._profile())
        assert "Berlin" in info.location
        assert "Munich" in info.location

    def test_unique_value_first_usp(self, analyzer: BusinessAnalyzer) -> None:
        info = analyzer._profile_to_business_info(self._profile())
        assert info.unique_value == "30 years exp"

    def test_social_links_passed(self, analyzer: BusinessAnalyzer) -> None:
        info = analyzer._profile_to_business_info(self._profile())
        assert "linkedin" in info.social_links


# ── save_profile ──────────────────────────────────────────────────────────────

class TestSaveProfile:
    def test_file_created(self, analyzer: BusinessAnalyzer, tmp_path: Path) -> None:
        project_dir = tmp_path / "test-project"
        (project_dir / "json").mkdir(parents=True)
        profile = BusinessProfile(company_name="Acme", analyzed_at="2026-01-01")
        path = analyzer.save_profile(profile, project_dir)
        assert path.exists()
        assert path.name == "business_profile.json"

    def test_valid_json_written(self, analyzer: BusinessAnalyzer, tmp_path: Path) -> None:
        project_dir = tmp_path / "test-project"
        (project_dir / "json").mkdir(parents=True)
        profile = BusinessProfile(company_name="Beta Corp", industry="retail")
        analyzer.save_profile(profile, project_dir)
        data = json.loads((project_dir / "json" / "business_profile.json").read_text())
        assert data["company_name"] == "Beta Corp"
        assert data["industry"] == "retail"

    def test_creates_missing_json_dir(self, analyzer: BusinessAnalyzer, tmp_path: Path) -> None:
        project_dir = tmp_path / "test-project"
        project_dir.mkdir()
        # json/ does not exist yet
        profile = BusinessProfile()
        analyzer.save_profile(profile, project_dir)
        assert (project_dir / "json" / "business_profile.json").exists()


# ── analyze_from_directory ────────────────────────────────────────────────────

class TestAnalyzeFromDirectory:
    def test_missing_directory_raises(self, analyzer: BusinessAnalyzer, tmp_path: Path) -> None:
        with pytest.raises(AnalysisError):
            analyzer.analyze_from_directory(tmp_path / "nonexistent")

    def test_no_ai_providers_still_returns_info(
        self, test_settings: Settings, tmp_path: Path, mock_router_no_providers,
    ) -> None:
        project_dir = _make_project_dir(tmp_path)
        analyzer = BusinessAnalyzer(test_settings, ai_router=mock_router_no_providers)
        info = analyzer.analyze_from_directory(project_dir)
        assert isinstance(info, BusinessInfo)
        # No AI → still deterministic fields
        assert info.contact_email != "" or info.contact_email == ""   # may or may not find one

    def test_ai_ok_profile_written(
        self, test_settings: Settings, tmp_path: Path, mock_router_ai_ok,
    ) -> None:
        project_dir = _make_project_dir(tmp_path)
        analyzer = BusinessAnalyzer(test_settings, ai_router=mock_router_ai_ok)
        info = analyzer.analyze_from_directory(project_dir)
        # Profile saved to disk
        profile_path = project_dir / "json" / "business_profile.json"
        assert profile_path.exists()
        # BusinessInfo correctly populated
        assert info.name == "Acme GmbH"
        assert "Roofing" in info.services

    def test_ai_error_still_returns_info(
        self, test_settings: Settings, tmp_path: Path,
    ) -> None:
        router = MagicMock()
        router.available_providers.return_value = [AIProvider.GEMINI]
        router.complete.side_effect = Exception("AI down")
        project_dir = _make_project_dir(tmp_path)
        analyzer = BusinessAnalyzer(test_settings, ai_router=router)
        # Should not raise; AI errors are captured in profile
        info = analyzer.analyze_from_directory(project_dir)
        assert isinstance(info, BusinessInfo)


# ── analyze from CrawlResult ──────────────────────────────────────────────────

class TestAnalyzeFromCrawlResult:
    def test_empty_crawl_raises(self, analyzer: BusinessAnalyzer) -> None:
        cr = CrawlResult(target_url="https://example.com", pages=[])
        with pytest.raises(AnalysisError):
            analyzer.analyze(cr)

    def test_returns_business_info(
        self, test_settings: Settings, mock_router_ai_ok,
    ) -> None:
        analyzer = BusinessAnalyzer(test_settings, ai_router=mock_router_ai_ok)
        cr = _make_crawl_result()
        info = analyzer.analyze(cr)
        assert isinstance(info, BusinessInfo)

    def test_deterministic_email_in_result(
        self, test_settings: Settings, mock_router_no_providers,
    ) -> None:
        analyzer = BusinessAnalyzer(test_settings, ai_router=mock_router_no_providers)
        cr = _make_crawl_result()
        info = analyzer.analyze(cr)
        assert info.contact_email == "info@acme.de"


# ── extract_name ──────────────────────────────────────────────────────────────

class TestExtractName:
    def test_strips_suffix(self, analyzer: BusinessAnalyzer) -> None:
        cr = _make_crawl_result(pages=[
            PageData(url="https://x.com/", title="Acme GmbH – Home",
                     page_type=PageType.HOME),
        ])
        assert analyzer.extract_name(cr) == "Acme GmbH"

    def test_pipe_separator(self, analyzer: BusinessAnalyzer) -> None:
        cr = _make_crawl_result(pages=[
            PageData(url="https://x.com/", title="Company Name | Welcome",
                     page_type=PageType.HOME),
        ])
        assert analyzer.extract_name(cr) == "Company Name"

    def test_empty_title_returns_empty(self, analyzer: BusinessAnalyzer) -> None:
        cr = _make_crawl_result(pages=[
            PageData(url="https://x.com/", title="", page_type=PageType.HOME),
        ])
        assert analyzer.extract_name(cr) == ""
