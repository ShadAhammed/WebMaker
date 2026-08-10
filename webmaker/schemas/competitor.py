"""
webmaker.schemas.competitor
===========================
Competitor artifacts — output of the CompetitorCrawler agent.

Crawl-only: this captures what each competitor *has*, without comparison. The
comparison / gap analysis is the WebsiteReviewer agent's responsibility.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from webmaker.schemas.base import Artifact


class CompetitorProject(BaseModel):
    """Structural facts captured from a single competitor site."""

    model_config = ConfigDict(extra="forbid")

    url:        str = ""
    name:       str = ""
    strengths:  list[str] = Field(default_factory=list)
    keywords:   list[str] = Field(default_factory=list)
    pages:      list[str] = Field(default_factory=list)
    notes:      str = ""


class CompetitorProjects(Artifact):
    """Collection of crawled competitor projects."""

    model_config = ConfigDict(extra="forbid")

    artifact_name: str = "competitors"

    competitors: list[CompetitorProject] = Field(default_factory=list)
    skipped:     list[str] = Field(default_factory=list)
    errors:      list[str] = Field(default_factory=list)
