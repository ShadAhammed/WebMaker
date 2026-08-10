"""
tests/unit/test_competitor_analyzer.py
=======================================
Unit tests for CompetitorAnalyzer.

All AI provider calls, WebsiteCrawler.crawl(), and filesystem I/O are mocked
— no network requests, no Playwright, no Gemini API.

Coverage:
  - Initialization with and without injected ai_router / crawler
  - analyze() raises AnalysisError (requires explicit URLs)
  - find_competitors() raises NotImplementedError
  - analyze_from_urls() full pipeline: load context, crawl, profile, compare, save
  - analyze_from_urls() with no client context
  - analyze_from_urls() when crawler fails for one competitor
  - analyze_from_urls() when AI is unavailable
  - profile_competitor() returns CompetitorInfo
  - _profile_competitor_url() crawl error captured in entry
  - _profile_competitor_url() AI error captured in entry
  - _profile_competitor_url() invalid URL captured
  - _build_competitor_prompt() structure and content
  - _build_comparison_prompt() structure and content
  - _parse_ai_json() raw JSON, code-fenced, embedded, invalid
  - _dict_to_competitor_profile() field normalisation
  - _merge_comparison_report() list merging
  - identify_content_gaps() gap detection
  - generate_recommendations() merges gaps + weaknesses
  - _save_outputs() writes expected files
  - _load_client_context() missing file, malformed file, valid file
  - _normalise_url() edge cases
  - _url_to_slug() edge cases
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from webmaker.config.settings import Settings
from webmaker.core.exceptions import AIError, AnalysisError, CrawlerError
from webmaker.core.types import (
    AIProvider, AnalysisResult, BusinessInfo, CompetitorInfo,
    CrawlResult, PageData, PageType,
)
from webmaker.modules.competitor_analyzer import (
    CompetitorAnalyzer,
    _ClientContext,
    _ComparisonReport,
    _CompetitorEntry,
    _CompetitorProfile,
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
    """AIRouter with Gemini available; complete() raises NotImplementedError."""
    r = MagicMock()
    r.available_providers.return_value = [AIProvider.GEMINI]
    r.complete.side_effect = NotImplementedError("not yet implemented")
    return r


@pytest.fixture
def mock_router_ok(mock_router):
    """AIRouter that returns a valid competitor profile JSON."""
    profile_json = json.dumps({
        "company_name":         "Acme Roofing GmbH",
        "business_category":    "local service",
        "main_services":        ["Roof repair", "New roofs"],
        "service_structure":    "multi-page",
        "navigation_structure": "standard",
        "homepage_layout":      "hero + services + testimonials",
        "cta_strategy":         "Contact us for a free quote",
        "trust_signals":        ["10+ years experience", "Certified"],
        "contact_methods":      ["phone", "form"],
        "service_areas":        ["Berlin", "Potsdam"],
        "has_faq":              True,
        "faq_topics":           ["Pricing", "Timeline"],
        "image_usage":          "moderate",
        "content_quality":      "good",
        "content_depth":        "moderate",
        "brand_tone":           "professional",
        "customer_focus":       "homeowners",
        "strengths":            ["Clear pricing page", "Strong trust signals"],
        "weaknesses":           ["No online booking", "Slow site"],
    })
    mock_router.complete.side_effect = None
    mock_router.complete.return_value = profile_json
    return mock_router


@pytest.fixture
def mock_router_comparison_ok(mock_router_ok):
    """AIRouter that returns comparison JSON on the second call."""
    comparison_json = json.dumps({
        "features_competitors_have":    ["Online booking", "Live chat"],
        "navigation_ideas":             ["Sticky navigation bar"],
        "trust_building_elements":      ["Badges", "Review count"],
        "homepage_structure_ideas":     ["Hero with CTA above fold"],
        "service_presentation_ideas":   ["Price cards"],
        "customer_journey_ideas":       ["Clear funnel from landing to quote"],
        "content_organization_ideas":   ["Service tabs"],
        "cta_ideas":                    ["Floating call button"],
        "faq_ideas":                    ["Cost FAQ section"],
        "image_presentation_ideas":     ["Before/after gallery"],
        "local_business_ideas":         ["Google Maps embed", "Local phone number"],
        "overall_opportunities":        ["Add testimonials", "Speed up load time"],
    })
    call_count = {"n": 0}
    profile_json = mock_router_ok.complete.return_value

    def side_effect(prompt, **kwargs):
        call_count["n"] += 1
        return comparison_json if call_count["n"] > 1 else profile_json

    mock_router_ok.complete.side_effect = side_effect
    return mock_router_ok


@pytest.fixture
def mock_crawler():
    """WebsiteCrawler that returns a minimal CrawlResult."""
    cr = MagicMock()
    cr.crawl.return_value = CrawlResult(
        target_url="https://competitor.example.com",
        pages=[
            PageData(
                url="https://competitor.example.com",
                title="Competitor Home",
                description="We fix roofs",
                page_type=PageType.HOME,
                text_content="Welcome to Competitor Roofing. We offer fast service.",
                headings=["Home", "Services", "Contact"],
            ),
            PageData(
                url="https://competitor.example.com/services",
                title="Our Services",
                page_type=PageType.SERVICES,
                text_content="We offer roof repair and installation.",
                headings=["Services"],
            ),
        ],
        total_pages=2,
        crawl_duration_s=1.5,
    )
    return cr


@pytest.fixture
def analyzer(test_settings, mock_router, mock_crawler):
    return CompetitorAnalyzer(
        test_settings,
        ai_router=mock_router,
        crawler=mock_crawler,
    )


@pytest.fixture
def project_dir(tmp_path):
    """Create a project directory with a business_profile.json."""
    d = tmp_path / "projects" / "client-example"
    (d / "json").mkdir(parents=True)
    profile = {
        "company_name":     "Client Roofers",
        "industry":         "Roofing",
        "main_services":    ["Roof inspection", "Emergency repair"],
        "target_customers": "homeowners",
        "service_areas":    ["Hamburg"],
        "brand_tone":       "professional",
        "source_url":       "https://client.example.com",
    }
    (d / "json" / "business_profile.json").write_text(
        json.dumps(profile), encoding="utf-8"
    )
    return d


# ── Initialization ─────────────────────────────────────────────────────────────

class TestInit:
    def test_creates_ai_router_if_not_provided(self, test_settings):
        with patch("webmaker.modules.competitor_analyzer.AIRouter") as mock_cls:
            with patch("webmaker.modules.competitor_analyzer.WebsiteCrawler"):
                mock_cls.return_value = MagicMock()
                mock_cls.return_value.available_providers.return_value = []
                ca = CompetitorAnalyzer(test_settings)
                mock_cls.assert_called_once_with(test_settings)

    def test_creates_crawler_if_not_provided(self, test_settings):
        with patch("webmaker.modules.competitor_analyzer.AIRouter"):
            with patch("webmaker.modules.competitor_analyzer.WebsiteCrawler") as mock_cls:
                mock_cls.return_value = MagicMock()
                ca = CompetitorAnalyzer(test_settings)
                mock_cls.assert_called_once_with(test_settings)

    def test_uses_injected_router_and_crawler(self, test_settings, mock_router, mock_crawler):
        ca = CompetitorAnalyzer(test_settings, ai_router=mock_router, crawler=mock_crawler)
        assert ca._ai_router is mock_router
        assert ca._crawler  is mock_crawler

    def test_max_competitors_from_settings(self, test_settings, mock_router, mock_crawler):
        ca = CompetitorAnalyzer(test_settings, ai_router=mock_router, crawler=mock_crawler)
        assert ca._max_competitors == test_settings.competitor_max_count

    def test_max_competitors_override(self, test_settings, mock_router, mock_crawler):
        ca = CompetitorAnalyzer(test_settings, max_competitors=2,
                                ai_router=mock_router, crawler=mock_crawler)
        assert ca._max_competitors == 2


# ── analyze() raises AnalysisError ────────────────────────────────────────────

class TestAnalyze:
    def test_analyze_raises_analysis_error(self, analyzer):
        business = BusinessInfo(name="Test", industry="Roofing", services=["Repair"])
        with pytest.raises(AnalysisError):
            analyzer.analyze(business)

    def test_analyze_error_message_contains_hint(self, analyzer):
        business = BusinessInfo(name="Test")
        with pytest.raises(AnalysisError, match="analyze_from_urls"):
            analyzer.analyze(business)


# ── find_competitors() raises NotImplementedError ─────────────────────────────

class TestFindCompetitors:
    def test_find_competitors_raises(self, analyzer):
        with pytest.raises(NotImplementedError):
            analyzer.find_competitors("Roofing", "Berlin")


# ── profile_competitor() ──────────────────────────────────────────────────────

class TestProfileCompetitor:
    def test_returns_competitor_info(self, test_settings, mock_router_ok, mock_crawler):
        ca = CompetitorAnalyzer(test_settings,
                                ai_router=mock_router_ok, crawler=mock_crawler)
        info = ca.profile_competitor("https://competitor.example.com")
        assert isinstance(info, CompetitorInfo)
        # _normalise_url adds a trailing slash to bare root URLs
        assert info.url in ("https://competitor.example.com",
                            "https://competitor.example.com/")

    def test_uses_crawler(self, test_settings, mock_router_ok, mock_crawler):
        ca = CompetitorAnalyzer(test_settings,
                                ai_router=mock_router_ok, crawler=mock_crawler)
        ca.profile_competitor("https://competitor.example.com")
        mock_crawler.crawl.assert_called_once()

    def test_invalid_url_does_not_raise(self, analyzer):
        info = analyzer.profile_competitor("not-a-url")
        assert isinstance(info, CompetitorInfo)
        assert info.url == "not-a-url"


# ── _profile_competitor_url() ─────────────────────────────────────────────────

class TestProfileCompetitorUrl:
    def test_successful_crawl_and_ai(self, test_settings, mock_router_ok, mock_crawler):
        ca = CompetitorAnalyzer(test_settings,
                                ai_router=mock_router_ok, crawler=mock_crawler)
        entry = ca._profile_competitor_url("https://competitor.example.com")
        assert entry.crawled is True
        assert entry.profile.company_name == "Acme Roofing GmbH"
        assert entry.crawl_errors == []
        assert entry.analysis_errors == []

    def test_crawler_error_captured(self, test_settings, mock_router_ok, mock_crawler):
        mock_crawler.crawl.side_effect = CrawlerError("Site unreachable")
        ca = CompetitorAnalyzer(test_settings,
                                ai_router=mock_router_ok, crawler=mock_crawler)
        entry = ca._profile_competitor_url("https://broken.example.com")
        assert entry.crawled is False
        assert len(entry.crawl_errors) == 1
        assert entry.analysis_errors == []

    def test_ai_notimplemented_captured_as_ai_error(self, analyzer, mock_crawler):
        # mock_router has complete() raising NotImplementedError
        entry = analyzer._profile_competitor_url("https://competitor.example.com")
        assert entry.crawled is True
        assert len(entry.analysis_errors) == 1
        assert "not yet implemented" in entry.analysis_errors[0].lower() or \
               "airouter" in entry.analysis_errors[0].lower()

    def test_no_ai_providers_skips_ai(self, test_settings, mock_router, mock_crawler):
        mock_router.available_providers.return_value = []
        ca = CompetitorAnalyzer(test_settings,
                                ai_router=mock_router, crawler=mock_crawler)
        entry = ca._profile_competitor_url("https://competitor.example.com")
        assert entry.crawled is True
        assert any("No AI providers" in e for e in entry.analysis_errors)

    def test_invalid_url_returns_entry_with_error(self, analyzer):
        entry = analyzer._profile_competitor_url("ftp://bad-scheme.com")
        assert entry.crawled is False
        assert len(entry.crawl_errors) == 1

    def test_empty_url_returns_error(self, analyzer):
        entry = analyzer._profile_competitor_url("")
        assert entry.crawled is False
        assert len(entry.crawl_errors) == 1


# ── _build_competitor_prompt() ────────────────────────────────────────────────

class TestBuildCompetitorPrompt:
    def _make_crawl_result(self):
        return CrawlResult(
            target_url="https://example.com",
            pages=[
                PageData(
                    url="https://example.com",
                    title="Example Home",
                    description="Best service",
                    page_type=PageType.HOME,
                    text_content="We provide quality roofing services.",
                    headings=["Welcome"],
                ),
            ],
            total_pages=1,
        )

    def test_contains_url(self, analyzer):
        cr = self._make_crawl_result()
        prompt = analyzer._build_competitor_prompt(cr)
        assert "https://example.com" in prompt

    def test_contains_page_title(self, analyzer):
        cr = self._make_crawl_result()
        prompt = analyzer._build_competitor_prompt(cr)
        assert "Example Home" in prompt

    def test_contains_json_schema(self, analyzer):
        cr = self._make_crawl_result()
        prompt = analyzer._build_competitor_prompt(cr)
        assert "company_name" in prompt
        assert "strengths" in prompt
        assert "has_faq" in prompt

    def test_includes_client_context_when_provided(self, analyzer):
        cr = self._make_crawl_result()
        ctx = _ClientContext(
            company_name="Client Co",
            industry="Roofing",
            main_services=["Repair"],
        )
        prompt = analyzer._build_competitor_prompt(cr, client_ctx=ctx)
        assert "Client Co" in prompt
        assert "Roofing" in prompt

    def test_no_client_context_omits_client_section(self, analyzer):
        cr = self._make_crawl_result()
        prompt = analyzer._build_competitor_prompt(cr, client_ctx=None)
        assert "CLIENT CONTEXT" not in prompt

    def test_respects_max_pages(self, analyzer):
        pages = [
            PageData(
                url=f"https://example.com/page{i}",
                title=f"Page {i}",
                page_type=PageType.UNKNOWN,
                text_content="x" * 100,
            )
            for i in range(20)
        ]
        cr = CrawlResult(target_url="https://example.com", pages=pages, total_pages=20)
        prompt = analyzer._build_competitor_prompt(cr)
        # Should not include all 20 pages
        count = prompt.count("https://example.com/page")
        assert count <= 8


# ── _build_comparison_prompt() ────────────────────────────────────────────────

class TestBuildComparisonPrompt:
    def _make_entries(self, count=2):
        entries = []
        for i in range(count):
            e = _CompetitorEntry(
                url=f"https://comp{i}.example.com",
                crawled=True,
                profile=_CompetitorProfile(
                    company_name=f"Competitor {i}",
                    main_services=["Service A"],
                    strengths=["Strong CTA"],
                    weaknesses=["Poor mobile"],
                    trust_signals=["10+ years"],
                    navigation_structure="simple",
                    content_quality="good",
                    has_faq=(i == 0),
                ),
            )
            entries.append(e)
        return entries

    def test_contains_competitor_names(self, analyzer):
        entries = self._make_entries()
        ctx = _ClientContext(company_name="Client Inc", industry="Roofing")
        prompt = analyzer._build_comparison_prompt(ctx, entries)
        assert "Competitor 0" in prompt
        assert "Competitor 1" in prompt

    def test_contains_client_name(self, analyzer):
        entries = self._make_entries()
        ctx = _ClientContext(company_name="Client Inc", industry="Roofing")
        prompt = analyzer._build_comparison_prompt(ctx, entries)
        assert "Client Inc" in prompt

    def test_contains_comparison_schema(self, analyzer):
        entries = self._make_entries()
        prompt = analyzer._build_comparison_prompt(None, entries)
        assert "navigation_ideas" in prompt
        assert "trust_building_elements" in prompt
        assert "cta_ideas" in prompt

    def test_no_copy_instruction_present(self, analyzer):
        entries = self._make_entries()
        prompt = analyzer._build_comparison_prompt(None, entries)
        assert "copying" in prompt.lower() or "not suggest" in prompt.lower()

    def test_no_client_context_shows_placeholder(self, analyzer):
        entries = self._make_entries()
        prompt = analyzer._build_comparison_prompt(None, entries)
        assert "No client profile" in prompt


# ── _parse_ai_json() ──────────────────────────────────────────────────────────

class TestParseAiJson:
    def test_valid_raw_json(self, analyzer):
        data = analyzer._parse_ai_json('{"company_name": "Acme"}')
        assert data["company_name"] == "Acme"

    def test_code_fenced_json(self, analyzer):
        text = '```json\n{"company_name": "Acme"}\n```'
        data = analyzer._parse_ai_json(text)
        assert data["company_name"] == "Acme"

    def test_code_fenced_no_lang(self, analyzer):
        text = '```\n{"company_name": "Acme"}\n```'
        data = analyzer._parse_ai_json(text)
        assert data["company_name"] == "Acme"

    def test_json_embedded_in_prose(self, analyzer):
        text = 'Here is the result: {"company_name": "Acme"} That is all.'
        data = analyzer._parse_ai_json(text)
        assert data.get("company_name") == "Acme"

    def test_invalid_json_returns_empty(self, analyzer):
        data = analyzer._parse_ai_json("This is not JSON at all.")
        assert data == {}

    def test_empty_string_returns_empty(self, analyzer):
        data = analyzer._parse_ai_json("")
        assert data == {}

    def test_nested_structure_preserved(self, analyzer):
        data = analyzer._parse_ai_json('{"strengths": ["a", "b"]}')
        assert data["strengths"] == ["a", "b"]


# ── _dict_to_competitor_profile() ────────────────────────────────────────────

class TestDictToCompetitorProfile:
    def test_parses_all_list_fields(self, analyzer):
        raw = {
            "main_services": ["A", "B"],
            "trust_signals": ["Cert"],
            "strengths":     ["Clear nav"],
        }
        profile = analyzer._dict_to_competitor_profile(raw)
        assert profile.main_services == ["A", "B"]
        assert profile.trust_signals == ["Cert"]
        assert profile.strengths == ["Clear nav"]

    def test_coerces_string_to_single_item_list(self, analyzer):
        profile = analyzer._dict_to_competitor_profile({"main_services": "Roofing"})
        assert profile.main_services == ["Roofing"]

    def test_none_list_field_becomes_empty(self, analyzer):
        profile = analyzer._dict_to_competitor_profile({"main_services": None})
        assert profile.main_services == []

    def test_has_faq_coerced_to_bool(self, analyzer):
        assert analyzer._dict_to_competitor_profile({"has_faq": "true"}).has_faq is True
        assert analyzer._dict_to_competitor_profile({"has_faq": 0}).has_faq is False

    def test_unknown_keys_ignored(self, analyzer):
        profile = analyzer._dict_to_competitor_profile({
            "company_name": "Test",
            "totally_unknown_field": "ignored",
        })
        assert profile.company_name == "Test"

    def test_empty_dict_gives_defaults(self, analyzer):
        profile = analyzer._dict_to_competitor_profile({})
        assert profile.company_name == ""
        assert profile.main_services == []

    def test_empty_strings_in_list_filtered(self, analyzer):
        profile = analyzer._dict_to_competitor_profile(
            {"strengths": ["Good", "", "  ", "Fast"]}
        )
        assert "" not in profile.strengths
        assert "Good" in profile.strengths
        assert "Fast" in profile.strengths


# ── _merge_comparison_report() ────────────────────────────────────────────────

class TestMergeComparisonReport:
    def test_merges_list_fields(self, analyzer):
        base = _ComparisonReport(
            client_url="https://client.com",
            analyzed_at="2024-01-01",
            competitors_analyzed=2,
        )
        ai_data = {
            "navigation_ideas": ["Sticky header"],
            "cta_ideas": ["Floating button"],
            "overall_opportunities": ["Add FAQ"],
        }
        report = analyzer._merge_comparison_report(base, ai_data)
        assert report.navigation_ideas == ["Sticky header"]
        assert report.cta_ideas == ["Floating button"]
        assert report.overall_opportunities == ["Add FAQ"]

    def test_preserves_metadata(self, analyzer):
        base = _ComparisonReport(
            client_url="https://client.com",
            analyzed_at="2024-01-01T12:00:00",
            competitors_analyzed=3,
            errors=["some error"],
        )
        report = analyzer._merge_comparison_report(base, {})
        assert report.client_url == "https://client.com"
        assert report.competitors_analyzed == 3
        assert report.errors == ["some error"]

    def test_string_value_converted_to_list(self, analyzer):
        base = _ComparisonReport()
        report = analyzer._merge_comparison_report(
            base, {"navigation_ideas": "Use a hamburger menu"}
        )
        assert isinstance(report.navigation_ideas, list)
        assert len(report.navigation_ideas) == 1

    def test_none_value_yields_empty_list(self, analyzer):
        base = _ComparisonReport()
        report = analyzer._merge_comparison_report(
            base, {"navigation_ideas": None}
        )
        assert report.navigation_ideas == []


# ── identify_content_gaps() ───────────────────────────────────────────────────

class TestIdentifyContentGaps:
    def test_finds_gaps_in_competitor_strengths(self, analyzer):
        business = BusinessInfo(name="Client", services=["Roof repair"])
        comp = CompetitorInfo(
            url="https://comp.example.com",
            name="Competitor",
            strengths=["Online booking system", "Live chat support"],
        )
        gaps = analyzer.identify_content_gaps(business, [comp])
        assert len(gaps) >= 1
        assert any("Competitor" in g or "comp.example.com" in g for g in gaps)

    def test_no_gaps_when_empty_competitors(self, analyzer):
        business = BusinessInfo(name="Client", services=["Roofing"])
        gaps = analyzer.identify_content_gaps(business, [])
        assert gaps == []

    def test_deduplicates_gaps(self, analyzer):
        business = BusinessInfo(name="Client", services=["Repair"])
        comp1 = CompetitorInfo(url="https://c1.com", strengths=["Online booking"])
        comp2 = CompetitorInfo(url="https://c2.com", strengths=["Online booking"])
        gaps = analyzer.identify_content_gaps(business, [comp1, comp2])
        lower_gaps = [g.lower() for g in gaps]
        booking_count = sum(1 for g in lower_gaps if "online booking" in g)
        assert booking_count == 1

    def test_includes_competitor_keywords_as_gaps(self, analyzer):
        business = BusinessInfo(name="Client", services=["Roof repair"])
        comp = CompetitorInfo(url="https://c.com", keywords=["solar panels", "gutters"])
        gaps = analyzer.identify_content_gaps(business, [comp])
        assert any("solar panels" in g.lower() or "gutters" in g.lower() for g in gaps)


# ── generate_recommendations() ────────────────────────────────────────────────

class TestGenerateRecommendations:
    def test_returns_list(self, analyzer):
        business = BusinessInfo(name="Client", services=["Repair"])
        comp = CompetitorInfo(url="https://c.com", strengths=["Fast"])
        recs = analyzer.generate_recommendations(business, [comp], gaps=["Gap 1"])
        assert isinstance(recs, list)

    def test_includes_gaps(self, analyzer):
        business = BusinessInfo(name="Client")
        recs = analyzer.generate_recommendations(
            business, [], gaps=["Add FAQ section"]
        )
        assert "Add FAQ section" in recs

    def test_includes_competitor_weaknesses_as_opportunities(self, analyzer):
        business = BusinessInfo(name="Client")
        comp = CompetitorInfo(
            url="https://c.com",
            weaknesses=["No mobile site", "Outdated design"],
        )
        recs = analyzer.generate_recommendations(business, [comp], gaps=[])
        text = " ".join(recs).lower()
        assert "mobile site" in text or "outdated design" in text

    def test_deduplicates(self, analyzer):
        business = BusinessInfo(name="Client")
        comp = CompetitorInfo(url="https://c.com", weaknesses=["Slow speed"])
        recs1 = analyzer.generate_recommendations(
            business, [comp], gaps=["Slow speed opportunity"]
        )
        # The same weakness should not appear as two separate duplicates
        lower = [r.lower() for r in recs1]
        assert len(lower) == len(set(lower))


# ── _save_outputs() ───────────────────────────────────────────────────────────

class TestSaveOutputs:
    def _make_entries(self):
        return [
            _CompetitorEntry(
                url="https://comp1.example.com",
                crawled=True,
                project_dir="/projects/comp1",
                profile=_CompetitorProfile(
                    company_name="Comp 1",
                    main_services=["Service A"],
                    strengths=["Fast"],
                    content_quality="good",
                    has_faq=True,
                ),
                analyzed_at="2024-01-01T00:00:00",
            ),
        ]

    def test_writes_competitors_json(self, tmp_path, analyzer):
        proj = tmp_path / "client_project"
        entries = self._make_entries()
        report = _ComparisonReport(competitors_analyzed=1)
        analyzer._save_outputs(proj, entries, report)
        path = proj / "json" / "competitors.json"
        assert path.exists()
        data = json.loads(path.read_text())
        from webmaker.core.schema import unwrap_json
        data = unwrap_json(data)
        assert isinstance(data, list)
        assert data[0]["company_name"] == "Comp 1"

    def test_writes_competitor_analysis_json(self, tmp_path, analyzer):
        proj = tmp_path / "client_project"
        entries = self._make_entries()
        report = _ComparisonReport()
        analyzer._save_outputs(proj, entries, report)
        path = proj / "json" / "competitor_analysis.json"
        assert path.exists()
        data = json.loads(path.read_text())
        from webmaker.core.schema import unwrap_json
        data = unwrap_json(data)
        assert data[0]["profile"]["main_services"] == ["Service A"]

    def test_writes_comparison_report_json(self, tmp_path, analyzer):
        proj = tmp_path / "client_project"
        entries = self._make_entries()
        report = _ComparisonReport(
            overall_opportunities=["Add FAQ", "Add booking"],
        )
        analyzer._save_outputs(proj, entries, report)
        path = proj / "json" / "comparison_report.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert "Add FAQ" in data["overall_opportunities"]

    def test_writes_per_competitor_json(self, tmp_path, analyzer):
        proj = tmp_path / "client_project"
        entries = self._make_entries()
        report = _ComparisonReport()
        analyzer._save_outputs(proj, entries, report)
        comp_dir = proj / "json" / "competitors"
        slugged = list(comp_dir.glob("*.json"))
        assert len(slugged) >= 1


# ── _load_client_context() ────────────────────────────────────────────────────

class TestLoadClientContext:
    def test_loads_valid_profile(self, tmp_path, analyzer):
        d = tmp_path / "proj" / "json"
        d.mkdir(parents=True)
        profile = {"company_name": "My Firm", "industry": "Roofing",
                   "main_services": ["A", "B"]}
        (d / "business_profile.json").write_text(json.dumps(profile))
        ctx = analyzer._load_client_context(tmp_path / "proj")
        assert ctx is not None
        assert ctx.company_name == "My Firm"
        assert ctx.industry == "Roofing"
        assert ctx.main_services == ["A", "B"]

    def test_missing_file_returns_none(self, tmp_path, analyzer):
        d = tmp_path / "empty_proj"
        d.mkdir()
        ctx = analyzer._load_client_context(d)
        assert ctx is None

    def test_malformed_json_returns_none(self, tmp_path, analyzer):
        d = tmp_path / "bad_proj" / "json"
        d.mkdir(parents=True)
        (d / "business_profile.json").write_text("not json!", encoding="utf-8")
        ctx = analyzer._load_client_context(tmp_path / "bad_proj")
        assert ctx is None

    def test_missing_fields_default_to_empty(self, tmp_path, analyzer):
        d = tmp_path / "partial_proj" / "json"
        d.mkdir(parents=True)
        (d / "business_profile.json").write_text(
            json.dumps({"company_name": "Firm"}), encoding="utf-8"
        )
        ctx = analyzer._load_client_context(tmp_path / "partial_proj")
        assert ctx is not None
        assert ctx.industry == ""
        assert ctx.main_services == []


# ── _normalise_url() ─────────────────────────────────────────────────────────

class TestNormaliseUrl:
    def test_valid_https(self):
        out = CompetitorAnalyzer._normalise_url("https://example.com/path")
        assert out == "https://example.com/path"

    def test_valid_http(self):
        out = CompetitorAnalyzer._normalise_url("http://example.com")
        assert out == "http://example.com/"

    def test_strips_fragment(self):
        out = CompetitorAnalyzer._normalise_url("https://example.com/page#section")
        assert "#" not in out

    def test_strips_trailing_slash_non_root(self):
        out = CompetitorAnalyzer._normalise_url("https://example.com/path/")
        assert out == "https://example.com/path"

    def test_ftp_returns_empty(self):
        assert CompetitorAnalyzer._normalise_url("ftp://example.com") == ""

    def test_no_scheme_returns_empty(self):
        assert CompetitorAnalyzer._normalise_url("example.com") == ""

    def test_empty_string_returns_empty(self):
        assert CompetitorAnalyzer._normalise_url("") == ""


# ── _url_to_slug() ───────────────────────────────────────────────────────────

class TestUrlToSlug:
    def test_standard_url(self):
        slug = CompetitorAnalyzer._url_to_slug("https://my-firm.example.com/path")
        assert slug == "my-firm-example-com"

    def test_strips_www(self):
        slug = CompetitorAnalyzer._url_to_slug("https://www.example.com")
        assert "www" not in slug

    def test_no_consecutive_hyphens(self):
        slug = CompetitorAnalyzer._url_to_slug("https://a..b.com")
        assert "--" not in slug

    def test_invalid_url_returns_fallback(self):
        slug = CompetitorAnalyzer._url_to_slug("not-a-url")
        assert isinstance(slug, str)
        assert len(slug) > 0


# ── analyze_from_urls() integration (mocked I/O) ──────────────────────────────

class TestAnalyzeFromUrls:
    def test_returns_analysis_result(self, test_settings, mock_router_ok, mock_crawler,
                                     project_dir):
        ca = CompetitorAnalyzer(test_settings,
                                ai_router=mock_router_ok, crawler=mock_crawler)
        result = ca.analyze_from_urls(
            ["https://competitor.example.com"], project_dir
        )
        assert isinstance(result, AnalysisResult)

    def test_profiles_each_url(self, test_settings, mock_router_ok, mock_crawler,
                               project_dir):
        ca = CompetitorAnalyzer(test_settings,
                                ai_router=mock_router_ok, crawler=mock_crawler)
        ca.analyze_from_urls(
            ["https://c1.example.com", "https://c2.example.com"], project_dir
        )
        assert mock_crawler.crawl.call_count == 2

    def test_respects_max_competitors(self, test_settings, mock_router_ok, mock_crawler,
                                      project_dir):
        ca = CompetitorAnalyzer(test_settings, max_competitors=1,
                                ai_router=mock_router_ok, crawler=mock_crawler)
        ca.analyze_from_urls(
            ["https://c1.example.com", "https://c2.example.com", "https://c3.example.com"],
            project_dir,
        )
        assert mock_crawler.crawl.call_count == 1

    def test_continues_after_crawler_failure(self, test_settings, mock_router_ok,
                                             mock_crawler, project_dir):
        call_count = {"n": 0}

        def crawl_side_effect(url):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise CrawlerError("Site down")
            return CrawlResult(
                target_url=url,
                pages=[
                    PageData(url=url, title="Home", page_type=PageType.HOME)
                ],
                total_pages=1,
            )

        mock_crawler.crawl.side_effect = crawl_side_effect
        ca = CompetitorAnalyzer(test_settings,
                                ai_router=mock_router_ok, crawler=mock_crawler)
        result = ca.analyze_from_urls(
            ["https://broken.com", "https://ok.example.com"], project_dir
        )
        assert isinstance(result, AnalysisResult)
        assert mock_crawler.crawl.call_count == 2

    def test_writes_json_files(self, test_settings, mock_router_ok, mock_crawler,
                               project_dir):
        ca = CompetitorAnalyzer(test_settings,
                                ai_router=mock_router_ok, crawler=mock_crawler)
        ca.analyze_from_urls(
            ["https://competitor.example.com"], project_dir
        )
        assert (project_dir / "json" / "competitors.json").exists()
        assert (project_dir / "json" / "competitor_analysis.json").exists()

    def test_no_ai_providers_still_returns_result(self, test_settings, mock_router,
                                                   mock_crawler, project_dir):
        mock_router.available_providers.return_value = []
        ca = CompetitorAnalyzer(test_settings,
                                ai_router=mock_router, crawler=mock_crawler)
        result = ca.analyze_from_urls(
            ["https://competitor.example.com"], project_dir
        )
        assert isinstance(result, AnalysisResult)

    def test_no_client_context_does_not_raise(self, test_settings, mock_router_ok,
                                               mock_crawler, tmp_path):
        empty_dir = tmp_path / "empty_project"
        empty_dir.mkdir()
        ca = CompetitorAnalyzer(test_settings,
                                ai_router=mock_router_ok, crawler=mock_crawler)
        result = ca.analyze_from_urls(
            ["https://competitor.example.com"], empty_dir
        )
        assert isinstance(result, AnalysisResult)

    def test_empty_url_list_returns_empty_result(self, test_settings, mock_router_ok,
                                                  mock_crawler, project_dir):
        ca = CompetitorAnalyzer(test_settings,
                                ai_router=mock_router_ok, crawler=mock_crawler)
        result = ca.analyze_from_urls([], project_dir)
        assert isinstance(result, AnalysisResult)
        assert result.competitors == []

    def test_result_contains_competitors(self, test_settings, mock_router_ok,
                                          mock_crawler, project_dir):
        ca = CompetitorAnalyzer(test_settings,
                                ai_router=mock_router_ok, crawler=mock_crawler)
        result = ca.analyze_from_urls(
            ["https://competitor.example.com"], project_dir
        )
        assert len(result.competitors) == 1
        # _normalise_url adds a trailing slash to bare root URLs
        assert result.competitors[0].url in (
            "https://competitor.example.com",
            "https://competitor.example.com/",
        )
