"""
webmaker.schemas.business
=========================
BusinessProfile artifact — the structured facts about the target business.

Aligned field-for-field with the legacy :class:`webmaker.core.types.BusinessInfo`
so existing modules can convert cleanly via :meth:`BusinessProfile.from_business_info`.
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from webmaker.core.types import BusinessInfo
from webmaker.schemas.base import Artifact


class BusinessProfile(Artifact):
    """Structured business facts extracted from the target crawl.

    This mirrors :class:`BusinessInfo` but is an :class:`Artifact` so it can be
    persisted and versioned on its own or embedded in :class:`TargetProject`.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_name: str = "business_profile"

    name:            str            = ""
    industry:        str            = ""
    location:        str            = ""
    services:        list[str]      = Field(default_factory=list)
    target_audience: str            = ""
    unique_value:    str            = ""
    tone_of_voice:   str            = ""
    primary_color:   str            = ""
    contact_email:   str            = ""
    contact_phone:   str            = ""
    social_links:    dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_business_info(cls, info: BusinessInfo) -> "BusinessProfile":
        """Build a BusinessProfile from a legacy :class:`BusinessInfo`."""
        return cls(
            name=info.name,
            industry=info.industry,
            location=info.location,
            services=list(info.services),
            target_audience=info.target_audience,
            unique_value=info.unique_value,
            tone_of_voice=info.tone_of_voice,
            primary_color=info.primary_color,
            contact_email=info.contact_email,
            contact_phone=info.contact_phone,
            social_links=dict(info.social_links),
        )

    def to_business_info(self) -> BusinessInfo:
        """Convert back to a legacy :class:`BusinessInfo` for existing modules."""
        return BusinessInfo(
            name=self.name,
            industry=self.industry,
            location=self.location,
            services=list(self.services),
            target_audience=self.target_audience,
            unique_value=self.unique_value,
            tone_of_voice=self.tone_of_voice,
            primary_color=self.primary_color,
            contact_email=self.contact_email,
            contact_phone=self.contact_phone,
            social_links=dict(self.social_links),
        )
