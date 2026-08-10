"""
tests/integration/test_environment.py
=======================================
Integration tests that verify the local development environment is intact.

These tests require the local services to be running:
  - MariaDB on the configured port
  - PHP available at the configured binary path

Run with:
    pytest tests/integration/ -v

Skip automatically in CI environments where services are unavailable.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from webmaker.config.settings import settings


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Return True if a TCP connection to host:port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ── Markers ───────────────────────────────────────────────────────────────────

requires_mariadb = pytest.mark.skipif(
    not _port_open(settings.db_host, settings.db_port),
    reason="MariaDB not reachable on configured port",
)

requires_php = pytest.mark.skipif(
    not settings.php_exe.exists(),
    reason="PHP executable not found",
)

requires_wpcli = pytest.mark.skipif(
    not settings.wpcli_path.exists(),
    reason="WP-CLI phar not found",
)


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestPHPEnvironment:

    @requires_php
    def test_php_version_output(self) -> None:
        result = subprocess.run(
            [str(settings.php_exe), "-c", str(settings.php_ini), "-r", "echo PHP_VERSION;"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "." in result.stdout   # version string contains dots


class TestMariaDBEnvironment:

    @requires_mariadb
    def test_mariadb_port_reachable(self) -> None:
        assert _port_open(settings.db_host, settings.db_port)


class TestWPCLIEnvironment:

    @requires_wpcli
    def test_wpcli_phar_exists(self) -> None:
        assert settings.wpcli_path.is_file()

    @requires_php
    @requires_wpcli
    def test_wpcli_info(self) -> None:
        result = subprocess.run(
            [
                str(settings.php_exe), "-c", str(settings.php_ini),
                str(settings.wpcli_path), "--info", "--format=json",
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
