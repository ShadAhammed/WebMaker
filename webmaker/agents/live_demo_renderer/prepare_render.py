"""
webmaker.agents.live_demo_renderer.prepare_render
=================================================
Module 5.1 — PrepareRender.

Builds a :class:`RenderRequest` from the APPROVED recommendations plus the
selected theme/template. No AI, no side effects. Only approved recommendations
(``selected=True``) are carried forward.
"""

from __future__ import annotations

from webmaker.core.logging import get_logger
from webmaker.schemas.design import DesignRecommendation
from webmaker.schemas.render import RenderRequest
from webmaker.schemas.review import OpContent

log = get_logger("renderer.prepare")

_STANDARD_PAGES = ("homepage", "about", "services", "contact", "faq")


def build_render_request(
    op_content: OpContent,
    design: DesignRecommendation,
    *,
    regenerate: bool = False,
) -> RenderRequest:
    """Assemble a RenderRequest from approved content + selected design.

    Args:
        op_content:  The reviewed OP-Content with human selections applied.
        design:      The chosen design (theme/template).
        regenerate:  Whether the renderer should wipe + rebuild.

    Returns:
        A RenderRequest containing only approved recommendations.
    """
    approved = op_content.selected_recommendations()
    slugs = sorted({r.page_slug for r in approved if r.page_slug}) or list(_STANDARD_PAGES)

    log.info(
        "PrepareRender: {n} approved recs across {m} page(s); theme={t} template={p}",
        n=len(approved), m=len(slugs),
        t=design.selected_theme, p=design.selected_template,
    )

    return RenderRequest(
        theme_id=design.selected_theme,
        template_id=design.selected_template,
        page_slugs=slugs,
        approved=approved,
        regenerate=regenerate,
    )
