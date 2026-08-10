"""
tests/conftest.py
=================
Shared pytest fixtures available to all test modules.

Add project-wide fixtures here: temporary directories, settings overrides,
mock objects, and database setup/teardown.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from webmaker.config.settings import Settings


# ── Settings fixture ──────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_settings(tmp_path_factory) -> Settings:
    """Return a Settings instance pointing to isolated temp directories.

    Using ``scope="session"`` means this runs once per test session,
    creating a single temporary tree reused by all tests.
    """
    base = tmp_path_factory.mktemp("webmaker_test")

    return Settings(
        project_root   = base,
        logs_dir       = base / "logs",
        cache_dir      = base / "cache",
        projects_dir   = base / "projects",
        outputs_dir    = base / "outputs",
        assets_dir     = base / "assets",
        templates_dir  = base / "templates",
        wordpress_dir  = base / "wordpress",
        # Use a non-standard port to avoid colliding with the dev instance
        server_port    = 18080,
        db_port        = 13307,
    )


# ── Temp project directory ────────────────────────────────────────────────────

@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Provide a fresh temporary directory for a single test's project data."""
    d = tmp_path / "project"
    d.mkdir()
    return d
