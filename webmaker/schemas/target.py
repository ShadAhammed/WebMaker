"""
webmaker.schemas.target
========================
TargetProject artifact — output of the TargetCrawler agent.

Bundles the crawled page inventory with the derived business profile. Page
bodies are intentionally summarised (url/title/type/word_count) to keep the
artifact small; full page text lives in the project's ``json/`` crawl outputs.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from webmaker.schemas.base import Artifact
from webmaker.schemas.business import BusinessProfile


class CrawledPage(BaseModel):
    """Lightweight summary of a single crawled page."""

    model_config = ConfigDict(extra="forbid")

    url:        str = ""
    title:      str = ""
    page_type:  str = "unknown"
    word_count: int = 0
    headings:   list[str] = Field(default_factory=list)


class TargetProject(Artifact):
    """The crawled target website plus its derived business profile."""

    model_config = ConfigDict(extra="forbid")

    artifact_name: str = "target"

    target_url:  str = ""
    total_pages: int = 0
    pages:       list[CrawledPage] = Field(default_factory=list)
    business:    BusinessProfile = Field(default_factory=BusinessProfile)
    errors:      list[str] = Field(default_factory=list)
