"""
webmaker.schemas.design
=======================
DesignRecommendation artifact — output of Agent 4 (Design Pattern Selector).

Agent 4 does **not** invent layouts. It selects a WordPress theme, starter
template, and one proven pattern per slot from the Design Pattern Library,
each with a short justification for the business.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from webmaker.schemas.base import Artifact

PatternSlot = Literal[
    "hero",
    "services",
    "about",
    "process",
    "testimonial",
    "faq",
    "cta",
    "footer",
]


class ThemeOption(BaseModel):
    """A single recommended theme + starter template pairing."""

    model_config = ConfigDict(extra="forbid")

    theme_id:         str = ""
    theme_name:       str = ""
    template_id:      str = ""
    template_name:    str = ""
    preview_url:      str = ""
    rationale:        str = ""
    score:            float = 0.0


class PatternSelection(BaseModel):
    """One selected design pattern for a page/section slot."""

    model_config = ConfigDict(extra="forbid")

    slot:          PatternSlot | str = "hero"
    pattern_id:    str = ""
    pattern_name:  str = ""
    justification: str = ""


class DesignRecommendation(Artifact):
    """Selected theme/template + design patterns for deterministic rendering."""

    model_config = ConfigDict(extra="forbid")

    artifact_name: str = "design"

    # Ranked theme/template options (best first); first is the default selection.
    options:           list[ThemeOption] = Field(default_factory=list)
    selected_theme:    str = ""
    selected_template: str = ""
    theme_justification: str = ""
    template_justification: str = ""

    # One selection per pattern slot (hero, services, … footer).
    patterns: list[PatternSelection] = Field(default_factory=list)

    typography:     str = ""
    color_palette:  list[str] = Field(default_factory=list)
    visual_style:   str = ""
    business_style: str = ""

    def pattern_for(self, slot: str) -> PatternSelection | None:
        """Return the selection for *slot*, if any."""
        for p in self.patterns:
            if str(p.slot) == slot:
                return p
        return None
