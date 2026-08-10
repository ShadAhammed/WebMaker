"""
tests/unit/test_utils.py
========================
Unit tests for webmaker.utils.helpers.
"""

from __future__ import annotations

import pytest

from webmaker.utils.helpers import (
    format_bytes,
    is_valid_url,
    slugify,
    truncate,
)


class TestSlugify:
    def test_basic(self) -> None:
        assert slugify("Hello World") == "hello-world"

    def test_diacritics_stripped(self) -> None:
        assert slugify("Müller") == "muller"

    def test_special_chars_replaced(self) -> None:
        assert slugify("foo & bar!") == "foo-bar"

    def test_custom_separator(self) -> None:
        assert slugify("hello world", "_") == "hello_world"

    def test_consecutive_separators_collapsed(self) -> None:
        result = slugify("a  --  b")
        assert "--" not in result
        assert result == "a-b"

    def test_empty_string(self) -> None:
        assert slugify("") == ""


class TestTruncate:
    def test_short_string_unchanged(self) -> None:
        assert truncate("hello", 10) == "hello"

    def test_long_string_truncated(self) -> None:
        result = truncate("hello world", 8)
        assert len(result) <= 8
        assert result.endswith("…")

    def test_exact_length_unchanged(self) -> None:
        assert truncate("12345", 5) == "12345"

    def test_custom_ellipsis(self) -> None:
        result = truncate("hello world", 8, "...")
        assert result.endswith("...")


class TestFormatBytes:
    def test_bytes(self) -> None:
        assert format_bytes(512) == "512 B"

    def test_kilobytes(self) -> None:
        assert "KB" in format_bytes(2048)

    def test_megabytes(self) -> None:
        assert "MB" in format_bytes(5 * 1024 * 1024)


class TestIsValidUrl:
    @pytest.mark.parametrize("url", [
        "https://example.com",
        "http://localhost:8080",
        "https://sub.domain.co.uk/path?q=1",
    ])
    def test_valid_urls(self, url: str) -> None:
        assert is_valid_url(url) is True

    @pytest.mark.parametrize("url", [
        "",
        "not-a-url",
        "ftp://example.com",
        "//no-scheme.com",
        "example.com",
    ])
    def test_invalid_urls(self, url: str) -> None:
        assert is_valid_url(url) is False
