"""Resolve local project asset paths without hardcoding client folder names."""

from __future__ import annotations

import os
from pathlib import Path

_WEBMAKER_ROOT = Path(__file__).resolve().parents[2]
_PROJECTS_ROOT = _WEBMAKER_ROOT / "projects"


def projects_root() -> Path:
    return _PROJECTS_ROOT


def configured_project_slug() -> str:
    return (os.environ.get("WEBMAKER_PROJECT") or "").strip()


def iter_project_dirs() -> list[Path]:
    """Local project folders (runtime only; contents are gitignored)."""
    root = _PROJECTS_ROOT
    if not root.is_dir():
        return []
    skip = {".jobs", "competitors"}
    out: list[Path] = []
    slug = configured_project_slug()
    if slug:
        preferred = root / slug
        if preferred.is_dir():
            out.append(preferred)
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name.lower() in skip:
            continue
        if child in out:
            continue
        # Personal/portfolio trees are local-only; still discoverable on disk,
        # but never committed.
        out.append(child)
    return out


def find_project_path(*parts: str) -> Path | None:
    """Return the first existing path under any local project dir."""
    for base in iter_project_dirs():
        candidate = base.joinpath(*parts)
        if candidate.exists():
            return candidate
    return None


def project_path(*parts: str) -> Path:
    """
    Path under the active/local project.

    Prefers an existing match; otherwise builds under WEBMAKER_PROJECT or 'demo'.
    """
    found = find_project_path(*parts)
    if found is not None:
        return found
    slug = configured_project_slug() or "demo"
    return _PROJECTS_ROOT / slug / Path(*parts)
