"""
tests/unit/test_qa_reviewer.py
===============================
Unit tests for QAReviewer.

All HTTP requests and AI provider calls are mocked — no live WordPress
or network access is required.

Coverage:
  - Initialization
  - Deterministic business / content / SEO / accessibility / structure / conversion checks
  - Scoring (category scores, overall, pass threshold)
  - AI prompt generation and JSON parsing
  - DeepSeek review (available / unavailable / NotImplementedError)
  - Claude second opinion
  - Report file generation (qa_report, seo_review, content_review, website_score)
  - Live check methods with mocked HTTP
  - calculate_score
  - review() / review_from_directory() pipelines
  - Error handling (missing files, malformed JSON, AI failures)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from webmaker.config.settings import Settings
from webmaker.core.exceptions import AIError, QAError
from webmaker.core.types import (
    AIProvider, GenerationResult, QACheck, QAReport,
)
from webmaker.modules.qa_reviewer import (
    QAIssue,
    QAReviewer,
    WebsiteScore,
    _PASS_THRESHOLD,
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
    r = MagicMock()
    r.is_available.return_value = False
    r.available_providers.return_value = []
    r.complete.side_effect = NotImplementedError("not yet")
    return r


@pytest.fixture
def mock_router_deepseek(mock_router):
    review = {
        "strengths": ["Clear homepage hero", "Contact page present"],
        "weaknesses": ["FAQ answers incomplete"],
        "inconsistencies": [],
        "missing_information": ["Pricing details"],
        "issues": [{
            "category": "content",
            "severity": "medium",
            "affected_page": "faq",
            "description": "FAQ has placeholder answers",
            "suggested_improvement": "Replace placeholders with verified facts",
        }],
        "recommendations": ["Complete FAQ answers", "Add trust badges"],
        "overall_assessment": "Solid structure with content gaps.",
    }

    def is_available(provider):
        return provider == AIProvider.DEEPSEEK

    mock_router.is_available.side_effect = is_available
    mock_router.complete.side_effect = None
    mock_router.complete.return_value = json.dumps(review)
    return mock_router


@pytest.fixture
def reviewer(test_settings, mock_router):
    return QAReviewer(test_settings, ai_router=mock_router)


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "projects" / "client-demo"
    json_dir = d / "json"
    json_dir.mkdir(parents=True)

    (json_dir / "business_profile.json").write_text(json.dumps({
        "company_name":  "Dachprofi Berlin",
        "industry":      "Roofing",
        "main_services": ["Dachreparatur", "Neue Dächer"],
        "service_areas": ["Berlin"],
        "contact_phone": "+49 30 111",
        "contact_email": "info@dachprofi.de",
        "trust_signals": ["15 Jahre Erfahrung"],
        "brand_tone":    "professional",
    }), encoding="utf-8")

    (json_dir / "optimized_homepage.json").write_text(json.dumps({
        "meta_title": "Dachprofi Berlin – Dachdecker",
        "meta_description": "Professionelle Dacharbeiten in Berlin.",
        "hero": {
            "heading": "Dachprofi Berlin – Ihr Dach in besten Händen",
            "subheading": "Schnell und zuverlässig",
            "cta_primary": "Jetzt anfragen",
        },
        "intro": "Dachprofi Berlin bietet professionelle Dacharbeiten.",
        "services_overview": {
            "heading": "Leistungen",
            "services": [
                {"name": "Dachreparatur", "short_description": "Schnelle Reparaturen."},
            ],
        },
        "why_choose_us": {
            "heading": "Warum wir",
            "points": [{"heading": "Erfahrung", "text": "15 Jahre."}],
        },
        "cta_section": {
            "heading": "Kontakt",
            "text": "Rufen Sie uns an.",
            "cta_button": "Kontakt",
        },
    }), encoding="utf-8")

    (json_dir / "optimized_about.json").write_text(json.dumps({
        "meta_title": "Über uns – Dachprofi",
        "meta_description": "Über Dachprofi Berlin.",
        "hero_heading": "Über Dachprofi Berlin",
        "company_story": "Wir sind ein Familienbetrieb aus Berlin mit langer Tradition.",
        "mission_statement": "Qualität zuerst.",
        "intro": "Lernen Sie unser Team kennen und erfahren Sie mehr über unsere Werte.",
    }), encoding="utf-8")

    (json_dir / "optimized_services.json").write_text(json.dumps({
        "meta_title": "Leistungen – Dachprofi",
        "meta_description": "Unsere Dachleistungen in Berlin.",
        "hero_heading": "Unsere Leistungen",
        "intro": "Dachreparatur und Neue Dächer für Berlin und Umgebung.",
        "services": [
            {
                "name": "Dachreparatur",
                "description": "Wir reparieren Ihr Dach schnell und fachgerecht.",
                "benefits": ["Schnell", "Zuverlässig"],
            },
            {
                "name": "Neue Dächer",
                "description": "Komplette Neueindeckungen nach aktuellen Standards.",
            },
        ],
    }), encoding="utf-8")

    (json_dir / "optimized_contact.json").write_text(json.dumps({
        "meta_title": "Kontakt – Dachprofi",
        "meta_description": "Kontaktieren Sie Dachprofi Berlin.",
        "hero_heading": "Kontakt",
        "intro": "Wir freuen uns auf Ihre Nachricht unter +49 30 111.",
        "contact_section": {
            "heading": "Schreiben Sie uns",
            "text": "Antwort innerhalb von 24 Stunden.",
            "form_cta": "Absenden",
        },
    }), encoding="utf-8")

    (json_dir / "optimized_faq.json").write_text(json.dumps({
        "meta_title": "FAQ – Dachprofi",
        "meta_description": "Häufige Fragen zu Dacharbeiten.",
        "hero_heading": "FAQ",
        "intro": "Antworten auf die häufigsten Fragen unserer Kunden in Berlin.",
        "faqs": [
            {"question": "Wie schnell kommen Sie?", "answer": "In der Regel innerhalb von 48 Stunden."},
        ],
    }), encoding="utf-8")

    (json_dir / "meta_data.json").write_text(json.dumps({
        "homepage": {"title": "Dachprofi Berlin", "description": "Dachdecker Berlin"},
        "about":    {"title": "Über uns", "description": "Unsere Geschichte"},
        "services": {"title": "Leistungen", "description": "Was wir anbieten"},
        "contact":  {"title": "Kontakt", "description": "Erreichen Sie uns"},
        "faq":      {"title": "FAQ", "description": "Fragen und Antworten"},
    }), encoding="utf-8")

    (json_dir / "generation_report.json").write_text(json.dumps({
        "site_title": "Dachprofi Berlin",
        "pages_created": [
            {"slug": "home", "title": "Home", "post_id": 1},
            {"slug": "about", "title": "About", "post_id": 2},
            {"slug": "services", "title": "Services", "post_id": 3},
            {"slug": "contact", "title": "Contact", "post_id": 4},
            {"slug": "faq", "title": "FAQ", "post_id": 5},
        ],
        "images_imported": [{"attachment_id": 10, "filename": "hero.jpg"}],
        "menu_created": True,
        "menu_items": ["Home", "About", "Services", "Contact", "FAQ"],
        "seo_applied": [{"post_id": 1, "slug": "homepage", "title": "Dachprofi Berlin"}],
        "homepage_id": 1,
        "errors": [],
        "warnings": [],
        "success": True,
    }), encoding="utf-8")

    (json_dir / "images.json").write_text(json.dumps([
        {"filename": "hero.jpg", "alt_text": "Dach Hero", "local_path": "/tmp/hero.jpg"},
        {"filename": "team.jpg", "alt_text": "", "local_path": "/tmp/team.jpg"},
    ]), encoding="utf-8")

    return d


# ── Initialization ─────────────────────────────────────────────────────────────

class TestInit:
    def test_creates_router_if_not_provided(self, test_settings):
        with patch("webmaker.modules.qa_reviewer.AIRouter") as mock_cls:
            mock_cls.return_value = MagicMock()
            QAReviewer(test_settings)
            mock_cls.assert_called_once_with(test_settings)

    def test_uses_injected_router(self, test_settings, mock_router):
        qa = QAReviewer(test_settings, ai_router=mock_router)
        assert qa._ai_router is mock_router


# ── Deterministic checks ──────────────────────────────────────────────────────

class TestBusinessConsistency:
    def test_flags_missing_company_name(self, reviewer):
        issues = reviewer._check_business_consistency({}, {}, {})
        assert any(i.severity == "critical" and "Company name" in i.description
                   for i in issues)

    def test_flags_name_missing_from_homepage(self, reviewer):
        biz = {"company_name": "Acme", "main_services": ["A"], "service_areas": ["X"],
               "contact_phone": "123"}
        pages = {"homepage": {"intro": "Welcome to our company"}}
        issues = reviewer._check_business_consistency(biz, pages, {})
        assert any("not found in homepage" in i.description for i in issues)

    def test_clean_profile_few_issues(self, reviewer, project_dir):
        biz = json.loads((project_dir / "json" / "business_profile.json").read_text())
        pages = reviewer._load_optimized_pages(project_dir)
        gen = json.loads((project_dir / "json" / "generation_report.json").read_text())
        issues = reviewer._check_business_consistency(biz, pages, gen)
        critical = [i for i in issues if i.severity == "critical"]
        assert critical == []


class TestContentQuality:
    def test_detects_placeholders(self, reviewer):
        pages = {
            "homepage": {
                "hero": {"heading": "Hello"},
                "intro": "We are [MISSING INFORMATION] experts in the field of roofing services today.",
            },
            "contact": {"hero_heading": "Contact", "intro": "Call us for more information about our work."},
        }
        issues = reviewer._check_content_quality(pages)
        assert any("Placeholder" in i.description for i in issues)

    def test_flags_missing_homepage(self, reviewer):
        pages = {"contact": {"hero_heading": "C", "intro": "x" * 80}}
        issues = reviewer._check_content_quality(pages)
        assert any(i.affected_page == "homepage" and i.severity == "critical"
                   for i in issues)

    def test_flags_empty_page(self, reviewer):
        pages = {
            "homepage": {},
            "contact": {"hero_heading": "C", "intro": "Please contact us for a free consultation today."},
        }
        issues = reviewer._check_content_quality(pages)
        assert any("empty" in i.description.lower() for i in issues)


class TestSeoFromJson:
    def test_flags_missing_meta_title(self, reviewer):
        pages = {"about": {"hero_heading": "About", "intro": "About us story here with enough words."}}
        issues = reviewer._check_seo_from_json(pages, {})
        assert any("meta title" in i.description.lower() for i in issues)

    def test_flags_long_title(self, reviewer):
        pages = {
            "about": {
                "meta_title": "A" * 70,
                "meta_description": "Short desc",
                "hero_heading": "About",
            }
        }
        issues = reviewer._check_seo_from_json(pages, {})
        assert any("60 characters" in i.description for i in issues)

    def test_uses_meta_data_json(self, reviewer):
        pages = {"about": {"hero_heading": "About Us Page Heading"}}
        meta = {"about": {"title": "About Title", "description": "About description here"}}
        issues = reviewer._check_seo_from_json(pages, meta)
        assert not any("Missing meta title" in i.description for i in issues)


class TestAccessibility:
    def test_flags_many_missing_alts(self, reviewer):
        images = [{"alt_text": ""} for _ in range(5)]
        issues = reviewer._check_accessibility_from_json({}, images)
        assert any("alt text" in i.description.lower() for i in issues)

    def test_flags_click_here(self, reviewer):
        pages = {"homepage": {"intro": "Please click here to continue"}}
        issues = reviewer._check_accessibility_from_json(pages, [])
        assert any("click here" in i.description.lower() for i in issues)


class TestStructure:
    def test_flags_missing_generation_report(self, reviewer):
        issues = reviewer._check_wordpress_structure({}, {})
        assert any("generation_report" in i.description for i in issues)

    def test_flags_missing_menu(self, reviewer):
        gen = {"pages_created": [{"slug": "home"}, {"slug": "contact"}],
               "menu_created": False, "homepage_id": 1}
        issues = reviewer._check_wordpress_structure(gen, {})
        assert any("menu" in i.description.lower() for i in issues)

    def test_clean_generation_report(self, reviewer, project_dir):
        gen = json.loads((project_dir / "json" / "generation_report.json").read_text())
        pages = reviewer._load_optimized_pages(project_dir)
        issues = reviewer._check_wordpress_structure(gen, pages)
        critical = [i for i in issues if i.severity == "critical"]
        assert critical == []


class TestConversion:
    def test_flags_missing_cta(self, reviewer):
        pages = {
            "homepage": {"intro": "We are a roofing company in Berlin with many years of experience."},
            "contact": {"intro": "Contact"},
            "services": {"intro": "Services"},
            "faq": {"intro": "FAQ"},
        }
        issues = reviewer._check_conversion(pages, {})
        assert any("call-to-action" in i.description.lower() for i in issues)

    def test_flags_missing_contact_page(self, reviewer):
        pages = {"homepage": {"hero": {"cta_primary": "Call"}, "cta_section": {"heading": "Go"}}}
        issues = reviewer._check_conversion(pages, {})
        assert any(i.affected_page == "contact" and i.severity == "critical"
                   for i in issues)


# ── Scoring ───────────────────────────────────────────────────────────────────

class TestScoring:
    def test_calculate_score_empty(self, reviewer):
        assert reviewer.calculate_score([]) == 0.0

    def test_calculate_score_average(self, reviewer):
        checks = [
            QACheck(name="a", passed=True, score=1.0),
            QACheck(name="b", passed=False, score=0.0),
        ]
        assert reviewer.calculate_score(checks) == 0.5

    def test_compute_scores_returns_website_score(self, reviewer, project_dir):
        pages = reviewer._load_optimized_pages(project_dir)
        biz = json.loads((project_dir / "json" / "business_profile.json").read_text())
        gen = json.loads((project_dir / "json" / "generation_report.json").read_text())
        issues = reviewer._check_business_consistency(biz, pages, gen)
        scores = reviewer._compute_scores(issues, [], pages, gen, biz)
        assert isinstance(scores, WebsiteScore)
        assert 0 <= scores.overall_website_quality <= 100
        assert scores.explanations.get("overall")

    def test_critical_issues_lower_score(self, reviewer):
        bad_issues = [
            QAIssue(category="content", severity="critical",
                    description="empty", suggested_improvement="fix"),
            QAIssue(category="content", severity="critical",
                    description="empty2", suggested_improvement="fix"),
        ]
        good_scores = reviewer._compute_scores([], [], {"homepage": {}}, {}, {})
        bad_scores = reviewer._compute_scores(bad_issues, [], {"homepage": {}}, {}, {})
        assert bad_scores.content_quality < good_scores.content_quality


# ── AI prompts / parsing ──────────────────────────────────────────────────────

class TestAiHelpers:
    def test_build_ai_review_prompt_contains_sections(self, reviewer):
        prompt = reviewer._build_ai_review_prompt(
            {"company_name": "Acme"},
            {"homepage": {"hero": {"heading": "Hi"}}},
            {"homepage": {"title": "T"}},
            {"menu_created": True},
            [],
        )
        assert "BUSINESS PROFILE" in prompt
        assert "Acme" in prompt
        assert "REQUIRED JSON RESPONSE" in prompt
        assert "Do NOT rewrite" in prompt

    def test_parse_raw_json(self, reviewer):
        data = reviewer._parse_ai_json('{"strengths": ["A"]}')
        assert data["strengths"] == ["A"]

    def test_parse_fenced_json(self, reviewer):
        data = reviewer._parse_ai_json('```json\n{"strengths": ["B"]}\n```')
        assert data["strengths"] == ["B"]

    def test_parse_invalid_returns_empty(self, reviewer):
        assert reviewer._parse_ai_json("not json") == {}

    def test_summarise_page_bounds_services(self, reviewer):
        content = {"services": [{"name": f"S{i}"} for i in range(10)], "intro": "Hi"}
        summary = reviewer._summarise_page(content)
        assert len(summary["services"]) == 5


# ── AI review calls ───────────────────────────────────────────────────────────

class TestAiReview:
    def test_raises_when_deepseek_unavailable(self, reviewer):
        with pytest.raises(AIError, match="DeepSeek"):
            reviewer._run_ai_review({}, {}, {}, {}, [])

    def test_returns_parsed_review(self, test_settings, mock_router_deepseek):
        qa = QAReviewer(test_settings, ai_router=mock_router_deepseek)
        result = qa._run_ai_review(
            {"company_name": "X"},
            {"homepage": {"intro": "Hello"}},
            {},
            {},
            [],
        )
        assert result["reviewer"] == "deepseek"
        assert "strengths" in result

    def test_handles_not_implemented(self, test_settings, mock_router):
        mock_router.is_available.side_effect = lambda p: p == AIProvider.DEEPSEEK
        mock_router.complete.side_effect = NotImplementedError("todo")
        qa = QAReviewer(test_settings, ai_router=mock_router)
        with pytest.raises(AIError, match="not yet implemented"):
            qa._run_ai_review({}, {}, {}, {}, [])


# ── Live HTTP checks (mocked) ─────────────────────────────────────────────────

class TestLiveChecks:
    def test_page_availability_ok(self, reviewer):
        reviewer._http_get = MagicMock(return_value=(200, "<html></html>", 0.1))
        checks = reviewer.check_page_availability("http://localhost:8080", ["home", "about"])
        assert len(checks) == 2
        assert all(c.passed for c in checks)

    def test_page_availability_fail(self, reviewer):
        reviewer._http_get = MagicMock(return_value=(404, "Nope", 0.1))
        checks = reviewer.check_page_availability("http://localhost:8080", ["missing"])
        assert checks[0].passed is False

    def test_seo_completeness(self, reviewer):
        html = (
            "<html><head><title>Test Co</title>"
            '<meta name="description" content="A description">'
            "</head><body><h1>Hello</h1></body></html>"
        )
        reviewer._http_get = MagicMock(return_value=(200, html, 0.05))
        checks = reviewer.check_seo_completeness("http://localhost:8080", ["home"])
        assert checks[0].passed is True
        assert checks[0].score == 1.0

    def test_seo_missing_fields(self, reviewer):
        reviewer._http_get = MagicMock(return_value=(200, "<html><body></body></html>", 0.05))
        checks = reviewer.check_seo_completeness("http://localhost:8080", ["home"])
        assert checks[0].passed is False
        assert "Missing" in checks[0].detail

    def test_image_alt_text(self, reviewer):
        html = '<img src="a.jpg" alt="ok"><img src="b.jpg"><img src="c.jpg" alt="">'
        reviewer._http_get = MagicMock(return_value=(200, html, 0.05))
        check = reviewer.check_image_alt_text("http://localhost:8080")
        assert check.passed is False
        assert "2/3" in check.detail

    def test_performance(self, reviewer):
        reviewer._http_get = MagicMock(return_value=(200, "x" * 1000, 0.2))
        check = reviewer.check_performance("http://localhost:8080")
        assert check.passed is True
        assert "TTFB" in check.detail

    def test_broken_links_none(self, reviewer):
        html = '<a href="/about/">About</a><a href="#top">Top</a><a href="mailto:a@b.c">Mail</a>'

        def http_get(url):
            return (200, html if url.rstrip("/").endswith(":8080") else "<ok>", 0.05)

        reviewer._http_get = MagicMock(side_effect=http_get)
        check = reviewer.check_broken_links("http://localhost:8080")
        assert check.name == "broken_links"
        assert check.passed is True


# ── review_from_directory pipeline ────────────────────────────────────────────

class TestReviewFromDirectory:
    def test_returns_qa_report(self, reviewer, project_dir):
        result = reviewer.review_from_directory(
            project_dir, skip_live_checks=True, skip_ai=True
        )
        assert isinstance(result, QAReport)
        assert result.wp_url
        assert isinstance(result.checks, list)

    def test_writes_all_report_files(self, reviewer, project_dir):
        reviewer.review_from_directory(
            project_dir, skip_live_checks=True, skip_ai=True
        )
        json_dir = project_dir / "json"
        assert (json_dir / "qa_report.json").exists()
        assert (json_dir / "seo_review.json").exists()
        assert (json_dir / "content_review.json").exists()
        assert (json_dir / "website_score.json").exists()

    def test_qa_report_json_structure(self, reviewer, project_dir):
        reviewer.review_from_directory(
            project_dir, skip_live_checks=True, skip_ai=True
        )
        data = json.loads((project_dir / "json" / "qa_report.json").read_text())
        assert "issues" in data
        assert "scores" in data
        assert "overall_score" in data
        assert "passed" in data
        for issue in data["issues"]:
            assert "category" in issue
            assert "severity" in issue
            assert "affected_page" in issue
            assert "description" in issue
            assert "suggested_improvement" in issue

    def test_website_score_json(self, reviewer, project_dir):
        reviewer.review_from_directory(
            project_dir, skip_live_checks=True, skip_ai=True
        )
        data = json.loads((project_dir / "json" / "website_score.json").read_text())
        assert "overall_website_quality" in data
        assert "content_quality" in data
        assert "seo_quality" in data
        assert "accessibility" in data
        assert "conversion_readiness" in data
        assert "business_consistency" in data
        assert "explanations" in data

    def test_includes_ai_review_when_available(self, test_settings, mock_router_deepseek,
                                                 project_dir):
        qa = QAReviewer(test_settings, ai_router=mock_router_deepseek)
        qa.review_from_directory(project_dir, skip_live_checks=True)
        data = json.loads((project_dir / "json" / "qa_report.json").read_text())
        assert data["ai_review"].get("reviewer") == "deepseek"
        assert data["ai_review"].get("strengths")

    def test_continues_when_ai_fails(self, reviewer, project_dir):
        # mock_router raises NotImplementedError when somehow called;
        # skip_ai=False but DeepSeek unavailable → warning, still succeeds
        result = reviewer.review_from_directory(
            project_dir, skip_live_checks=True, skip_ai=False
        )
        assert isinstance(result, QAReport)
        data = json.loads((project_dir / "json" / "qa_report.json").read_text())
        assert any("AI review" in w for w in data.get("warnings", []))

    def test_handles_empty_project(self, reviewer, tmp_path):
        empty = tmp_path / "empty"
        (empty / "json").mkdir(parents=True)
        result = reviewer.review_from_directory(
            empty, skip_live_checks=True, skip_ai=True
        )
        assert isinstance(result, QAReport)
        assert (empty / "json" / "qa_report.json").exists()

    def test_handles_malformed_json(self, reviewer, tmp_path):
        d = tmp_path / "bad"
        (d / "json").mkdir(parents=True)
        (d / "json" / "business_profile.json").write_text("NOT JSON", encoding="utf-8")
        (d / "json" / "optimized_homepage.json").write_text(
            json.dumps({
                "meta_title": "T",
                "meta_description": "D",
                "hero": {"heading": "H", "cta_primary": "Go"},
                "intro": "Some longer intro text for the homepage content quality check here.",
                "cta_section": {"heading": "CTA"},
            }),
            encoding="utf-8",
        )
        (d / "json" / "optimized_contact.json").write_text(
            json.dumps({
                "meta_title": "C",
                "meta_description": "CD",
                "hero_heading": "Contact",
                "intro": "Please get in touch with our team for a free consultation.",
            }),
            encoding="utf-8",
        )
        result = reviewer.review_from_directory(
            d, skip_live_checks=True, skip_ai=True
        )
        assert isinstance(result, QAReport)

    def test_good_project_tends_to_pass(self, reviewer, project_dir):
        result = reviewer.review_from_directory(
            project_dir, skip_live_checks=True, skip_ai=True
        )
        assert result.overall_score >= _PASS_THRESHOLD
        assert result.passed is True


# ── review(GenerationResult) ──────────────────────────────────────────────────

class TestReviewGenerationResult:
    def test_live_only_when_no_project_dir(self, reviewer):
        reviewer._http_get = MagicMock(return_value=(
            200,
            '<html><head><title>T</title>'
            '<meta name="description" content="D"></head>'
            '<body><h1>H</h1><img alt="x" src="a.jpg"></body></html>',
            0.1,
        ))
        gen = GenerationResult(
            wp_url="http://localhost:8080",
            wp_path=Path("/tmp/wp"),
            pages_created=["home", "about"],
            success=True,
        )
        result = reviewer.review(gen)
        assert isinstance(result, QAReport)
        assert len(result.checks) >= 1

    def test_raises_without_url_or_project(self, reviewer):
        gen = GenerationResult(
            wp_url="",
            wp_path=Path("/tmp/wp"),
            pages_created=[],
            success=False,
        )
        with pytest.raises(QAError):
            reviewer.review(gen)


# ── Issue model / helpers ─────────────────────────────────────────────────────

class TestHelpers:
    def test_dict_to_issue(self, reviewer):
        issue = reviewer._dict_to_issue({
            "category": "seo",
            "severity": "high",
            "affected_page": "home",
            "description": "Missing title",
            "suggested_improvement": "Add title",
        })
        assert issue.category == "seo"
        assert issue.severity == "high"

    def test_issues_to_checks(self, reviewer):
        issues = [
            QAIssue(category="seo", severity="high", description="a"),
            QAIssue(category="seo", severity="low", description="b"),
            QAIssue(category="content", severity="critical", description="c"),
        ]
        checks = reviewer._issues_to_checks(issues)
        names = {c.name for c in checks}
        assert "deterministic:seo" in names
        assert "deterministic:content" in names

    def test_slugify(self, reviewer):
        assert reviewer._slugify("Hello World!") == "hello-world"
