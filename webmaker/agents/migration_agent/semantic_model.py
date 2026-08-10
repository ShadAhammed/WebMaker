"""
webmaker.agents.migration_agent.semantic_model
===============================================
Universal Semantic Layout Model — CMS-agnostic page structure.

This JSON is the only contract between Layout Analyzer and Theme Mapper.
It must not mention HTML, CSS, WordPress, or theme class names.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SectionType = Literal[
    "hero",
    "services_grid",
    "features_grid",
    "cards",
    "two_column",
    "three_column",
    "image_text",
    "text_image",
    "gallery",
    "faq",
    "testimonials",
    "contact",
    "team",
    "about",
    "pricing",
    "cta",
    "footer",
    "map",
    "timeline",
    "process",
    "statistics",
    "before_after",
    "labeled_sections",  # title-left / content-right rows (common service pages)
    "rich_text",
    "nav_links",
    "unknown",
]


class LayoutItem(BaseModel):
    """One card / grid cell / FAQ entry / list block."""

    model_config = ConfigDict(extra="forbid")

    heading: str = ""
    text: str = ""
    image: str = ""
    link: str = ""
    button: str = ""
    bullets: list[str] = Field(default_factory=list)
    # Ordered in-column blocks (h2/h3/p) — preserves stacked section structure.
    blocks: list[dict[str, str]] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)


class LayoutSection(BaseModel):
    """One logical page section in reading order."""

    model_config = ConfigDict(extra="forbid")

    type: SectionType = "unknown"
    heading: str = ""
    subheading: str = ""
    text: str = ""
    button: str = ""
    button_url: str = ""
    image: str = ""
    images: list[str] = Field(default_factory=list)
    columns: int = 1
    layout: str = ""  # e.g. text_left_image_right
    items: list[LayoutItem] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    # Nested labeled rows (title | content) for services detail pages.
    rows: list[LayoutItem] = Field(default_factory=list)
    # Ordered content blocks for rich columns (preserves stacked h2/p structure).
    blocks: list[dict[str, str]] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)


class PageLayout(BaseModel):
    """Semantic layout for one page."""

    model_config = ConfigDict(extra="forbid")

    page: str = ""
    slug: str = ""
    title: str = ""
    url: str = ""
    sections: list[LayoutSection] = Field(default_factory=list)


class SiteLayoutModel(BaseModel):
    """Full-site semantic layout model (Agent 0 intermediate artifact)."""

    model_config = ConfigDict(extra="forbid")

    source_url: str = ""
    pages: list[PageLayout] = Field(default_factory=list)
