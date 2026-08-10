"""
webmaker.utils.helpers
=======================
Small, stateless utility functions used throughout WebMaker.
Each function has a single, well-defined purpose and no side-effects
beyond the file system operations explicitly described in its docstring.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse


def ensure_dir(path: Path) -> Path:
    """Create *path* (and any missing parents) if it does not exist.

    Args:
        path: Directory path to create.

    Returns:
        The same *path* so this function can be used inline.
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def slugify(text: str, separator: str = "-") -> str:
    """Convert *text* to a URL-safe slug.

    Strips diacritics, lowercases, replaces non-alphanumeric characters
    with *separator*, and collapses consecutive separators.

    Args:
        text:      Input string (may contain Unicode).
        separator: Character used between words; defaults to ``"-"``.

    Returns:
        Normalised slug string.

    Examples::

        slugify("Müller & Söhne GmbH")   # → "muller-sohne-gmbh"
        slugify("Hello World!", "_")      # → "hello_world"
    """
    # Decompose Unicode and strip combining characters (diacritics)
    normalised = unicodedata.normalize("NFKD", text)
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii")
    lowered    = ascii_text.lower()
    cleaned    = re.sub(r"[^a-z0-9]+", separator, lowered)
    return cleaned.strip(separator)


def truncate(text: str, max_length: int, ellipsis: str = "…") -> str:
    """Shorten *text* to *max_length* characters, appending *ellipsis*.

    If *text* is already within *max_length*, it is returned unchanged.

    Args:
        text:       Input string.
        max_length: Maximum allowed length (inclusive of ellipsis).
        ellipsis:   String appended when truncation occurs.

    Returns:
        Possibly truncated string.
    """
    if len(text) <= max_length:
        return text
    cut = max_length - len(ellipsis)
    return text[:cut].rstrip() + ellipsis


def format_bytes(n: int) -> str:
    """Format *n* bytes as a human-readable string.

    Args:
        n: Byte count (non-negative integer).

    Returns:
        String such as ``"4.2 MB"`` or ``"512 B"``.
    """
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n //= 1024
    return f"{n:.1f} PB"


def is_valid_url(url: str) -> bool:
    """Return True if *url* is a syntactically valid HTTP(S) URL.

    Args:
        url: String to validate.

    Returns:
        True if scheme is http or https and a netloc is present.
    """
    try:
        result = urlparse(url)
        return result.scheme in {"http", "https"} and bool(result.netloc)
    except ValueError:
        return False
