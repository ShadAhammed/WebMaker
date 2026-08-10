"""
webmaker.core.exceptions
========================
Custom exception hierarchy for WebMaker.

All exceptions inherit from WebMakerError so callers can catch the
entire family with a single except clause when desired.

Usage::

    from webmaker.core.exceptions import CrawlerError, AIError

    raise CrawlerError("Could not reach target URL", url=url)
"""

from __future__ import annotations


class WebMakerError(Exception):
    """Base exception for all WebMaker errors."""

    def __init__(self, message: str, **context) -> None:
        super().__init__(message)
        self.message  = message
        self.context  = context

    def __str__(self) -> str:
        if self.context:
            ctx = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
            return f"{self.message} [{ctx}]"
        return self.message


# ── Configuration ─────────────────────────────────────────────────────────────

class ConfigurationError(WebMakerError):
    """Raised when required configuration is missing or invalid."""


# ── Crawler ───────────────────────────────────────────────────────────────────

class CrawlerError(WebMakerError):
    """Raised when the website crawler fails to retrieve or parse a page."""


class RateLimitError(CrawlerError):
    """Raised when the target site rate-limits the crawler."""


class RobotsBlockedError(CrawlerError):
    """Raised when robots.txt disallows crawling the target URL."""


# ── Analysis ──────────────────────────────────────────────────────────────────

class AnalysisError(WebMakerError):
    """Raised when business or competitor analysis fails."""


# ── AI ────────────────────────────────────────────────────────────────────────

class AIError(WebMakerError):
    """Raised when an AI provider call fails."""


class AIProviderUnavailableError(AIError):
    """Raised when a requested AI provider is unreachable or unconfigured."""


class AIResponseError(AIError):
    """Raised when the AI response cannot be parsed or is invalid."""


# ── Generation ────────────────────────────────────────────────────────────────

class GenerationError(WebMakerError):
    """Raised when WordPress site generation fails."""


class WordPressError(GenerationError):
    """Raised when a WordPress or WP-CLI operation fails."""


class ThemeError(GenerationError):
    """Raised when theme creation or activation fails."""


# ── QA ────────────────────────────────────────────────────────────────────────

class QAError(WebMakerError):
    """Raised when the QA review process fails."""


# ── Project management ────────────────────────────────────────────────────────

class ProjectError(WebMakerError):
    """Raised when project creation, loading, or saving fails."""


class ProjectNotFoundError(ProjectError):
    """Raised when a referenced project does not exist."""


class ProjectAlreadyExistsError(ProjectError):
    """Raised when attempting to create a project that already exists."""


# ── Database ──────────────────────────────────────────────────────────────────

class DatabaseError(WebMakerError):
    """Raised when a database operation fails."""
