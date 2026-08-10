"""
webmaker.core.schema
====================
Versioned JSON output helpers.

Every structured JSON artefact written by WebMaker should include
``schema_version`` so future format changes remain backward compatible.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Current on-disk schema version for all WebMaker JSON artefacts.
SCHEMA_VERSION: int = 1


def ensure_schema_version(data: Any, *, version: int = SCHEMA_VERSION) -> Any:
    """Return *data* with ``schema_version`` applied.

    - ``dict`` → insert ``schema_version`` (without clobbering an existing value)
    - ``list`` → wrap as ``{"schema_version": N, "items": [...]}``
    - other   → returned unchanged
    """
    if isinstance(data, dict):
        if "schema_version" not in data:
            # Put schema_version first for readability
            return {"schema_version": version, **data}
        return data
    if isinstance(data, list):
        return {"schema_version": version, "items": data}
    return data


def unwrap_json(data: Any) -> Any:
    """Accept legacy bare lists/dicts and versioned wrappers.

    Versioned list form::

        {"schema_version": 1, "items": [...]}

    Returns the inner list when present; otherwise returns *data* as-is.
    """
    if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
        # Only unwrap when this looks like our list wrapper (has schema_version
        # or only items (+ optional metadata) keys commonly used).
        if "schema_version" in data or set(data.keys()) <= {
            "schema_version", "items", "generated_at", "count",
        }:
            return data["items"]
    return data


def write_versioned_json(path: Path, data: Any, *, version: int = SCHEMA_VERSION) -> None:
    """Write *data* to *path* as UTF-8 JSON with ``schema_version`` applied."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = ensure_schema_version(data, version=version)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def load_json(path: Path, *, default: Any = None) -> Any:
    """Load JSON from *path*, returning *default* on any error."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def load_json_list(path: Path, *, default: list | None = None) -> list:
    """Load a JSON list, accepting legacy bare lists and versioned wrappers."""
    raw = load_json(path, default=None)
    if raw is None:
        return list(default or [])
    unwrapped = unwrap_json(raw)
    if isinstance(unwrapped, list):
        return unwrapped
    return list(default or [])


def load_json_dict(path: Path, *, default: dict | None = None) -> dict:
    """Load a JSON object, stripping unknown wrapper noise when needed."""
    raw = load_json(path, default=None)
    if isinstance(raw, dict):
        return raw
    return dict(default or {})
