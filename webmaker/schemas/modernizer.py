"""
webmaker.schemas.modernizer
===========================
Agent 1 — Intelligent Website Modernizer (Creative Director).

Uses Website Package + Theme/Template + Design Library → Design Blueprint →
premium WordPress demo.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from webmaker.schemas.base import Artifact


class ModernizeInput(BaseModel):
    """Typed input for the WebsiteModernizerAgent."""

    model_config = ConfigDict(extra="forbid")

    target_url:  str = ""
    theme_id:    str = ""
    template_id: str = ""


class BlueprintSection(BaseModel):
    """One section choice in the Design Blueprint."""

    model_config = ConfigDict(extra="forbid")

    section: str = ""
    reference: str = ""
    reference_path: str = ""
    reason: str = ""
    layout_notes: str = ""
    include: bool = True
    client_content: dict[str, Any] = Field(default_factory=dict)


class DesignBlueprint(BaseModel):
    """Creative Director blueprint before page population."""

    model_config = ConfigDict(extra="forbid")

    creative_direction: str = ""
    sections: list[BlueprintSection] = Field(default_factory=list)
    heuristic: bool = False


class ModernizeResult(Artifact):
    """Output of the Intelligent Website Modernizer (Agent 1).

    Attributes:
        target_url:         Source URL that was modernized.
        theme_id:           Installed WordPress theme.
        template_id:        Starter template imported (may be empty).
        wp_url:             Local WordPress demo URL.
        pages_built:        Slugs of pages written to WordPress.
        assets_copied:      Number of local images imported.
        mapping_summary:    One-line Claude summary of what was mapped.
        design_notes:       How Design Library patterns informed the layout.
        vision_summary:     What Claude saw in Design Library screenshots.
        vision_images:      Number of screenshots sent to Claude vision.
        vision_used:        True when Claude vision analyzed library screenshots.
        blueprint_path:     Path to persisted design_blueprint.json.
        blueprint_sections: Compact list of section→reference choices.
        library_refs_used:  Count of Design Library references chosen.
        ai_used:            True when Claude content mapping was invoked.
        success:            True when WordPress generation succeeded.
        errors:             Non-fatal warnings and fatal errors.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_name: str = "modernizer"

    target_url:         str  = ""
    theme_id:           str  = ""
    template_id:        str  = ""
    wp_url:             str  = ""
    pages_built:        list[str] = Field(default_factory=list)
    assets_copied:      int  = 0
    mapping_summary:    str  = ""
    design_notes:       str  = ""
    vision_summary:     str  = ""
    vision_images:      int  = 0
    vision_used:        bool = False
    blueprint_path:     str  = ""
    blueprint_sections: list[str] = Field(default_factory=list)
    library_refs_used:  int  = 0
    ai_used:            bool = False
    success:            bool = False
    errors:             list[str] = Field(default_factory=list)
