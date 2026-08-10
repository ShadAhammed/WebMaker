"""
tests/unit/test_project_manager.py
====================================
Unit tests for ProjectManager.

All downstream modules (crawler, analyzers, optimizer, generator, QA) are
mocked — no network, WordPress, or AI calls are made.

Coverage:
  - Initialization / projects directory creation
  - create_project (folders, project.json, duplicates)
  - load_project / open_project / save_project / delete_project / list_projects
  - set_status / set_competitor_urls / phase status tracking
  - run_pipeline orchestration order
  - resume skips completed phases
  - force_phases re-runs
  - skip_phases
  - stop on failure / save state
  - compete phase with no competitor URLs (graceful)
  - verify_environment
  - error handling (invalid URL, missing project, no active project)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from webmaker.config.settings import Settings
from webmaker.core.exceptions import (
    ProjectAlreadyExistsError,
    ProjectError,
    ProjectNotFoundError,
)
from webmaker.core.types import (
    AnalysisResult,
    BusinessInfo,
    CrawlResult,
    GenerationResult,
    ProjectStatus,
    QAReport,
)
from webmaker.modules.project_manager import (
    PhaseName,
    PhaseStatus,
    ProjectManager,
    ProjectState,
    _PIPELINE,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def test_settings(tmp_path):
    php_dir = tmp_path / "bin" / "php"
    php_dir.mkdir(parents=True)
    (php_dir / "php.exe").write_text("")
    wp = tmp_path / "wordpress"
    wp.mkdir()
    (wp / "wp-config.php").write_text("<?php")
    wpcli = tmp_path / "bin" / "wp-cli.phar"
    wpcli.parent.mkdir(parents=True, exist_ok=True)
    wpcli.write_text("")

    return Settings(
        project_root  = tmp_path,
        logs_dir      = tmp_path / "logs",
        cache_dir     = tmp_path / "cache",
        projects_dir  = tmp_path / "projects",
        outputs_dir   = tmp_path / "outputs",
        assets_dir    = tmp_path / "assets",
        templates_dir = tmp_path / "templates",
        wordpress_dir = wp,
        php_dir       = php_dir,
        wpcli_path    = wpcli,
        mariadb_dir   = tmp_path / "bin" / "mariadb",
        server_port   = 18080,
        db_port       = 13307,
    )


@pytest.fixture
def manager(test_settings):
    return ProjectManager(test_settings)


def _mock_modules(manager: ProjectManager, projects_dir: Path, url: str = "https://example.com") -> dict:
    """Attach mocked modules that write minimal JSON artifacts."""

    # Crawler: create domain folder + pages.json
    domain_dir = projects_dir / "example-com"
    domain_dir.mkdir(parents=True, exist_ok=True)
    (domain_dir / "json").mkdir(parents=True, exist_ok=True)

    crawler = MagicMock()
    def do_crawl(u):
        (domain_dir / "json" / "pages.json").write_text("[]", encoding="utf-8")
        (domain_dir / "json" / "crawl_summary.json").write_text("{}", encoding="utf-8")
        return CrawlResult(target_url=u, pages=[], total_pages=0)
    crawler.crawl.side_effect = do_crawl
    manager._crawler = crawler

    biz = MagicMock()
    def do_analyze(d):
        path = Path(d) / "json" / "business_profile.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"company_name": "Example"}), encoding="utf-8")
        return BusinessInfo(name="Example", industry="Demo")
    biz.analyze_from_directory.side_effect = do_analyze
    manager._business_analyzer = biz

    comp = MagicMock()
    def do_compete(urls, d):
        j = Path(d) / "json"
        j.mkdir(parents=True, exist_ok=True)
        (j / "competitors.json").write_text("[]", encoding="utf-8")
        (j / "comparison_report.json").write_text("{}", encoding="utf-8")
        return AnalysisResult(business=BusinessInfo(name="Example"))
    comp.analyze_from_urls.side_effect = do_compete
    manager._competitor_analyzer = comp

    opt = MagicMock()
    def do_optimize(d, **kwargs):
        j = Path(d) / "json"
        j.mkdir(parents=True, exist_ok=True)
        (j / "optimized_homepage.json").write_text("{}", encoding="utf-8")
        (j / "meta_data.json").write_text("{}", encoding="utf-8")
        return {"pages_generated": ["homepage"], "errors": []}
    opt.optimize_from_directory.side_effect = do_optimize
    manager._content_optimizer = opt

    gen = MagicMock()
    def do_generate(d, **kwargs):
        j = Path(d) / "json"
        j.mkdir(parents=True, exist_ok=True)
        (j / "generation_report.json").write_text(
            json.dumps({"success": True, "pages_created": ["home"]}),
            encoding="utf-8",
        )
        return GenerationResult(
            wp_url="http://localhost:8080",
            wp_path=Path("/wp"),
            pages_created=["home"],
            success=True,
        )
    gen.generate_from_directory.side_effect = do_generate
    manager._wordpress_generator = gen

    qa = MagicMock()
    def do_review(d, **kwargs):
        j = Path(d) / "json"
        j.mkdir(parents=True, exist_ok=True)
        (j / "qa_report.json").write_text("{}", encoding="utf-8")
        (j / "website_score.json").write_text("{}", encoding="utf-8")
        return QAReport(wp_url="http://localhost:8080", overall_score=0.9, passed=True)
    qa.review_from_directory.side_effect = do_review
    manager._qa_reviewer = qa

    fixer = MagicMock()
    def do_fix(d, **kwargs):
        j = Path(d) / "json"
        j.mkdir(parents=True, exist_ok=True)
        (j / "fix_report.json").write_text(
            json.dumps({"pages_fixed": ["homepage"], "errors": []}),
            encoding="utf-8",
        )
        return {"pages_fixed": ["homepage"], "errors": [], "rebuilt": True}
    fixer.fix_from_directory.side_effect = do_fix
    manager._website_fixer = fixer

    return {
        "crawler": crawler,
        "biz": biz,
        "comp": comp,
        "opt": opt,
        "gen": gen,
        "qa": qa,
        "fixer": fixer,
        "domain_dir": domain_dir,
    }


# ── Initialization ─────────────────────────────────────────────────────────────

class TestInit:
    def test_creates_projects_dir(self, test_settings, tmp_path):
        projects_dir = tmp_path / "new_projects"
        assert not projects_dir.exists()
        ProjectManager(test_settings, projects_dir=projects_dir)
        assert projects_dir.exists()

    def test_active_project_none_on_init(self, manager):
        assert manager.active_project is None

    def test_accepts_custom_projects_dir(self, test_settings, tmp_path):
        custom = tmp_path / "custom"
        mgr = ProjectManager(test_settings, projects_dir=custom)
        assert mgr._projects_dir == custom


# ── create / load / save / delete / list ───────────────────────────────────────

class TestLifecycle:
    def test_create_project_writes_json_and_folders(self, manager, test_settings):
        cfg = manager.create_project("https://example.com", name="demo")
        assert cfg.name == "demo"
        assert cfg.status == ProjectStatus.PENDING
        assert cfg.target_url.startswith("https://example.com")

        project_dir = test_settings.projects_dir / "demo"
        assert project_dir.exists()
        assert (project_dir / "project.json").exists()
        assert (project_dir / "json").exists()
        assert (project_dir / "logs").exists()
        assert (project_dir / "images").exists()

        data = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
        assert data["id"] == cfg.id
        assert data["pending_phases"] == [p.value for p in _PIPELINE]
        assert data["completed_phases"] == []
        assert "crawl" in data["phases"]

    def test_create_project_from_url_slug(self, manager, test_settings):
        cfg = manager.create_project("https://www.example.com/path")
        # domain slug matches crawler convention
        assert (test_settings.projects_dir / "example-com" / "project.json").exists()
        assert cfg.name == "example-com"

    def test_create_duplicate_raises(self, manager):
        manager.create_project("https://example.com", name="dup")
        with pytest.raises(ProjectAlreadyExistsError):
            manager.create_project("https://example.com", name="dup")

    def test_create_invalid_url_raises(self, manager):
        with pytest.raises(ProjectError, match="Invalid"):
            manager.create_project("not-a-url")

    def test_load_project_by_slug(self, manager):
        cfg = manager.create_project("https://example.com", name="alpha")
        manager._active = None
        loaded = manager.load_project("alpha")
        assert loaded.id == cfg.id
        assert manager.active_project is not None

    def test_load_project_by_id(self, manager):
        cfg = manager.create_project("https://example.com", name="beta")
        manager._active = None
        loaded = manager.load_project(cfg.id)
        assert loaded.name == "beta"

    def test_load_missing_raises(self, manager):
        with pytest.raises(ProjectNotFoundError):
            manager.load_project("does-not-exist")

    def test_open_project_alias(self, manager):
        cfg = manager.create_project("https://example.com", name="gamma")
        manager._active = None
        opened = manager.open_project("gamma")
        assert opened.id == cfg.id

    def test_save_project_updates_file(self, manager, test_settings):
        cfg = manager.create_project("https://example.com", name="delta")
        cfg.notes = "hello"
        manager.save_project(cfg)
        data = json.loads(
            (test_settings.projects_dir / "delta" / "project.json").read_text()
        )
        assert data["notes"] == "hello"

    def test_save_without_active_raises(self, manager):
        with pytest.raises(ProjectError, match="No active"):
            manager.save_project()

    def test_delete_project(self, manager, test_settings):
        manager.create_project("https://example.com", name="epsilon")
        assert (test_settings.projects_dir / "epsilon").exists()
        manager.delete_project("epsilon")
        assert not (test_settings.projects_dir / "epsilon").exists()
        assert manager.active_project is None

    def test_list_projects_sorted(self, manager):
        manager.create_project("https://a.example.com", name="a-proj")
        manager.create_project("https://b.example.com", name="b-proj")
        projects = manager.list_projects()
        assert len(projects) >= 2
        # newest first
        assert projects[0].created_at >= projects[1].created_at


# ── Status tracking ───────────────────────────────────────────────────────────

class TestStatus:
    def test_set_status(self, manager, test_settings):
        manager.create_project("https://example.com", name="status-test")
        manager.set_status(ProjectStatus.CRAWLING)
        assert manager.active_project.status == ProjectStatus.CRAWLING
        data = json.loads(
            (test_settings.projects_dir / "status-test" / "project.json").read_text()
        )
        assert data["status"] == "crawling"

    def test_set_status_without_active_raises(self, manager):
        with pytest.raises(ProjectError):
            manager.set_status(ProjectStatus.COMPLETED)

    def test_set_competitor_urls(self, manager):
        manager.create_project("https://example.com", name="comp-test")
        manager.set_competitor_urls(["https://rival.com", "bad", "https://other.com"])
        assert len(manager.active_state.competitor_urls) == 2

    def test_phase_status_defaults_not_started(self, manager):
        manager.create_project("https://example.com", name="phase-test")
        assert manager.get_phase_status(PhaseName.CRAWL) == PhaseStatus.NOT_STARTED
        assert manager.get_phase_status("analyze") == PhaseStatus.NOT_STARTED


# ── Pipeline orchestration ────────────────────────────────────────────────────

class TestPipeline:
    def test_run_pipeline_calls_modules_in_order(self, manager, test_settings):
        manager.create_project(
            "https://example.com",
            name="example-com",  # align with crawler domain folder
            competitor_urls=["https://competitor.example.com"],
        )
        mocks = _mock_modules(manager, test_settings.projects_dir)

        result = manager.run_pipeline()
        assert result.status == ProjectStatus.COMPLETED

        mocks["crawler"].crawl.assert_called_once()
        mocks["biz"].analyze_from_directory.assert_called_once()
        mocks["comp"].analyze_from_urls.assert_called_once()
        mocks["opt"].optimize_from_directory.assert_called_once()
        mocks["gen"].generate_from_directory.assert_called_once()
        mocks["qa"].review_from_directory.assert_called_once()
        mocks["fixer"].fix_from_directory.assert_called_once()

        state = manager.active_state
        assert state.completed_phases == [p.value for p in _PIPELINE]
        assert state.pending_phases == []
        for phase in _PIPELINE:
            assert state.phases[phase.value].status == PhaseStatus.COMPLETED.value

    def test_resume_skips_completed_phases(self, manager, test_settings):
        manager.create_project(
            "https://example.com",
            name="example-com",
            competitor_urls=["https://competitor.example.com"],
        )
        mocks = _mock_modules(manager, test_settings.projects_dir)

        # Mark crawl + analyze completed
        state = manager.active_state
        for phase in (PhaseName.CRAWL, PhaseName.ANALYZE):
            state.phases[phase.value].status = PhaseStatus.COMPLETED.value
            state.completed_phases.append(phase.value)
        state.pending_phases = [p.value for p in _PIPELINE if p.value not in state.completed_phases]
        manager.save_project()

        manager.resume()

        mocks["crawler"].crawl.assert_not_called()
        mocks["biz"].analyze_from_directory.assert_not_called()
        mocks["comp"].analyze_from_urls.assert_called_once()
        mocks["qa"].review_from_directory.assert_called_once()

    def test_force_phases_reruns(self, manager, test_settings):
        manager.create_project(
            "https://example.com",
            name="example-com",
            competitor_urls=["https://c.com"],
        )
        mocks = _mock_modules(manager, test_settings.projects_dir)

        # Complete crawl
        state = manager.active_state
        state.phases[PhaseName.CRAWL.value].status = PhaseStatus.COMPLETED.value
        state.completed_phases = [PhaseName.CRAWL.value]
        manager.save_project()

        manager.run_pipeline(force_phases=["crawl"], skip_phases=[
            "compete", "optimize", "generate", "review", "fix"
        ])
        mocks["crawler"].crawl.assert_called_once()

    def test_skip_phases(self, manager, test_settings):
        manager.create_project("https://example.com", name="example-com")
        mocks = _mock_modules(manager, test_settings.projects_dir)

        manager.run_pipeline(skip_phases=["compete", "generate", "review", "fix"])
        mocks["comp"].analyze_from_urls.assert_not_called()
        mocks["gen"].generate_from_directory.assert_not_called()
        mocks["qa"].review_from_directory.assert_not_called()
        mocks["fixer"].fix_from_directory.assert_not_called()
        mocks["crawler"].crawl.assert_called_once()
        mocks["biz"].analyze_from_directory.assert_called_once()
        mocks["opt"].optimize_from_directory.assert_called_once()

    def test_stops_on_failure_and_saves(self, manager, test_settings):
        manager.create_project("https://example.com", name="example-com")
        mocks = _mock_modules(manager, test_settings.projects_dir)
        mocks["biz"].analyze_from_directory.side_effect = RuntimeError("boom")

        result = manager.run_pipeline()
        assert result.status == ProjectStatus.FAILED

        state = manager.active_state
        assert state.phases[PhaseName.CRAWL.value].status == PhaseStatus.COMPLETED.value
        assert state.phases[PhaseName.ANALYZE.value].status == PhaseStatus.FAILED.value
        assert "boom" in state.last_error
        mocks["comp"].analyze_from_urls.assert_not_called()

        # Persisted
        data = json.loads(
            (test_settings.projects_dir / "example-com" / "project.json").read_text()
        )
        assert data["status"] == "failed"
        assert data["phases"]["analyze"]["status"] == "failed"

    def test_compete_without_urls_still_completes(self, manager, test_settings):
        manager.create_project("https://example.com", name="example-com")
        mocks = _mock_modules(manager, test_settings.projects_dir)

        manager.run_pipeline(skip_phases=["generate", "review", "fix"])
        mocks["comp"].analyze_from_urls.assert_not_called()
        assert (
            manager.active_state.phases[PhaseName.COMPETE.value].status
            == PhaseStatus.COMPLETED.value
        )
        assert manager.active_state.phases[PhaseName.COMPETE.value].warnings

    def test_run_phase_single(self, manager, test_settings):
        manager.create_project("https://example.com", name="example-com")
        mocks = _mock_modules(manager, test_settings.projects_dir)
        ok = manager.run_phase("crawl")
        assert ok is True
        mocks["crawler"].crawl.assert_called_once()
        mocks["biz"].analyze_from_directory.assert_not_called()

    def test_run_pipeline_requires_active(self, manager):
        with pytest.raises(ProjectError, match="No active"):
            manager.run_pipeline()

    def test_project_log_written(self, manager, test_settings):
        manager.create_project("https://example.com", name="example-com")
        _mock_modules(manager, test_settings.projects_dir)
        manager.run_pipeline(
            skip_phases=["compete", "optimize", "generate", "review", "fix"]
        )
        log_file = test_settings.projects_dir / "example-com" / "logs" / "project.log"
        assert log_file.exists()
        text = log_file.read_text(encoding="utf-8")
        assert "Phase started: crawl" in text
        assert "Phase completed: crawl" in text


# ── Environment ───────────────────────────────────────────────────────────────

class TestEnvironment:
    def test_verify_environment(self, manager):
        result = manager.verify_environment()
        assert result["php"] is True
        assert result["wpcli"] is True
        assert result["wordpress"] is True
        assert result["projects_dir"] is True
        assert "mariadb" in result


# ── Helpers ───────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_domain_slug(self):
        assert ProjectManager._domain_slug("https://www.Example.com/x") == "example-com"

    def test_normalise_url(self):
        assert ProjectManager._normalise_url("https://Example.com/path/") == \
            "https://example.com/path"
        assert ProjectManager._normalise_url("ftp://x.com") == ""
