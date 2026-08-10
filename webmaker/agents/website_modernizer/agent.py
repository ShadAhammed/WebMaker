"""
webmaker.agents.website_modernizer.agent
========================================
Agent 1 — Intelligent Website Modernizer (Creative Director).

Pipeline
--------
1.  Ensure crawl / website_package from Agent 0 exists.
2.  Index WebMaker Design Library (``Library/``).
3.  Build Website Package context for Claude.
4.  Create Design Blueprint (Claude studies library refs per section).
5.  Map client content into blueprint sections (Claude).
6.  Build premium Gutenberg ``optimized_*.json`` pages.
7.  Install theme + starter template (best-effort).
8.  Generate WordPress demo + open preview.
9.  Emit ModernizeResult (+ persist design_blueprint.json).

Fallback
--------
If Claude fails: heuristic blueprint from library + deterministic layout
pipeline so the demo still generates.
"""

from __future__ import annotations

import json
from pathlib import Path

from webmaker.agents.base import AgentContext, BaseAgent
from webmaker.agents.website_modernizer.content_mapper import map_content
from webmaker.agents.website_modernizer.design_blueprint import (
    create_design_blueprint,
    heuristic_blueprint,
    save_blueprint,
)
from webmaker.agents.website_modernizer.library_index import index_library
from webmaker.agents.website_modernizer.page_builder import build_pages
from webmaker.agents.website_modernizer.prompt_builder import build_context
from webmaker.core.logging import get_logger
from webmaker.schemas.modernizer import ModernizeInput, ModernizeResult

log = get_logger("agent.modernizer")


