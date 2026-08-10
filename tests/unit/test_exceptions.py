"""
tests/unit/test_exceptions.py
==============================
Unit tests for the custom exception hierarchy.
"""

from __future__ import annotations

import pytest

from webmaker.core.exceptions import (
    AIError,
    AIProviderUnavailableError,
    AnalysisError,
    ConfigurationError,
    CrawlerError,
    DatabaseError,
    GenerationError,
    ProjectError,
    ProjectNotFoundError,
    QAError,
    RateLimitError,
    WebMakerError,
    WordPressError,
)


class TestExceptionHierarchy:
    """All exceptions must inherit from WebMakerError."""

    @pytest.mark.parametrize("exc_class", [
        ConfigurationError,
        CrawlerError,
        RateLimitError,
        AnalysisError,
        AIError,
        AIProviderUnavailableError,
        GenerationError,
        WordPressError,
        QAError,
        ProjectError,
        ProjectNotFoundError,
        DatabaseError,
    ])
    def test_inherits_from_webmaker_error(self, exc_class) -> None:
        assert issubclass(exc_class, WebMakerError)

    def test_rate_limit_is_crawler_error(self) -> None:
        assert issubclass(RateLimitError, CrawlerError)

    def test_wordpress_error_is_generation_error(self) -> None:
        assert issubclass(WordPressError, GenerationError)

    def test_project_not_found_is_project_error(self) -> None:
        assert issubclass(ProjectNotFoundError, ProjectError)

    def test_provider_unavailable_is_ai_error(self) -> None:
        assert issubclass(AIProviderUnavailableError, AIError)


class TestExceptionMessages:
    """Exception str() should include message and context."""

    def test_simple_message(self) -> None:
        exc = WebMakerError("something went wrong")
        assert "something went wrong" in str(exc)

    def test_context_included_in_str(self) -> None:
        exc = CrawlerError("timeout", url="https://example.com", timeout=30)
        text = str(exc)
        assert "timeout" in text
        assert "url=" in text

    def test_context_stored_as_dict(self) -> None:
        exc = AIError("bad response", provider="gemini", code=429)
        assert exc.context["provider"] == "gemini"
        assert exc.context["code"]     == 429
