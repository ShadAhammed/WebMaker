"""
webmaker.agents.competitor_crawler.agent
========================================
Agent 2 — CompetitorCrawler.

Responsibility: crawl competitor websites ONLY. Does not compare (that is the
WebsiteReviewer's job). URLs already documented in the competitor ``.md`` are
skipped by the underlying analyzer. No AI in this agent's own logic.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from webmaker.agents.base import AgentContext, BaseAgent
from webmaker.core.logging import get_logger
from webmaker.core.types import CompetitorInfo
from webmaker.schemas.competitor import CompetitorProject, CompetitorProjects

log = get_logger("agent.competitor_crawler")


class CrawlCompetitorsInput(BaseModel):
    """Typed input for the CompetitorCrawler agent."""

    model_config = ConfigDict(extra="forbid")

    competitor_urls: list[str] = Field(default_factory=list)


class CompetitorCrawlerAgent(BaseAgent[CrawlCompetitorsInput, CompetitorProjects]):
    """Crawl competitors and produce a :class:`CompetitorProjects` artifact."""

    name = "competitor_crawler"
    input_model = CrawlCompetitorsInput
    output_model = CompetitorProjects

    def __init__(self, context: AgentContext, *, analyzer=None) -> None:
        super().__init__(context)
        self._analyzer = analyzer

    def _get_analyzer(self):
        if self._analyzer is None:
            from webmaker.modules.competitor_analyzer import CompetitorAnalyzer
            self._analyzer = CompetitorAnalyzer(self._ctx.settings)
        return self._analyzer

    def _run(self, data: CrawlCompetitorsInput) -> CompetitorProjects:
        urls = [u.strip() for u in data.competitor_urls if str(u).strip()]
        if not urls:
            log.warning("No competitor URLs provided — producing empty artifact")
            return CompetitorProjects(
                competitors=[],
                errors=["No competitor URLs provided"],
            )

        analyzer = self._get_analyzer()
        force = bool(self._ctx.extras.get("force_crawl") or self._ctx.extras.get("force_ai"))
        result = analyzer.analyze_from_urls(
            urls, self._ctx.data_dir, force=force,
        )

        competitors = [
            self._to_competitor_project(c)
            for c in getattr(result, "competitors", []) or []
        ]

        return CompetitorProjects(competitors=competitors)

    @staticmethod
    def _to_competitor_project(info: CompetitorInfo) -> CompetitorProject:
        return CompetitorProject(
            url=getattr(info, "url", "") or "",
            name=getattr(info, "name", "") or "",
            strengths=list(getattr(info, "strengths", []) or []),
            keywords=list(getattr(info, "keywords", []) or []),
        )
