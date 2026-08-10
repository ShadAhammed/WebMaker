"""
webmaker.agents.live_demo_renderer.agent
========================================
Agent 5 — LiveDemoRenderer (the core product path).

Splits internally into three modules:
- ``prepare_render``    (5.1): build a RenderRequest from approved selections
- ``wordpress_renderer`` (5.2): apply the request to WordPress (no AI, no invention)
- ``live_preview``      (5.3): refresh the browser preview

Renders approved content only. Produces a :class:`RenderResult` artifact.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from webmaker.agents.base import AgentContext, BaseAgent
from webmaker.agents.live_demo_renderer.live_preview import refresh_preview
from webmaker.agents.live_demo_renderer.prepare_render import build_render_request
from webmaker.agents.live_demo_renderer.wordpress_renderer import WordPressRenderer
from webmaker.core.logging import get_logger
from webmaker.schemas.design import DesignRecommendation
from webmaker.schemas.render import RenderResult
from webmaker.schemas.review import OpContent

log = get_logger("agent.live_demo_renderer")


class RenderAgentInput(BaseModel):
    """Typed input for the LiveDemoRenderer agent."""

    model_config = ConfigDict(extra="forbid")

    op_content: OpContent
    design: DesignRecommendation = Field(default_factory=DesignRecommendation)


class LiveDemoRendererAgent(BaseAgent[RenderAgentInput, RenderResult]):
    """Render approved content into the live WordPress demo."""

    name = "live_demo_renderer"
    input_model = RenderAgentInput
    output_model = RenderResult

    def __init__(self, context: AgentContext, *, renderer=None) -> None:
        super().__init__(context)
        self._renderer = renderer

    def _run(self, data: RenderAgentInput) -> RenderResult:
        regenerate = bool(self._ctx.extras.get("regenerate", False))
        open_browser = bool(self._ctx.extras.get("open_browser", True))

        # 5.1 PrepareRender
        request = build_render_request(
            data.op_content, data.design, regenerate=regenerate
        )

        # 5.2 WordPressRenderer
        renderer = self._renderer or WordPressRenderer(
            self._ctx.settings, self._ctx.data_dir
        )
        result = renderer.render(request)

        # 5.3 LivePreview
        if result.success:
            refresh_preview(result.wp_url, open_browser=open_browser)

        return result
