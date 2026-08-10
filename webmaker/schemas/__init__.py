"""
webmaker.schemas
================
Strict Pydantic v2 artifact schemas exchanged between agents.

Rules (WebMaker V2):
- Every artifact is a Pydantic model — never a bare dict.
- Every artifact embeds :class:`ArtifactMeta` for deterministic persistence.
- Agents read prior artifacts as typed inputs and emit their own artifact.
- No ``Any`` on agent boundaries.

Each module owns one artifact family; import from here for convenience::

    from webmaker.schemas import TargetProject, OpContent, Recommendation
"""

from __future__ import annotations

from webmaker.schemas.acquisition import (
    AcquireInput,
    CategoryScores,
    PageContentStats,
    ValidationReport,
    WebsitePackageResult,
)
from webmaker.schemas.base import Artifact, ArtifactMeta
from webmaker.schemas.business import BusinessProfile
from webmaker.schemas.competitor import CompetitorProject, CompetitorProjects
from webmaker.schemas.design import DesignRecommendation, PatternSelection, ThemeOption
from webmaker.schemas.migration import MigrateInput, MigrationResult
from webmaker.schemas.modernizer import (
    BlueprintSection,
    DesignBlueprint,
    ModernizeInput,
    ModernizeResult,
)
from webmaker.schemas.qa import QAArtifact
from webmaker.schemas.render import RenderRequest, RenderResult
from webmaker.schemas.review import OpContent, Recommendation, SectionReview
from webmaker.schemas.target import TargetProject

__all__ = [
    "AcquireInput",
    "Artifact",
    "ArtifactMeta",
    "BusinessProfile",
    "CategoryScores",
    "CompetitorProject",
    "CompetitorProjects",
    "DesignRecommendation",
    "MigrateInput",
    "MigrationResult",
    "ModernizeInput",
    "ModernizeResult",
    "BlueprintSection",
    "DesignBlueprint",
    "PageContentStats",
    "PatternSelection",
    "ThemeOption",
    "OpContent",
    "QAArtifact",
    "Recommendation",
    "RenderRequest",
    "RenderResult",
    "SectionReview",
    "TargetProject",
    "ValidationReport",
    "WebsitePackageResult",
]
