"""
tests/unit/test_config.py
==========================
Unit tests for the Settings configuration model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from webmaker.config.settings import Settings


class TestSettingsDefaults:
    """Settings should have sensible defaults without any .env file."""

    def test_project_root_is_path(self, test_settings: Settings) -> None:
        assert isinstance(test_settings.project_root, Path)

    def test_server_port_default(self) -> None:
        s = Settings(project_root=Path("."))
        assert isinstance(s.server_port, int)
        assert s.server_port > 0

    def test_wordpress_url_format(self, test_settings: Settings) -> None:
        url = test_settings.wordpress_url
        assert url.startswith("http://")
        assert str(test_settings.server_port) in url

    def test_db_dsn_format(self, test_settings: Settings) -> None:
        dsn = test_settings.db_dsn
        assert dsn.startswith("mysql+pymysql://")

    def test_empty_api_keys_by_default(self, test_settings: Settings) -> None:
        """No API keys should be set unless explicitly provided."""
        assert test_settings.gemini_api_key   == ""
        assert test_settings.claude_api_key   == ""
        assert test_settings.deepseek_api_key == ""

    def test_php_exe_is_path(self, test_settings: Settings) -> None:
        assert isinstance(test_settings.php_exe, Path)


class TestSettingsOverride:
    """Settings values should be overridable via constructor kwargs."""

    def test_override_server_port(self) -> None:
        s = Settings(project_root=Path("."), server_port=9999)
        assert s.server_port == 9999

    def test_override_log_level(self) -> None:
        s = Settings(project_root=Path("."), log_level="DEBUG")
        assert s.log_level == "DEBUG"