class WebsiteModernizerAgent(BaseAgent[ModernizeInput, ModernizeResult]):
    """Agent 1 — Creative Director: Design Library → Blueprint → WP demo."""

    name         = "website_modernizer"
    input_model  = ModernizeInput
    output_model = ModernizeResult

    def __init__(
        self,
        context: AgentContext,
        *,
        router=None,
        generator=None,
    ) -> None:
        super().__init__(context)
        self._router    = router
        self._generator = generator

    def _get_router(self):
        if self._router is None:
            from webmaker.modules.ai_router import AIRouter
            self._router = AIRouter(self._ctx.settings)
        return self._router

    def _get_generator(self):
        if self._generator is None:
            from webmaker.modules.wordpress_generator import WordPressGenerator
            self._generator = WordPressGenerator(self._ctx.settings)
        return self._generator

    def _run(self, data: ModernizeInput) -> ModernizeResult:
        data_dir    = Path(self._ctx.data_dir)
        wp_url      = getattr(self._ctx.settings, "wordpress_url", "")
        errors:     list[str] = []
        ai_used     = False
        mapping_sum = ""
        design_notes = ""
        blueprint: dict | None = None
        blueprint_path = ""
        blueprint_lines: list[str] = []
        library_refs_used = 0

        # ── 1. Ensure crawl data exists ───────────────────────────────────────
        package_dir = data_dir / "website_package"
        pages_json  = data_dir / "json" / "pages.json"
        force_crawl = bool(self._ctx.extras.get("force_crawl"))

        if not pages_json.is_file() or force_crawl:
            log.info("No crawl data — running WebsiteCrawler for {url}", url=data.target_url)
            try:
                from webmaker.modules.website_crawler import WebsiteCrawler
                WebsiteCrawler(self._ctx.settings).crawl(
                    data.target_url, output_dir=data_dir
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"crawl: {exc}")
                log.error("Crawl failed: {e}", e=exc)

        # ── 2. Theme catalog ──────────────────────────────────────────────────
        theme_entry = None
        try:
            from webmaker.data.theme_catalog import get_theme
            theme_entry = get_theme(data.theme_id)
        except Exception:  # noqa: BLE001
            pass

        # ── 3. Index Design Library ───────────────────────────────────────────
        log.info("Indexing Design Library…")
        catalog = index_library(settings=self._ctx.settings)
        if not catalog.references:
            errors.append("design library: empty or missing Library/")
            log.warning("Design Library empty — blueprint will be thin")

        # ── 4. Website Package context ────────────────────────────────────────
        if not package_dir.is_dir():
            log.warning("website_package/ missing at {p}", p=package_dir)

        log.info("Building prompt context from website_package…")
        context_str = build_context(
            package_dir if package_dir.is_dir() else data_dir,
            data.theme_id,
            data.template_id,
            theme_entry,
        )
        log.info("Context built ({n} chars)", n=len(context_str))

        # ── 5. Design Blueprint (study library WITH vision) ───────────────────
        log.info("Creating Design Blueprint — Claude vision on Design Library screenshots…")
        router = self._get_router()
        try:
            blueprint = create_design_blueprint(
                business_context=context_str,
                catalog=catalog,
                router=router,
                theme_id=data.theme_id,
                template_id=data.template_id,
                use_vision=True,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"design blueprint: {exc}")
            log.error("Blueprint error: {e}", e=exc)
            blueprint = None

        if not blueprint:
            blueprint = heuristic_blueprint(catalog, business_context=context_str)

        vision_used = bool(blueprint.get("vision_used"))
        vision_images = int(blueprint.get("vision_images") or 0)
        vision_summary = str(blueprint.get("vision_summary") or "")
        if vision_used:
            log.info(
                "Vision analysis complete — {n} screenshots studied",
                n=vision_images,
            )
        else:
            log.warning("Vision not used for blueprint (text/heuristic only)")

        try:
            bp_path = save_blueprint(blueprint, data_dir)
            blueprint_path = str(bp_path)
            log.info("Design Blueprint saved → {p}", p=bp_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"blueprint save: {exc}")

        for sec in blueprint.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            flag = "✓" if sec.get("include", True) else "–"
            line = f"{flag} {sec.get('section')}: {sec.get('reference')} — {sec.get('reason', '')[:90]}"
            blueprint_lines.append(line)
            if sec.get("reference"):
                library_refs_used += 1
            log.info("Blueprint {line}", line=line)

        # ── 6. Content mapping (populate blueprint) ───────────────────────────
        content_map: dict | None = None
        log.info("Calling Claude to populate Design Blueprint with client content…")
        try:
            content_map = map_content(
                context_str,
                router=router,
                theme_id=data.theme_id,
                template_id=data.template_id,
                blueprint=blueprint,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"content mapping: {exc}")
            log.error("Content mapping error: {e}", e=exc)

        # ── 7. Build optimized_*.json ──────────────────────────────────────────
        pages_built: list[str] = []
        if content_map:
            ai_used      = True
            mapping_sum  = content_map.get("mapping_summary", "")
            design_notes = content_map.get("design_notes", "")
            log.info(
                "Content map received — {n} pages — {s}",
                n=len((content_map.get("pages") or {}).keys()),
                s=mapping_sum[:80],
            )
            try:
                # Persist for rebuilds without re-calling Claude
                try:
                    (data_dir / "json").mkdir(parents=True, exist_ok=True)
                    (data_dir / "json" / "content_map.json").write_text(
                        json.dumps(content_map, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
                pages_built = build_pages(
                    content_map,
                    data_dir,
                    theme_id=data.theme_id,
                    images_dir=data_dir / "images",
                    blueprint=blueprint,
                )
                log.info("Pages built: {slugs}", slugs=pages_built)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"page build: {exc}")
                log.error("Page builder failed: {e}", e=exc)

        if not pages_built:
            log.warning("AI mapping unavailable — falling back to layout pipeline")
            try:
                from webmaker.agents.migration_agent.pipeline import (
                    write_layout_migrated_pages,
                )
                pages_built = write_layout_migrated_pages(
                    data_dir,
                    theme_id=data.theme_id or "kadence",
                    source_url=data.target_url,
                )
                log.info("Fallback pipeline wrote {n} pages", n=len(pages_built))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"fallback pipeline: {exc}")
                log.error("Fallback pipeline failed: {e}", e=exc)

        # ── 8. Assets ─────────────────────────────────────────────────────────
        images_dir    = data_dir / "images"
        assets_copied = len(list(images_dir.glob("*.*"))) if images_dir.is_dir() else 0

        # ── 9. Theme + template ───────────────────────────────────────────────
        generator = self._get_generator()
        if data.theme_id:
            log.info("Installing theme: {t}", t=data.theme_id)
            try:
                generator.install_theme_stack(data.theme_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"theme install: {exc}")
                log.error("Theme install error (continuing): {e}", e=exc)

            if data.template_id:
                log.info("Importing starter template: {p}", p=data.template_id)
                try:
                    generator.import_starter_template(data.template_id, data.theme_id)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"template import (non-fatal): {exc}")
                    log.warning("Template import non-fatal: {e}", e=exc)

        # ── 10. WordPress generate ────────────────────────────────────────────
        log.info("Generating WordPress demo…")
        success = False
        try:
            if data.theme_id and data.template_id:
                result = generator.hydrate_template_content(
                    data_dir,
                    page_slugs=pages_built or None,
                )
            else:
                result = generator.generate_from_directory(
                    data_dir,
                    reset=True,
                    update_only=False,
                )
            if getattr(result, "errors", None):
                errors.extend(str(e) for e in result.errors)
            success = bool(getattr(result, "success", False))
            wp_url  = getattr(result, "wp_url", "") or wp_url
            log.info(
                "WP generation done — success={s} pages={n}",
                s=success,
                n=len(getattr(result, "pages_created", [])),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"wordpress generation: {exc}")
            log.error("WP generation error: {e}", e=exc)

        # ── 11. Preview ───────────────────────────────────────────────────────
        open_browser = bool(self._ctx.extras.get("open_browser", True))
        if success and open_browser:
            try:
                from webmaker.agents.live_demo_renderer.live_preview import refresh_preview
                refresh_preview(wp_url, open_browser=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not open browser preview: {e}", e=exc)

        return ModernizeResult(
            target_url=data.target_url,
            theme_id=data.theme_id,
            template_id=data.template_id,
            wp_url=wp_url,
            pages_built=pages_built,
            assets_copied=assets_copied,
            mapping_summary=mapping_sum,
            design_notes=design_notes,
            vision_summary=vision_summary,
            vision_images=vision_images,
            vision_used=vision_used,
            blueprint_path=blueprint_path,
            blueprint_sections=blueprint_lines[:20],
            library_refs_used=library_refs_used,
            ai_used=ai_used,
            success=success,
            errors=errors,
        )
