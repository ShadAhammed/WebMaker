"""
webmaker.schemas.migration
==========================
MigrationResult artifact — output of the MigrationAgent.

MigrationAgent performs the faithful "as-is" migration of the target website
into a chosen WordPress theme (after WebsiteAcquisition). No AI is called, no
content is altered. This artifact signals subsequent agents that a base
WordPress site already exists and that ``optimized_*.json`` files contain
verbatim source content.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from webmaker.schemas.base import Artifact


class MigrateInput(BaseModel):
    """Typed input for the MigrationAgent."""

    model_config = ConfigDict(extra="forbid")

    target_url:   str = ""
    theme_id:     str = ""
    template_id:  str = ""


class MigrationResult(Artifact):
    """Outcome of the faithful as-is migration.

    Attributes:
        target_url:     The crawled source URL.
        theme_id:       WordPress theme that was installed.
        template_id:    Starter template that was imported (may be empty).
        wp_url:         Local WordPress demo URL after migration.
        pages_migrated: List of page slugs written to WordPress.
        assets_copied:  Number of images/assets copied from crawl.
        success:        True when WordPress generation succeeded.
        errors:         Non-fatal warnings and fatal errors collected.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_name: str = "migration"

    target_url:     str = ""
    theme_id:       str = ""
    template_id:    str = ""
    wp_url:         str = ""
    pages_migrated: list[str] = Field(default_factory=list)
    assets_copied:  int = 0
    success:        bool = False
    errors:         list[str] = Field(default_factory=list)
