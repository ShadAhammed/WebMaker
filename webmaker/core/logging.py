"""
webmaker.core.logging
=====================
Centralised logging configuration using loguru.

Features:
- Console output with colour and human-readable format
- Rotating file log written to logs/webmaker.log
- Per-module child loggers via get_logger()
- Log level controlled by LOG_LEVEL environment variable

Usage::

    from webmaker.core.logging import setup_logging, get_logger

    setup_logging()                     # call once at startup
    log = get_logger("website_crawler")
    log.info("Crawling {url}", url=url)
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

# Loguru sink IDs so they can be removed before re-adding (idempotent setup)
_CONSOLE_ID: int | None = None
_FILE_ID:    int | None = None


def setup_logging(
    *,
    level: str = "INFO",
    log_dir: Path | None = None,
    log_filename: str = "webmaker.log",
    rotation: str = "10 MB",
    retention: str = "14 days",
    colorize: bool = True,
) -> None:
    """Configure console and file logging.

    Must be called once at application startup before any module uses
    ``get_logger()``.

    Args:
        level:        Minimum log level (DEBUG | INFO | WARNING | ERROR).
        log_dir:      Directory for log files. Defaults to ``<project_root>/logs``.
        log_filename: Name of the rotating log file.
        rotation:     Loguru rotation trigger (size or time).
        retention:    How long old log files are kept.
        colorize:     Enable ANSI colour codes in console output.
    """
    global _CONSOLE_ID, _FILE_ID

    # Remove previously registered sinks (allows calling setup_logging again)
    if _CONSOLE_ID is not None:
        logger.remove(_CONSOLE_ID)
    if _FILE_ID is not None:
        logger.remove(_FILE_ID)

    # Remove loguru default sink
    logger.remove(0)

    console_fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{extra[module]}</cyan> | "
        "<level>{message}</level>"
    )
    file_fmt = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
        "{level: <8} | "
        "{extra[module]} | "
        "{message}"
    )

    _CONSOLE_ID = logger.add(
        sys.stderr,
        format=console_fmt,
        level=level,
        colorize=colorize,
    )

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        _FILE_ID = logger.add(
            log_dir / log_filename,
            format=file_fmt,
            level=level,
            rotation=rotation,
            retention=retention,
            encoding="utf-8",
        )


def get_logger(module_name: str):
    """Return a loguru logger bound to a module name.

    Args:
        module_name: Human-readable identifier shown in log lines.

    Returns:
        A loguru logger with ``extra["module"]`` pre-bound.
    """
    return logger.bind(module=module_name)
