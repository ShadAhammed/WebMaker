"""
webmaker.agents.website_acquisition.agent
=========================================
Agent 0 — Website Acquisition & Validation.

Completely understand the target website and produce a Website Package that is
verified before migration. No AI rewriting. No migration. No SEO.
"""

from __future__ import annotations

import json
from pathlib import Path

from webmaker.agents.base import AgentContext, BaseAgent
from webmaker.agents.website_acquisition.stages import (
    assets as assets_stage,
    brand as brand_stage,
    content as content_stage,
    crawl as crawl_stage,
    html_store as html_stage,
    layout as layout_stage,
    package_writer,
    screenshots as shots_stage,
    validator as validator_stage,
)
from webmaker.core.logging import get_logger
from webmaker.schemas.acquisition import (
    AcquireInput,
    PageContentStats,
    WebsitePackageResult,
)

log = get_logger("agent.acquisition")


class WebsiteAcquisitionAgent(BaseAgent[AcquireInput, WebsitePackageResult]):
    """Agent 0 — acquire + validate website package (deterministic)."""

    name = "website_acquisition"
    input_model = AcquireInput
    output_model = WebsitePackageResult

    def __init__(self, context: AgentContext, *, crawler=None) -> None:
        super().__init__(context)
        self._crawler = crawler

    def _run(self, data: AcquireInput) -> WebsitePackageResult:
        data_dir = Path(self._ctx.data_dir)
        package_dir = data_dir / "website_package"
        package_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        force = bool(data.force_crawl or self._ctx.extras.get("force_crawl"))
        threshold = float(data.threshold or self._ctx.extras.get("threshold") or 0.95)

        # ── Stage 1: Crawl ───────────────────────────────────────────────────
        try:
            crawl_stage.run_crawl(
                data.target_url,
                data_dir,
                settings=self._ctx.settings,
                force=force,
                crawler=self._crawler,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"crawl: {exc}")
            log.error("Crawl failed: {e}", e=exc)

        # ── Stage 2: HTML / DOM ──────────────────────────────────────────────
        html_meta: list = []
        try:
            html_meta = html_stage.process_html(data_dir, package_dir)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"html: {exc}")
            log.error("HTML stage failed: {e}", e=exc)

        # ── Stage 3: Assets ──────────────────────────────────────────────────
        assets: dict = {}
        try:
            assets = assets_stage.inventory_assets(data_dir, package_dir)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"assets: {exc}")
            log.error("Assets stage failed: {e}", e=exc)

        # ── Stage 4: Content ─────────────────────────────────────────────────
        content: dict = {"pages": [], "stats": []}
        try:
            content = content_stage.extract_content(data_dir, package_dir)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"content: {exc}")
            log.error("Content stage failed: {e}", e=exc)

        # ── Stage 5: Brand ───────────────────────────────────────────────────
        brand: dict = {}
        try:
            brand = brand_stage.extract_brand(data_dir, package_dir)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"brand: {exc}")
            log.error("Brand stage failed: {e}", e=exc)

        # ── Stage 6: Layout ──────────────────────────────────────────────────
        sections: dict = {"pages": []}
        try:
            sections = layout_stage.detect_layouts(data_dir, package_dir)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"layout: {exc}")
            log.error("Layout stage failed: {e}", e=exc)

        # Merge section counts into stats
        stats: list[PageContentStats] = list(content.get("stats") or [])
        sec_map = {
            p.get("slug"): p for p in (sections.get("pages") or []) if isinstance(p, dict)
        }
        for i, st in enumerate(stats):
            n = int((sec_map.get(st.slug) or {}).get("section_count") or 0)
            stats[i] = st.model_copy(update={"sections": n})

        # ── Stage 7: Screenshots ─────────────────────────────────────────────
        screenshots: dict = {"pages": [], "notes": []}
        try:
            screenshots = shots_stage.collect_screenshots(
                data_dir,
                package_dir,
                settings=self._ctx.settings,
                target_url=data.target_url,
            )
            # Refresh has_screenshot after copy/capture
            shot_slugs = {
                p.get("slug") for p in screenshots.get("pages") or []
                if p.get("full_page")
            }
            stats = [
                s.model_copy(update={"has_screenshot": s.slug in shot_slugs or s.has_screenshot})
                for s in stats
            ]
        except Exception as exc:  # noqa: BLE001
            errors.append(f"screenshots: {exc}")
            log.error("Screenshots stage failed: {e}", e=exc)

        # ── Stage 8: Package ─────────────────────────────────────────────────
        try:
            package_writer.write_package(
                data_dir,
                package_dir,
                target_url=data.target_url,
                content=content,
                assets=assets,
                brand=brand,
                sections=sections,
                html_meta=html_meta,
                screenshots=screenshots,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"package: {exc}")
            log.error("Package writer failed: {e}", e=exc)

        navigation = {}
        nav_path = package_dir / "navigation.json"
        if nav_path.is_file():
            try:
                navigation = json.loads(nav_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                navigation = {}

        # ── Stage 9: Validation ──────────────────────────────────────────────
        try:
            report = validator_stage.validate_package(
                package_dir,
                stats=stats,
                sections=sections,
                assets=assets,
                brand=brand,
                navigation=navigation,
                threshold=threshold,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"validation: {exc}")
            log.error("Validation failed: {e}", e=exc)
            from webmaker.schemas.acquisition import ValidationReport
            report = ValidationReport(threshold=threshold, passed=False, gaps=errors[:])

        success = bool(stats) and not any(e.startswith("crawl:") for e in errors)

        return WebsitePackageResult(
            target_url=data.target_url,
            package_dir=str(package_dir),
            pages_crawled=len(stats),
            overall_score=report.overall,
            passed=report.passed,
            threshold=threshold,
            per_page_stats=stats,
            gaps=list(report.gaps),
            scores=report.scores,
            success=success,
            errors=errors,
            extras={
                "notes": report.notes,
                "screenshots_notes": screenshots.get("notes") or [],
            },
        )
