"""
webmaker.core.prompts
=====================
Dynamic prompt loader for Markdown files under ``prompts/``.

Modules must not hardcode long system prompts in Python source.  Load them
with :func:`load_prompt` instead.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from webmaker.core.exceptions import ConfigurationError
from webmaker.core.logging import get_logger

log = get_logger("prompts")

# Default prompts directory: <project_root>/prompts
_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_PROMPTS_DIR = _ROOT / "prompts"


class PromptLoader:
    """Loads and renders Markdown prompt templates from disk.

    Args:
        prompts_dir: Directory containing ``*.md`` prompt files.
    """

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self._dir = Path(prompts_dir) if prompts_dir else _DEFAULT_PROMPTS_DIR

    @property
    def prompts_dir(self) -> Path:
        return self._dir

    def list_prompts(self) -> list[str]:
        """Return available prompt names (without ``.md``)."""
        if not self._dir.exists():
            return []
        return sorted(p.stem for p in self._dir.glob("*.md"))

    def exists(self, name: str) -> bool:
        """Return True if ``name.md`` exists."""
        return self._path(name).is_file()

    def load(self, name: str, variables: dict[str, Any] | None = None, **kwargs: Any) -> str:
        """Load ``name.md`` and optionally interpolate ``{{variables}}``.

        Args:
            name:      Prompt file stem (e.g. ``"business"`` → ``business.md``).
            variables: Optional ``{{key}}`` replacements (preferred).
            **kwargs:  Additional ``{{key}}`` replacements (merged into variables).

        Returns:
            Prompt text.

        Raises:
            ConfigurationError: If the prompt file is missing.
        """
        path = self._path(name)
        if not path.is_file():
            raise ConfigurationError(
                f"Prompt file not found: {path}",
                prompt=name,
                path=str(path),
            )
        text = path.read_text(encoding="utf-8")
        # Strip YAML-like front matter if present (--- ... ---)
        text = _strip_front_matter(text).strip()
        merged = {**(variables or {}), **kwargs}
        if merged:
            text = _interpolate(text, merged)
        log.debug("Loaded prompt '{n}' ({c} chars)", n=name, c=len(text))
        return text

    def load_or_default(
        self,
        name: str,
        default: str,
        variables: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        """Load a prompt, falling back to *default* if the file is missing."""
        try:
            return self.load(name, variables=variables, **kwargs)
        except ConfigurationError:
            log.warning("Prompt '{n}' missing — using inline default", n=name)
            text = default
            merged = {**(variables or {}), **kwargs}
            if merged:
                text = _interpolate(text, merged)
            return text

    def _path(self, name: str) -> Path:
        stem = name.removesuffix(".md").strip().replace("\\", "/").split("/")[-1]
        return self._dir / f"{stem}.md"


def _strip_front_matter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :]
    return text


def _interpolate(text: str, variables: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key in variables:
            return str(variables[key])
        return match.group(0)

    return re.sub(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}", repl, text)


# Module-level singleton for convenience
_loader = PromptLoader()


def load_prompt(name: str, variables: dict | None = None, **kwargs) -> str:
    """Load a prompt from the default ``prompts/`` directory."""
    return _loader.load(name, variables=variables, **kwargs)


def load_prompt_or_default(
    name: str, default: str, variables: dict | None = None, **kwargs
) -> str:
    """Load a prompt or return *default* when the file is absent."""
    return _loader.load_or_default(name, default, variables=variables, **kwargs)


def get_prompt_loader(prompts_dir: Path | None = None) -> PromptLoader:
    """Return a PromptLoader (new instance when *prompts_dir* is set)."""
    if prompts_dir is None:
        return _loader
    return PromptLoader(prompts_dir)


# Invalidate cached paths if needed in tests
def clear_prompt_cache() -> None:
    """Reset the default loader (useful in tests)."""
    global _loader
    _loader = PromptLoader()
