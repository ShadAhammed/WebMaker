"""
webmaker.agents.target_crawler.agent
====================================
Agent 1 — TargetCrawler.

Responsibility: crawl the target website ONLY and derive its business profile.
Does not review, optimise, or generate. No AI in the crawl itself; the business
profile derivation reuses the existing BusinessAnalyzer (Claude) unchanged.

If crawl outputs and ``business_profile.json`` / ``target_business.md`` already
exist, this agent reuses them and does **not** re-call Claude (saves tokens).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from webmaker.agents.base import AgentContext, BaseAgent
from webmaker.core.logging import get_logger
from webmaker.core.types import PageData
from webmaker.schemas.business import BusinessProfile
from webmaker.schemas.target import CrawledPage, TargetProject

log = get_logger("agent.target_crawler")


class CrawlTargetInput(BaseModel):
    """Typed input for the TargetCrawler agent."""

    model_config = ConfigDict(extra="forbid")

    target_url: str


class TargetCrawlerAgent(BaseAgent[CrawlTargetInput, TargetProject]):
    """Crawl the target site and produce a :class:`TargetProject` artifact."""

    name = "target_crawler"
    input_model = CrawlTargetInput
    output_model = TargetProject

    def __init__(self, context: AgentContext, *, crawler=None, analyzer=None) -> None:
        super().__init__(context)
        self._crawler = crawler
        self._analyzer = analyzer

    def _get_crawler(self):
        if self._crawler is None:
            from webmaker.modules.website_crawler import WebsiteCrawler
            self._crawler = WebsiteCrawler(self._ctx.settings)
        return self._crawler

    def _get_analyzer(self):
        if self._analyzer is None:
            from webmaker.modules.business_analyzer import BusinessAnalyzer
            self._analyzer = BusinessAnalyzer(self._ctx.settings)
        return self._analyzer

    def _run(self, data: CrawlTargetInput) -> TargetProject:
        force = bool(self._ctx.extras.get("force_crawl") or self._ctx.extras.get("force_ai"))
        data_dir = Path(self._ctx.data_dir)
        pages_json = data_dir / "json" / "pages.json"
        analyzer = self._get_analyzer()

        # Fast path: reuse crawl + profile without Claude.
        if pages_json.is_file() and not force:
            log.info("Reusing crawl outputs — skipping re-crawl")
            info = analyzer.analyze_from_directory(data_dir, force_ai=False)
            pages = self._pages_from_json(pages_json)
            return TargetProject(
                target_url=data.target_url,
                total_pages=len(pages),
                pages=pages,
                business=BusinessProfile.from_business_info(info),
                errors=[],
            )

        crawler = self._get_crawler()
        crawl_result = crawler.crawl(data.target_url, output_dir=data_dir)
        info = analyzer.analyze(crawl_result, force_ai=force)
        pages = [self._to_crawled_page(p) for p in getattr(crawl_result, "pages", [])]

        return TargetProject(
            target_url=data.target_url,
            total_pages=getattr(crawl_result, "total_pages", 0) or len(pages),
            pages=pages,
            business=BusinessProfile.from_business_info(info),
            errors=list(getattr(crawl_result, "errors", []) or []),
        )

    @staticmethod
    def _pages_from_json(pages_json: Path) -> list[CrawledPage]:
        try:
            raw = json.loads(pages_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(raw, dict):
            if "items" in raw:
                raw = raw["items"]
            elif "data" in raw:
                raw = raw["data"]
            elif "pages" in raw:
                raw = raw["pages"]
        if not isinstance(raw, list):
            return []
        out: list[CrawledPage] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            out.append(
                CrawledPage(
                    url=str(row.get("url") or ""),
                    title=str(row.get("title") or ""),
                    page_type=str(row.get("page_type") or "unknown"),
                    word_count=int(row.get("word_count") or 0),
                    headings=list(row.get("headings") or [])[:20],
                )
            )
        return out

    @staticmethod
    def _to_crawled_page(page: PageData) -> CrawledPage:
        page_type = getattr(page, "page_type", None)
        return CrawledPage(
            url=getattr(page, "url", "") or "",
            title=getattr(page, "title", "") or "",
            page_type=(page_type.value if hasattr(page_type, "value") else str(page_type or "unknown")),
            word_count=len((getattr(page, "text_content", "") or "").split()),
            headings=list(getattr(page, "headings", []) or [])[:20],
        )
