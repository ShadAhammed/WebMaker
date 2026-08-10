"""
app.py — WebMaker application entry point
==========================================
This module is the top-level entry point for the WebMaker application.

Current responsibilities (Phase 2):
  1. Load and validate configuration from .env
  2. Initialise the logging system (console + file)
  3. Initialise the ProjectManager
  4. Perform environment startup checks
  5. Report readiness

Future phases will add the workflow orchestration and Streamlit UI here.

Usage::

    python app.py
"""

from __future__ import annotations

import sys
from pathlib import Path


# ── Bootstrap: logging must be set up before any webmaker import that logs ────

def _bootstrap() -> None:
    """Minimal pre-import setup (stdout encoding, path checks)."""
    # Ensure UTF-8 output on Windows even without PYTHONUTF8=1
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except AttributeError:
            pass  # Python < 3.7 fallback — not supported


_bootstrap()

# ── Imports (after bootstrap) ─────────────────────────────────────────────────

from webmaker.config.settings import settings
from webmaker.core.logging import get_logger, setup_logging
from webmaker.core.exceptions import ConfigurationError, WebMakerError
from webmaker.modules.project_manager import ProjectManager


# ── Logging initialisation ────────────────────────────────────────────────────

setup_logging(
    level=settings.log_level,
    log_dir=settings.logs_dir,
    log_filename=settings.log_filename,
)

log = get_logger("app")


# ── Startup checks ─────────────────────────────────────────────────────────────

def _check_configuration() -> list[str]:
    """Validate that the minimum required settings are present.

    Returns:
        List of human-readable warning strings (empty if all is well).
    """
    warnings: list[str] = []

    if not settings.php_exe.exists():
        warnings.append(f"PHP executable not found: {settings.php_exe}")

    if not settings.wordpress_dir.exists():
        warnings.append(f"WordPress directory not found: {settings.wordpress_dir}")

    if not any([
        settings.gemini_api_key,
        settings.claude_api_key,
        settings.deepseek_api_key,
    ]):
        warnings.append(
            "No AI API keys configured. "
            "Set GEMINI_API_KEY, CLAUDE_API_KEY, or DEEPSEEK_API_KEY in .env"
        )

    return warnings


def _check_runtime_directories() -> None:
    """Ensure all runtime directories exist, creating them if necessary."""
    dirs = [
        settings.logs_dir,
        settings.cache_dir,
        settings.projects_dir,
        settings.outputs_dir,
        settings.assets_dir,
        settings.templates_dir,
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def _print_startup_summary(manager: ProjectManager) -> None:
    """Print a brief environment summary to stdout."""
    separator = "─" * 50
    print(separator)
    print("  WebMaker — Startup Summary")
    print(separator)
    print(f"  Project root : {settings.project_root}")
    print(f"  Log level    : {settings.log_level}")
    print(f"  WordPress    : {settings.wordpress_url}")
    print(f"  Database     : {settings.db_host}:{settings.db_port}/{settings.db_name}")

    providers = []
    if settings.gemini_api_key:
        providers.append("Gemini")
    if settings.claude_api_key:
        providers.append("Claude")
    if settings.deepseek_api_key:
        providers.append("DeepSeek")
    print(f"  AI providers : {', '.join(providers) if providers else 'none configured'}")
    print(separator)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    """Application entry point."""
    log.info("WebMaker starting up...")

    # 1 — Ensure runtime directories are present
    _check_runtime_directories()
    log.debug("Runtime directories verified")

    # 2 — Validate configuration
    config_warnings = _check_configuration()
    for warning in config_warnings:
        log.warning(warning)

    # 3 — Initialise ProjectManager
    try:
        manager = ProjectManager(settings)
        log.debug("ProjectManager initialised")
    except WebMakerError as exc:
        log.error("Failed to initialise ProjectManager: {e}", e=exc)
        sys.exit(1)

    # 4 — Print startup summary
    _print_startup_summary(manager)

    # 5 — Done
    print("\nWebMaker initialized successfully.")
    log.info("WebMaker initialized successfully.")


if __name__ == "__main__":
    main()
