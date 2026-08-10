"""
webmaker.core.types
===================
Shared data models and enumerations used across all WebMaker modules.

All types are Pydantic v2 models or standard enums.
No business logic lives here — only data shapes.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ── Enumerations ──────────────────────────────────────────────────────────────

class ProjectStatus(str, Enum):
    """Lifecycle stages of a WebMaker project."""

    PENDING    = "pending"
    CRAWLING   = "crawling"
    ANALYZING  = "analyzing"
    COMPETING  = "competing"
    OPTIMIZING = "optimizing"
    GENERATING = "generating"
    REVIEWING  = "reviewing"
    FIXING     = "fixing"
    COMPLETED  = "completed"
    FAILED     = "failed"


class AIProvider(str, Enum):
    """Supported AI providers."""

    GEMINI   = "gemini"
    CLAUDE   = "claude"
    DEEPSEEK = "deepseek"
    OPENAI   = "openai"
    AUTO     = "auto"


class PageType(str, Enum):
    """Classified page types discovered during crawling."""

    HOME       = "home"
    ABOUT      = "about"
    SERVICES   = "services"
    CONTACT    = "contact"
    BLOG       = "blog"
    PRODUCT    = "product"
    GALLERY    = "gallery"
    UNKNOWN    = "unknown"


# ── Project ───────────────────────────────────────────────────────────────────

class ProjectConfig(BaseModel):
    """Persistent configuration for a single WebMaker project."""

    id:          str
    name:        str
    target_url:  str
    status:      ProjectStatus = ProjectStatus.PENDING
    created_at:  datetime      = Field(default_factory=datetime.utcnow)
    updated_at:  datetime      = Field(default_factory=datetime.utcnow)
    output_dir:  Path          = Path("outputs")
    notes:       str           = ""
    metadata:    dict[str, Any] = Field(default_factory=dict)


# ── Crawler output ────────────────────────────────────────────────────────────

class AssetReference(BaseModel):
    """Reference to a discovered static asset (image, CSS, font, etc.)."""

    url:          str
    asset_type:   str          # image | stylesheet | script | font | video
    local_path:   Path | None  = None
    size_bytes:   int          = 0


class PageData(BaseModel):
    """Data extracted from a single crawled page."""

    url:              str
    title:            str              = ""
    description:      str              = ""
    page_type:        PageType         = PageType.UNKNOWN
    html:             str              = ""
    text_content:     str              = ""
    headings:         list[str]        = Field(default_factory=list)
    links:            list[str]        = Field(default_factory=list)
    images:           list[str]        = Field(default_factory=list)
    assets:           list[AssetReference] = Field(default_factory=list)
    screenshot_path:  Path | None      = None
    status_code:      int              = 200
    crawled_at:       datetime         = Field(default_factory=datetime.utcnow)


class CrawlResult(BaseModel):
    """Aggregated output from a full website crawl."""

    target_url:      str
    pages:           list[PageData]    = Field(default_factory=list)
    total_pages:     int               = 0
    crawl_duration_s: float            = 0.0
    errors:          list[str]         = Field(default_factory=list)
    completed_at:    datetime          = Field(default_factory=datetime.utcnow)


# ── Analysis output ───────────────────────────────────────────────────────────

class BusinessInfo(BaseModel):
    """Structured business information extracted from the crawl."""

    name:           str              = ""
    industry:       str              = ""
    location:       str              = ""
    services:       list[str]        = Field(default_factory=list)
    target_audience: str             = ""
    unique_value:   str              = ""
    tone_of_voice:  str              = ""
    primary_color:  str              = ""
    contact_email:  str              = ""
    contact_phone:  str              = ""
    social_links:   dict[str, str]   = Field(default_factory=dict)


class CompetitorInfo(BaseModel):
    """Basic information about a discovered competitor."""

    url:        str
    name:       str              = ""
    strengths:  list[str]        = Field(default_factory=list)
    weaknesses: list[str]        = Field(default_factory=list)
    keywords:   list[str]        = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """Combined output from business and competitor analysis."""

    business:        BusinessInfo
    competitors:     list[CompetitorInfo] = Field(default_factory=list)
    content_gaps:    list[str]            = Field(default_factory=list)
    recommendations: list[str]            = Field(default_factory=list)
    analyzed_at:     datetime             = Field(default_factory=datetime.utcnow)


# ── Generation output ─────────────────────────────────────────────────────────

class GenerationResult(BaseModel):
    """Outcome of the WordPress site generation phase."""

    wp_url:          str
    wp_path:         Path
    theme_slug:      str              = ""
    plugins_installed: list[str]      = Field(default_factory=list)
    pages_created:   list[str]        = Field(default_factory=list)
    admin_url:       str              = ""
    generated_at:    datetime         = Field(default_factory=datetime.utcnow)
    success:         bool             = False
    errors:          list[str]        = Field(default_factory=list)


# ── QA output ────────────────────────────────────────────────────────────────

class QACheck(BaseModel):
    """Result of a single QA check."""

    name:    str
    passed:  bool
    score:   float    = 0.0          # 0.0 – 1.0
    detail:  str      = ""


class QAReport(BaseModel):
    """Full QA review report for a generated WordPress site."""

    wp_url:          str
    checks:          list[QACheck]   = Field(default_factory=list)
    overall_score:   float           = 0.0
    recommendations: list[str]       = Field(default_factory=list)
    reviewed_at:     datetime        = Field(default_factory=datetime.utcnow)
    passed:          bool            = False
