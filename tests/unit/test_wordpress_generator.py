"""
tests/unit/test_wordpress_generator.py
=======================================
Unit tests for WordPressGenerator.

All WP-CLI / PHP / filesystem WordPress interactions are mocked —
no real WordPress instance or network is required.

Coverage:
  - Initialization
  - verify_installation() success and failure modes
  - generate_from_directory() full pipeline with mocked WP-CLI
  - create_page() create vs update paths
  - upload_media() success and missing file
  - set_seo_meta() writes expected meta keys
  - set_homepage()
  - install_theme() / install_plugin() refuse downloads
  - reset_wordpress()
  - HTML rendering for all page types
  - JSON loading (business, optimized pages, meta, images)
  - Menu creation
  - Media import
  - Generation report writing
  - Error handling (missing JSON, WP-CLI failures, missing images)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from webmaker.config.settings import Settings
from webmaker.core.exceptions import GenerationError, WordPressError
from webmaker.core.types import AnalysisResult, BusinessInfo, GenerationResult
from webmaker.modules.content_optimizer import PageContent
from webmaker.modules.wordpress_generator import (
    WordPressGenerator,
    GenerationReport,
    _PAGE_TITLES,
    _STANDARD_PAGES,
    _WP_SLUGS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def test_settings(tmp_path):
    php_dir = tmp_path / "bin" / "php"
    php_dir.mkdir(parents=True)
    (php_dir / "php.exe").write_text("")
    (php_dir / "php.ini").write_text("")
    wpcli = tmp_path / "bin" / "wp-cli.phar"
    wpcli.parent.mkdir(parents=True, exist_ok=True)
    wpcli.write_text("")
    wp_dir = tmp_path / "wordpress"
    wp_dir.mkdir()
    (wp_dir / "wp-config.php").write_text("<?php // test")

    return Settings(
        project_root  = tmp_path,
        logs_dir      = tmp_path / "logs",
        cache_dir     = tmp_path / "cache",
        projects_dir  = tmp_path / "projects",
        outputs_dir   = tmp_path / "outputs",
        assets_dir    = tmp_path / "assets",
        templates_dir = tmp_path / "templates",
        wordpress_dir = wp_dir,
        php_dir       = php_dir,
        wpcli_path    = wpcli,
        server_port   = 18080,
        db_port       = 13307,
    )


@pytest.fixture
def generator(test_settings):
    gen = WordPressGenerator(test_settings)
    # Default: WP-CLI succeeds with empty / sensible responses
    gen._wpcli = MagicMock(return_value="")
    return gen


@pytest.fixture
def project_dir(tmp_path):
    """Project directory with all expected JSON inputs."""
    d = tmp_path / "projects" / "client-demo"
    json_dir = d / "json"
    images_dir = d / "images"
    json_dir.mkdir(parents=True)
    images_dir.mkdir(parents=True)

    # Sample image file
    img = images_dir / "hero.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)  # minimal JPEG header

    (json_dir / "business_profile.json").write_text(json.dumps({
        "company_name":  "Dachprofi Berlin",
        "industry":      "Roofing",
        "main_services": ["Dachreparatur", "Neue Dächer"],
        "service_areas": ["Berlin"],
        "unique_value":  "15 Jahre Erfahrung",
        "languages":     ["de"],
        "contact_phone": "+49 30 111",
        "contact_email": "info@dachprofi.de",
    }), encoding="utf-8")

    (json_dir / "optimized_homepage.json").write_text(json.dumps({
        "meta_title": "Dachprofi Berlin – Dachdecker",
        "meta_description": "Professionelle Dacharbeiten in Berlin.",
        "hero": {
            "heading": "Ihr Dach in besten Händen",
            "subheading": "Schnell und zuverlässig",
            "cta_primary": "Jetzt anfragen",
            "cta_secondary": "Leistungen",
        },
        "intro": "Wir sind Dachdecker aus Berlin.",
        "services_overview": {
            "heading": "Unsere Leistungen",
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
        "hero_heading": "Über uns",
        "company_story": "Wir sind ein Familienbetrieb.",
        "mission_statement": "Qualität zuerst.",
        "values": [{"name": "Qualität", "description": "Beste Materialien."}],
        "team_intro": "Ein erfahrenes Team.",
    }), encoding="utf-8")

    (json_dir / "optimized_services.json").write_text(json.dumps({
        "meta_title": "Leistungen – Dachprofi",
        "meta_description": "Unsere Leistungen.",
        "hero_heading": "Leistungen",
        "intro": "Alles rund ums Dach.",
        "services": [
            {
                "name": "Dachreparatur",
                "slug": "dachreparatur",
                "heading": "Dachreparatur",
                "description": "Wir reparieren Ihr Dach.",
                "benefits": ["Schnell", "Günstig"],
                "process_steps": ["Anfrage", "Besichtigung", "Reparatur"],
                "cta": "Anfragen",
            },
        ],
    }), encoding="utf-8")

    (json_dir / "optimized_contact.json").write_text(json.dumps({
        "meta_title": "Kontakt – Dachprofi",
        "meta_description": "Kontaktieren Sie uns.",
        "hero_heading": "Kontakt",
        "intro": "Wir freuen uns auf Ihre Nachricht.",
        "contact_section": {
            "heading": "Schreiben Sie uns",
            "text": "Antwort innerhalb von 24 Stunden.",
            "form_cta": "Absenden",
        },
    }), encoding="utf-8")

    (json_dir / "optimized_faq.json").write_text(json.dumps({
        "meta_title": "FAQ – Dachprofi",
        "meta_description": "Häufige Fragen.",
        "hero_heading": "FAQ",
        "intro": "Antworten auf Ihre Fragen.",
        "faqs": [
            {"question": "Was kostet eine Reparatur?", "answer": "[MISSING INFORMATION]"},
        ],
    }), encoding="utf-8")

    (json_dir / "meta_data.json").write_text(json.dumps({
        "homepage": {"title": "Dachprofi Berlin", "description": "Dachdecker Berlin"},
        "about":    {"title": "Über uns", "description": "Unsere Geschichte"},
        "services": {"title": "Leistungen", "description": "Was wir anbieten"},
        "contact":  {"title": "Kontakt", "description": "Erreichen Sie uns"},
        "faq":      {"title": "FAQ", "description": "Fragen und Antworten"},
    }), encoding="utf-8")

    (json_dir / "images.json").write_text(json.dumps([
        {
            "filename":   "hero.jpg",
            "source_url": "https://example.com/hero.jpg",
            "alt_text":   "Dach Hero",
            "local_path": str(img),
            "width": 800,
            "height": 600,
        },
        {
            "filename":   "missing.png",
            "source_url": "https://example.com/missing.png",
            "alt_text":   "Gone",
            "local_path": str(images_dir / "does-not-exist.png"),
        },
    ]), encoding="utf-8")

    return d


def _wpcli_side_effect(*args, **kwargs):
    """Sensible default WP-CLI mock responses by command."""
    cmd = args[0] if args else ""
    if cmd == "core" and "is-installed" in args:
        return ""
    if cmd == "post" and "create" in args:
        return "101\n"
    if cmd == "post" and "list" in args:
        if "--format=ids" in args or "--field=ID" in args:
            return ""
        return ""
    if cmd == "media" and "import" in args:
        return "201\n"
    if cmd == "menu" and "create" in args:
        return ""
    if cmd == "menu" and "list" in args:
        return "[]"
    if cmd == "theme" and "list" in args:
        return json.dumps([
            {"name": "twentytwentyfour", "status": "inactive"},
            {"name": "twentytwentythree", "status": "active"},
        ])
    if cmd == "plugin" and "list" in args:
        return "akismet\n"
    if cmd == "option":
        return "Success"
    return ""


# ── Initialization ─────────────────────────────────────────────────────────────

class TestInit:
    def test_uses_settings_paths(self, test_settings):
        gen = WordPressGenerator(test_settings)
        assert gen._wp_path == test_settings.wordpress_dir
        assert gen._wpcli_path == test_settings.wpcli_path

    def test_path_overrides(self, test_settings, tmp_path):
        wp = tmp_path / "custom-wp"
        wp.mkdir()
        cli = tmp_path / "custom-cli.phar"
        cli.write_text("")
        gen = WordPressGenerator(test_settings, wp_path=wp, wpcli_path=cli)
        assert gen._wp_path == wp
        assert gen._wpcli_path == cli


# ── verify_installation() ─────────────────────────────────────────────────────

class TestVerifyInstallation:
    def test_passes_when_all_present(self, generator):
        generator._wpcli = MagicMock(return_value="")
        generator.verify_installation()  # should not raise

    def test_fails_when_wp_dir_missing(self, test_settings, tmp_path):
        gen = WordPressGenerator(test_settings, wp_path=tmp_path / "nope")
        with pytest.raises(WordPressError, match="not found"):
            gen.verify_installation()

    def test_fails_when_wp_config_missing(self, test_settings, tmp_path):
        empty = tmp_path / "empty-wp"
        empty.mkdir()
        gen = WordPressGenerator(test_settings, wp_path=empty)
        with pytest.raises(WordPressError, match="wp-config"):
            gen.verify_installation()

    def test_fails_when_core_not_installed(self, generator):
        generator._wpcli = MagicMock(
            side_effect=WordPressError("not installed")
        )
        with pytest.raises(WordPressError, match="not installed"):
            generator.verify_installation()


# ── create_page() ─────────────────────────────────────────────────────────────

class TestCreatePage:
    def test_creates_new_page(self, generator):
        generator._wpcli = MagicMock(side_effect=lambda *a, **k: (
            "" if a[0] == "post" and "list" in a else "42\n"
        ))
        pc = PageContent(
            slug="about",
            title="About Us",
            body_html="<p>Hello</p>",
            meta_title="About",
            meta_description="About page",
        )
        page_id = generator.create_page("about", pc)
        assert page_id == 42
        # Verify post create was called
        calls = [c.args for c in generator._wpcli.call_args_list]
        assert any(c and c[0] == "post" and "create" in c for c in calls)

    def test_updates_existing_page(self, generator):
        def side_effect(*args, **kwargs):
            if args[0] == "post" and "list" in args:
                return "55"
            return ""

        generator._wpcli = MagicMock(side_effect=side_effect)
        pc = PageContent(slug="about", title="About", body_html="<p>Hi</p>")
        page_id = generator.create_page("about", pc)
        assert page_id == 55
        calls = [c.args for c in generator._wpcli.call_args_list]
        assert any(c and c[0] == "post" and "update" in c for c in calls)


# ── upload_media() ────────────────────────────────────────────────────────────

class TestUploadMedia:
    def test_uploads_existing_file(self, generator, tmp_path):
        img = tmp_path / "logo.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)
        generator._wpcli = MagicMock(return_value="99\n")
        mid = generator.upload_media(img, title="Logo")
        assert mid == 99
        args = generator._wpcli.call_args.args
        assert args[0] == "media"
        assert "import" in args

    def test_raises_on_missing_file(self, generator, tmp_path):
        with pytest.raises(WordPressError, match="not found"):
            generator.upload_media(tmp_path / "nope.jpg")


# ── set_seo_meta() ────────────────────────────────────────────────────────────

class TestSetSeoMeta:
    def test_writes_meta_keys(self, generator):
        generator._wpcli = MagicMock(return_value="")
        generator.set_seo_meta(10, "SEO Title", "SEO Description")
        calls = [c.args for c in generator._wpcli.call_args_list]
        meta_calls = [c for c in calls if "meta" in c]
        assert len(meta_calls) >= 2
        # Check keys appear
        flat = " ".join(" ".join(map(str, c)) for c in calls)
        assert "_webmaker_seo_title" in flat
        assert "_webmaker_seo_description" in flat

    def test_handles_partial_failures(self, generator):
        call_n = {"n": 0}

        def side_effect(*args, **kwargs):
            call_n["n"] += 1
            if call_n["n"] == 1:
                raise WordPressError("fail once")
            return ""

        generator._wpcli = MagicMock(side_effect=side_effect)
        # Should not raise if some keys succeed
        generator.set_seo_meta(10, "Title", "Desc")


# ── set_homepage() ────────────────────────────────────────────────────────────

class TestSetHomepage:
    def test_updates_options(self, generator):
        generator._wpcli = MagicMock(return_value="")
        generator.set_homepage(7)
        calls = [c.args for c in generator._wpcli.call_args_list]
        assert any("show_on_front" in c for c in calls)
        assert any("page_on_front" in c for c in calls)


# ── install_theme / install_plugin ────────────────────────────────────────────

class TestInstallThemePlugin:
    def test_activates_installed_theme(self, generator):
        generator._wpcli = MagicMock(side_effect=lambda *a, **k: (
            "twentytwentyfour\ntwentytwentythree\n"
            if a[0] == "theme" and "list" in a else ""
        ))
        generator.install_theme("twentytwentyfour")
        calls = [c.args for c in generator._wpcli.call_args_list]
        assert any(c[0] == "theme" and "activate" in c for c in calls)

    def test_refuses_missing_theme(self, generator):
        generator._wpcli = MagicMock(return_value="twentytwentyfour\n")
        with pytest.raises(WordPressError, match="not installed"):
            generator.install_theme("astra")

    def test_activates_installed_plugin(self, generator):
        generator._wpcli = MagicMock(side_effect=lambda *a, **k: (
            "akismet\nhello\n" if a[0] == "plugin" and "list" in a else ""
        ))
        generator.install_plugin("akismet")
        calls = [c.args for c in generator._wpcli.call_args_list]
        assert any(c[0] == "plugin" and "activate" in c for c in calls)

    def test_refuses_missing_plugin(self, generator):
        generator._wpcli = MagicMock(return_value="akismet\n")
        with pytest.raises(WordPressError, match="not installed"):
            generator.install_plugin("yoast-seo")


# ── reset_wordpress() ─────────────────────────────────────────────────────────

class TestResetWordPress:
    def test_deletes_pages_and_menus(self, generator):
        def side_effect(*args, **kwargs):
            if args[0] == "post" and "list" in args:
                return "1 2 3"
            if args[0] == "menu" and "list" in args:
                return json.dumps([{"term_id": 5, "name": "Primary"}])
            return ""

        generator._wpcli = MagicMock(side_effect=side_effect)
        generator.reset_wordpress()
        calls = [c.args for c in generator._wpcli.call_args_list]
        assert any(c[0] == "post" and "delete" in c for c in calls)
        assert any(c[0] == "menu" and "delete" in c for c in calls)


# ── HTML rendering ────────────────────────────────────────────────────────────

class TestHtmlRendering:
    def test_homepage_has_h1(self, generator):
        content = {
            "hero": {"heading": "Welcome", "subheading": "Sub", "cta_primary": "Go"},
            "intro": "Intro text.",
        }
        html = generator._render_homepage(content, [])
        assert "<h1>Welcome</h1>" in html
        assert "Intro text." in html

    def test_about_renders_story(self, generator):
        content = {
            "hero_heading": "About",
            "company_story": "Our story.",
            "mission_statement": "Mission.",
            "values": [{"name": "Quality", "description": "Best."}],
        }
        html = generator._render_about(content, [])
        assert "<h1>About</h1>" in html
        assert "Our story." in html
        assert "Quality" in html

    def test_services_renders_list(self, generator):
        content = {
            "hero_heading": "Services",
            "intro": "All services.",
            "services": [{
                "name": "Repair",
                "heading": "Repair",
                "description": "We repair.",
                "benefits": ["Fast"],
                "process_steps": ["Call"],
                "cta": "Contact",
            }],
        }
        html = generator._render_services(content, [])
        assert "Repair" in html
        assert "We repair." in html
        assert "<ul>" in html
        assert "<ol>" in html

    def test_contact_includes_form(self, generator):
        content = {
            "hero_heading": "Contact",
            "intro": "Hello",
            "contact_section": {
                "heading": "Form",
                "text": "Write us",
                "form_cta": "Send",
            },
        }
        html = generator._render_contact(content, [])
        assert "<form" in html
        assert "Send" in html

    def test_faq_renders_qa(self, generator):
        content = {
            "hero_heading": "FAQ",
            "faqs": [{"question": "How much?", "answer": "Depends."}],
        }
        html = generator._render_faq(content, [])
        assert "How much?" in html
        assert "Depends." in html

    def test_body_html_passthrough(self, generator):
        content = {"body_html": "<div>Ready</div>"}
        html = generator._render_html("homepage", content, [])
        assert html == "<div>Ready</div>"

    def test_escapes_html_in_content(self, generator):
        content = {"hero": {"heading": "<script>alert(1)</script>"}}
        html = generator._render_homepage(content, [])
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# ── JSON loading ──────────────────────────────────────────────────────────────

class TestJsonLoading:
    def test_load_business_profile(self, generator, project_dir):
        biz = generator._load_business_profile(project_dir)
        assert biz["company_name"] == "Dachprofi Berlin"

    def test_load_optimized_pages(self, generator, project_dir):
        pages = generator._load_optimized_pages(project_dir)
        assert "homepage" in pages
        assert "about" in pages
        assert "services" in pages
        assert "contact" in pages
        assert "faq" in pages

    def test_load_meta_data(self, generator, project_dir):
        meta = generator._load_meta_data(project_dir)
        assert meta["homepage"]["title"] == "Dachprofi Berlin"

    def test_load_images(self, generator, project_dir):
        images = generator._load_image_metadata(project_dir)
        assert len(images) == 2
        assert images[0]["filename"] == "hero.jpg"

    def test_missing_business_returns_empty(self, generator, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        assert generator._load_business_profile(d) == {}

    def test_malformed_json_returns_default(self, generator, tmp_path):
        d = tmp_path / "bad" / "json"
        d.mkdir(parents=True)
        (d / "business_profile.json").write_text("NOT JSON", encoding="utf-8")
        assert generator._load_business_profile(tmp_path / "bad") == {}

    def test_no_optimized_pages(self, generator, tmp_path):
        d = tmp_path / "nopages"
        (d / "json").mkdir(parents=True)
        assert generator._load_optimized_pages(d) == {}


# ── Slug helpers ──────────────────────────────────────────────────────────────

class TestHelpers:
    def test_slugify(self):
        assert WordPressGenerator._slugify("Dach Reparatur!") == "dach-reparatur"
        assert WordPressGenerator._slugify("  Hello   World  ") == "hello-world"

    def test_parse_id(self):
        assert WordPressGenerator._parse_id("42\n") == 42
        with pytest.raises(WordPressError):
            WordPressGenerator._parse_id("not-a-number")

    def test_extract_page_title_from_hero(self, generator):
        title = generator._extract_page_title(
            "homepage", {"hero": {"heading": "Main H1"}}
        )
        assert title == "Main H1"


# ── generate_from_directory() ─────────────────────────────────────────────────

class TestGenerateFromDirectory:
    def test_returns_generation_result(self, generator, project_dir):
        generator.verify_installation = MagicMock()
        generator._wpcli = MagicMock(side_effect=_wpcli_side_effect)

        # Track page create IDs
        counter = {"n": 100}

        def side_effect(*args, **kwargs):
            if args[0] == "post" and "create" in args:
                counter["n"] += 1
                return f"{counter['n']}\n"
            if args[0] == "media" and "import" in args:
                return "201\n"
            return _wpcli_side_effect(*args, **kwargs)

        generator._wpcli = MagicMock(side_effect=side_effect)
        result = generator.generate_from_directory(project_dir)
        assert isinstance(result, GenerationResult)
        assert result.success is True
        assert len(result.pages_created) >= 5

    def test_writes_generation_report(self, generator, project_dir):
        generator.verify_installation = MagicMock()

        counter = {"n": 100}

        def side_effect(*args, **kwargs):
            if args[0] == "post" and "create" in args:
                counter["n"] += 1
                return f"{counter['n']}\n"
            if args[0] == "media" and "import" in args:
                return "201\n"
            return _wpcli_side_effect(*args, **kwargs)

        generator._wpcli = MagicMock(side_effect=side_effect)
        generator.generate_from_directory(project_dir)

        report_path = project_dir / "json" / "generation_report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert "pages_created" in report
        assert "images_imported" in report
        assert "menu_created" in report
        assert "seo_applied" in report
        assert report["success"] is True

    def test_imports_existing_images(self, generator, project_dir):
        generator.verify_installation = MagicMock()
        counter = {"n": 100}

        def side_effect(*args, **kwargs):
            if args[0] == "post" and "create" in args:
                counter["n"] += 1
                return f"{counter['n']}\n"
            if args[0] == "media" and "import" in args:
                return "201\n"
            return _wpcli_side_effect(*args, **kwargs)

        generator._wpcli = MagicMock(side_effect=side_effect)
        generator.generate_from_directory(project_dir)
        report = json.loads(
            (project_dir / "json" / "generation_report.json").read_text()
        )
        assert len(report["images_imported"]) == 1
        assert report["images_imported"][0]["filename"] == "hero.jpg"

    def test_warns_on_missing_images(self, generator, project_dir):
        generator.verify_installation = MagicMock()
        counter = {"n": 100}

        def side_effect(*args, **kwargs):
            if args[0] == "post" and "create" in args:
                counter["n"] += 1
                return f"{counter['n']}\n"
            if args[0] == "media" and "import" in args:
                return "201\n"
            return _wpcli_side_effect(*args, **kwargs)

        generator._wpcli = MagicMock(side_effect=side_effect)
        generator.generate_from_directory(project_dir)
        report = json.loads(
            (project_dir / "json" / "generation_report.json").read_text()
        )
        assert any("missing" in w.lower() or "Image missing" in w
                   for w in report["warnings"])

    def test_applies_seo_meta(self, generator, project_dir):
        generator.verify_installation = MagicMock()
        counter = {"n": 100}

        def side_effect(*args, **kwargs):
            if args[0] == "post" and "create" in args:
                counter["n"] += 1
                return f"{counter['n']}\n"
            if args[0] == "media" and "import" in args:
                return "201\n"
            return _wpcli_side_effect(*args, **kwargs)

        generator._wpcli = MagicMock(side_effect=side_effect)
        generator.generate_from_directory(project_dir)
        report = json.loads(
            (project_dir / "json" / "generation_report.json").read_text()
        )
        assert len(report["seo_applied"]) >= 1

    def test_creates_menu(self, generator, project_dir):
        generator.verify_installation = MagicMock()
        counter = {"n": 100}

        def side_effect(*args, **kwargs):
            if args[0] == "post" and "create" in args:
                counter["n"] += 1
                return f"{counter['n']}\n"
            if args[0] == "media" and "import" in args:
                return "201\n"
            return _wpcli_side_effect(*args, **kwargs)

        generator._wpcli = MagicMock(side_effect=side_effect)
        generator.generate_from_directory(project_dir)
        report = json.loads(
            (project_dir / "json" / "generation_report.json").read_text()
        )
        assert report["menu_created"] is True
        assert len(report["menu_items"]) >= 1

    def test_raises_when_no_optimized_pages(self, generator, tmp_path):
        generator.verify_installation = MagicMock()
        generator._wpcli = MagicMock(return_value="")
        empty = tmp_path / "empty_proj"
        (empty / "json").mkdir(parents=True)
        with pytest.raises(GenerationError, match="optimized"):
            generator.generate_from_directory(empty)

    def test_raises_when_wp_unavailable(self, generator, project_dir):
        generator.verify_installation = MagicMock(
            side_effect=WordPressError("no wp")
        )
        with pytest.raises(GenerationError, match="verification"):
            generator.generate_from_directory(project_dir)

    def test_continues_after_page_failure(self, generator, project_dir):
        generator.verify_installation = MagicMock()
        counter = {"n": 0}

        def side_effect(*args, **kwargs):
            if args[0] == "post" and "create" in args:
                counter["n"] += 1
                if counter["n"] == 1:
                    raise WordPressError("page fail")
                return f"{100 + counter['n']}\n"
            if args[0] == "media" and "import" in args:
                return "201\n"
            return _wpcli_side_effect(*args, **kwargs)

        generator._wpcli = MagicMock(side_effect=side_effect)
        result = generator.generate_from_directory(project_dir)
        # Some pages should still succeed
        assert len(result.pages_created) >= 1

    def test_creates_service_subpages(self, generator, project_dir):
        generator.verify_installation = MagicMock()
        counter = {"n": 100}

        def side_effect(*args, **kwargs):
            if args[0] == "post" and "create" in args:
                counter["n"] += 1
                return f"{counter['n']}\n"
            if args[0] == "media" and "import" in args:
                return "201\n"
            return _wpcli_side_effect(*args, **kwargs)

        generator._wpcli = MagicMock(side_effect=side_effect)
        result = generator.generate_from_directory(project_dir)
        assert "dachreparatur" in result.pages_created

    def test_site_settings_updated(self, generator, project_dir):
        generator.verify_installation = MagicMock()
        counter = {"n": 100}

        def side_effect(*args, **kwargs):
            if args[0] == "post" and "create" in args:
                counter["n"] += 1
                return f"{counter['n']}\n"
            if args[0] == "media" and "import" in args:
                return "201\n"
            return _wpcli_side_effect(*args, **kwargs)

        generator._wpcli = MagicMock(side_effect=side_effect)
        generator.generate_from_directory(project_dir)
        report = json.loads(
            (project_dir / "json" / "generation_report.json").read_text()
        )
        assert "blogname" in report["settings_updated"]


# ── generate() in-memory path ─────────────────────────────────────────────────

class TestGenerate:
    def test_writes_temp_json_and_delegates(self, generator, test_settings):
        generator.verify_installation = MagicMock()
        counter = {"n": 100}

        def side_effect(*args, **kwargs):
            if args[0] == "post" and "create" in args:
                counter["n"] += 1
                return f"{counter['n']}\n"
            return _wpcli_side_effect(*args, **kwargs)

        generator._wpcli = MagicMock(side_effect=side_effect)

        analysis = AnalysisResult(
            business=BusinessInfo(
                name="Test Co",
                industry="Roofing",
                services=["Repair"],
            ),
        )
        pages = {
            "homepage": PageContent(
                slug="homepage",
                title="Home",
                body_html="<h1>Home</h1>",
                meta_title="Home Title",
                meta_description="Home Desc",
            ),
            "about": PageContent(
                slug="about",
                title="About",
                body_html="<h1>About</h1>",
            ),
        }
        result = generator.generate(analysis, pages, "proj-123")
        assert isinstance(result, GenerationResult)

        # Temp JSON should exist
        json_dir = test_settings.projects_dir / "proj-123" / "json"
        assert (json_dir / "optimized_homepage.json").exists()
        assert (json_dir / "business_profile.json").exists()

    def test_raises_on_empty_pages(self, generator):
        analysis = AnalysisResult(business=BusinessInfo(name="X"))
        with pytest.raises(GenerationError, match="No page content"):
            generator.generate(analysis, {}, "proj-empty")


# ── _wpcli real subprocess wiring (mocked subprocess) ─────────────────────────

class TestWpcliRunner:
    def test_builds_correct_command(self, test_settings):
        gen = WordPressGenerator(test_settings)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="ok", stderr=""
            )
            out = gen._wpcli("option", "get", "blogname")
            assert out == "ok"
            cmd = mock_run.call_args.args[0]
            assert str(test_settings.php_exe) in cmd
            assert str(test_settings.wpcli_path) in cmd
            assert "--allow-root" in cmd
            assert "option" in cmd

    def test_raises_on_nonzero(self, test_settings):
        gen = WordPressGenerator(test_settings)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Error: boom"
            )
            with pytest.raises(WordPressError, match="boom"):
                gen._wpcli("core", "is-installed")

    def test_raises_on_timeout(self, test_settings):
        import subprocess as sp
        gen = WordPressGenerator(test_settings)
        with patch("subprocess.run", side_effect=sp.TimeoutExpired("cmd", 1)):
            with pytest.raises(WordPressError, match="timed out"):
                gen._wpcli("post", "list")
