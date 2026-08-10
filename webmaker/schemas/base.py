"""
webmaker.schemas.base
=====================
Shared foundations for every artifact schema.

``ArtifactMeta`` is embedded in every artifact so the orchestrator can persist,
version, and audit artifacts deterministically. ``Artifact`` is the base class
all concrete artifacts inherit from.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class ArtifactMeta(BaseModel):
    """Provenance metadata embedded in every artifact.

    Attributes:
        artifact_id:    Stable identifier (``<project>:<agent>``).
        project:        Project slug the artifact belongs to.
        agent:          Name of the agent that produced the artifact.
        schema_version: Version of the artifact schema (bump on breaking change).
        created_at:     ISO-8601 UTC creation timestamp.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_id:    str = ""
    project:        str = ""
    agent:          str = ""
    schema_version: str = "1.0"
    created_at:     str = Field(default_factory=_utc_now_iso)


class Artifact(BaseModel):
    """Base class for all agent artifacts.

    Every artifact carries an :class:`ArtifactMeta`. Concrete artifacts declare
    a class-level ``artifact_name`` used as the on-disk filename stem.
    """

    model_config = ConfigDict(extra="forbid")

    # Filename stem under projects/<slug>/artifacts/ — overridden per artifact.
    artifact_name: str = "artifact"

    meta: ArtifactMeta = Field(default_factory=ArtifactMeta)
