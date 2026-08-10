"""
webmaker.schemas.review
=======================
OP-Content artifacts — output of the WebsiteReviewer agent (the intelligence
layer). This is what the TK "OP-Content" tab renders as tickable cards.

Each :class:`Recommendation` carries the exact fields required by the V2 spec:
Current / Issue / Recommendation / Reason / Source / Priority / Selected.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from webmaker.schemas.base import Artifact

Priority = Literal["critical", "high", "medium", "low"]


class Recommendation(BaseModel):
    """A single, human-approvable improvement recommendation.

    Only recommendations with ``selected=True`` reach the rendering stage.
    """

    model_config = ConfigDict(extra="forbid")

    id:             str = ""
    page_slug:      str = ""
    section:        str = ""
    current:        str = ""
    issue:          str = ""
    recommendation: str = ""
    reason:         str = ""
    source:         str = ""
    priority:       Priority = "medium"
    selected:       bool = False
    # Optional ready-to-apply content for the renderer (never invented facts).
    proposed_html:  str = ""


class SectionReview(BaseModel):
    """Review of one section of one page, with its recommendations."""

    model_config = ConfigDict(extra="forbid")

    page_slug:       str = ""
    section:         str = ""
    summary:         str = ""
    recommendations: list[Recommendation] = Field(default_factory=list)


class OpContent(Artifact):
    """The full optimisation-content review across all pages."""

    model_config = ConfigDict(extra="forbid")

    artifact_name: str = "op_content"

    sections:     list[SectionReview] = Field(default_factory=list)
    page_slugs:   list[str] = Field(default_factory=list)
    summary:      str = ""

    def all_recommendations(self) -> list[Recommendation]:
        """Flatten every recommendation across all sections."""
        out: list[Recommendation] = []
        for sec in self.sections:
            out.extend(sec.recommendations)
        return out

    def selected_recommendations(self) -> list[Recommendation]:
        """Return only the recommendations the human approved."""
        return [r for r in self.all_recommendations() if r.selected]
