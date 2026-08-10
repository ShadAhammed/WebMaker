"""
webmaker.schemas.qa
===================
QA artifact — output of the QAReviewer agent.

V2 dual-model report:
- Claude Sonnet 4.6 → content / SEO / German writing (``content_review``)
- GPT → layout / UX / visual quality (``visual_review``)
- Merged recommendations in ``recommendations``
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from webmaker.schemas.base import Artifact


class QACheckResult(BaseModel):
    """Result of a single QA check."""

    model_config = ConfigDict(extra="forbid")

    name:   str = ""
    passed: bool = False
    score:  float = 0.0
    detail: str = ""


class QAArtifact(Artifact):
    """Aggregated QA review of the live demo (Claude content + GPT visual)."""

    model_config = ConfigDict(extra="forbid")

    artifact_name: str = "qa"

    wp_url:          str = ""
    overall_score:   float = 0.0
    passed:          bool = False
    checks:          list[QACheckResult] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    # Claude Sonnet — content / SEO / German writing summary.
    content_review:  str = ""
    # GPT — layout / UX / visual quality summary.
    visual_review:   str = ""
