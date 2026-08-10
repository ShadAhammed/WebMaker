"""
webmaker.schemas.acquisition
============================
Website Acquisition & Validation artifacts (Agent 0).

Agent 0 only acquires and validates. No AI, no migration, no SEO rewriting.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from webmaker.schemas.base import Artifact


class AcquireInput(BaseModel):
    """Typed input for the WebsiteAcquisitionAgent (Agent 0)."""

    model_config = ConfigDict(extra="forbid")

    target_url: str = ""
    force_crawl: bool = False
    threshold: float = 0.95


class PageContentStats(BaseModel):
    """Per-page extraction counts for the Crawl UI."""

    model_config = ConfigDict(extra="forbid")

    slug: str = ""
    url: str = ""
    title: str = ""
    h1: int = 0
    h2: int = 0
    h3: int = 0
    paragraphs: int = 0
    images: int = 0
    buttons: int = 0
    links: int = 0
    forms: int = 0
    lists: int = 0
    tables: int = 0
    sections: int = 0
    has_screenshot: bool = False
    has_raw_html: bool = False


class ChecklistItem(BaseModel):
    """One validation checklist row for a page."""

    model_config = ConfigDict(extra="forbid")

    key: str = ""
    label: str = ""
    ok: bool = False
    detail: str = ""


class PageValidation(BaseModel):
    """Validation checklist for one page."""

    model_config = ConfigDict(extra="forbid")

    slug: str = ""
    url: str = ""
    items: list[ChecklistItem] = Field(default_factory=list)
    score: float = 0.0


class CategoryScores(BaseModel):
    """Aggregate completeness scores (0.0–1.0)."""

    model_config = ConfigDict(extra="forbid")

    pages: float = 0.0
    text: float = 0.0
    images: float = 0.0
    buttons: float = 0.0
    sections: float = 0.0
    navigation: float = 0.0
    brand_assets: float = 0.0


class ValidationReport(BaseModel):
    """Stage 9 — Website Acquisition Report."""

    model_config = ConfigDict(extra="forbid")

    threshold: float = 0.95
    overall: float = 0.0
    passed: bool = False
    scores: CategoryScores = Field(default_factory=CategoryScores)
    pages: list[PageValidation] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class WebsitePackageResult(Artifact):
    """Orchestrator artifact for Agent 0 acquisition."""

    model_config = ConfigDict(extra="forbid")

    artifact_name: str = "acquisition"

    target_url: str = ""
    package_dir: str = ""
    pages_crawled: int = 0
    overall_score: float = 0.0
    passed: bool = False
    threshold: float = 0.95
    per_page_stats: list[PageContentStats] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    scores: CategoryScores = Field(default_factory=CategoryScores)
    success: bool = False
    errors: list[str] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)
