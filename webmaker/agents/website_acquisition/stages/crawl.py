"""
Stage 1 — Crawl via WebsiteCrawler (no AI).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from webmaker.core.logging import get_logger

log = get_logger("acquisition.crawl")


def run_crawl(
    target_url: str,
    data_dir: Path,
    *,
    settings: object,
    force: bool = False,
    crawler: Any = None,
) -> dict[str, Any]:
    """Crawl the site into the project data dir. Reuse pages.json unless force."""
    data_dir = Path(data_dir)
    pages_json = data_dir / "json" / "pages.json"
    if pages_json.is_file() and not force:
        log.info("pages.json present — reusing crawl (set force_crawl to re-crawl)")
        return {"reused": True, "pages_json": str(pages_json)}

    if crawler is None:
        from webmaker.modules.website_crawler import WebsiteCrawler
        crawler = WebsiteCrawler(settings)

    log.info("Crawling {url}…", url=target_url)
    result = crawler.crawl(target_url, output_dir=data_dir)
    summary = {
        "reused": False,
        "total_pages": getattr(result, "total_pages", 0)
        or getattr(result, "successful_pages", 0),
        "errors": list(getattr(result, "errors", []) or []),
    }
    log.info("Crawl complete — pages={n}", n=summary["total_pages"])
    return summary
