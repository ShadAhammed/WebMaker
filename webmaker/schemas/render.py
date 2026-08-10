"""
webmaker.schemas.render
=======================
Render artifacts — input/output of the LiveDemoRenderer agent.

``RenderRequest`` is built by the PrepareRender module from *approved*
recommendations + the selected theme/template. ``RenderResult`` reports what
the WordPressRenderer applied. The renderer never invents content.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from webmaker.schemas.base import Artifact
from webmaker.schemas.review import Recommendation


class RenderRequest(Artifact):
    """Everything the renderer needs to update the live demo."""

    model_config = ConfigDict(extra="forbid")

    artifact_name: str = "render_request"

    theme_id:    str = ""
    template_id: str = ""
    page_slugs:  list[str] = Field(default_factory=list)
    # Approved recommendations only (selected=True upstream).
    approved:    list[Recommendation] = Field(default_factory=list)
    regenerate:  bool = False


class RenderResult(Artifact):
    """Outcome of a render pass against the local WordPress demo."""

    model_config = ConfigDict(extra="forbid")

    artifact_name: str = "render_result"

    wp_url:          str = ""
    pages_rendered:  list[str] = Field(default_factory=list)
    theme_applied:   str = ""
    template_applied: str = ""
    success:         bool = False
    errors:          list[str] = Field(default_factory=list)
