"""
webmaker.agents.migration_agent.agent
======================================
Agent 0 — MigrationAgent.

Responsibility
--------------
Faithful "as-is" migration of the target website into the chosen WordPress
theme.  No AI is called.  No content is changed or invented.  The operator
sees the client's real site in a professional theme immediately, without
waiting for AI analysis.

Pipeline (all deterministic — zero AI calls)
--------------------------------------------
1.  Crawl the target site via WebsiteCrawler
    (skipped when ``pages.json`` already exists and ``force_crawl`` is False).
2.  Universal layout migration (internal only — external API unchanged)::

       raw HTML → DOM Extractor → Layout Analyzer → Semantic Layout Model
                → Theme Mapper → ``optimized_*.json`` (verbatim content)

3.  Install the chosen theme and activate it via WP-CLI.
4.  Import starter template when ``template_id`` is provided (best-effort;
    failure is logged but does not abort the migration).
5.  Generate WordPress pages from the ``optimized_*.json`` files.
6.  Open/refresh the browser preview.
7.  Emit a :class:`~webmaker.schemas.migration.MigrationResult` artifact.

After this agent completes, the operator can:
- Directly inspect the migrated site (nothing missing, nothing invented).
- Run the OP-Content tab to get AI recommendations and apply improvements
  on top of the already-working site.
"""

from __future__ import annotations

from pathlib import Path

from webmaker.agents.base import AgentContext, BaseAgent
from webmaker.agents.migration_agent.pipeline import write_layout_migrated_pages
from webmaker.core.logging import get_logger
from webmaker.schemas.migration import MigrateInput, MigrationResult

log = get_logger("agent.migration")


class MigrationAgent(BaseAgent[MigrateInput, MigrationResult]):
    """Agent 0 — faithful content migration into the chosen WordPress theme."""

    name         = "migration_agent"
    input_model  = MigrateInput
    output_model = MigrationResult

    def __init__(
        self,
        context: AgentContext,
        *,
        crawler=None,
        generator=None,
    ) -> None:
        super().__init__(context)
        self._crawler   = crawler
        self._generator = generator

    # ── Lazy dependency factories ────────────────────────────────────────────

    def _get_crawler(self):
        if self._crawler is None:
            from webmaker.modules.website_crawler import WebsiteCrawler
            self._crawler = WebsiteCrawler(self._ctx.settings)
        return self._crawler

    def _get_generator(self):
        if self._generator is None:
            from webmaker.modules.wordpress_generator import WordPressGenerator
            self._generator = WordPressGenerator(self._ctx.settings)
        return self._generator

    # ── Main ─────────────────────────────────────────────────────────────────

    def _run(self, data: MigrateInput) -> MigrationResult:
        data_dir  = Path(self._ctx.data_dir)
        wp_url    = getattr(self._ctx.settings, "wordpress_url", "")
        errors:   list[str] = []

        # ── 1. Crawl (reuse existing unless force_crawl is requested) ─────────
        pages_json   = data_dir / "json" / "pages.json"
        force_crawl  = bool(self._ctx.extras.get("force_crawl"))

        if not pages_json.is_file() or force_crawl:
            log.info("Crawling {url} for migration…", url=data.target_url)
            try:
                self._get_crawler().crawl(data.target_url, output_dir=data_dir)
                log.info("Crawl complete")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"crawl: {exc}")
                log.error("Crawl failed (will attempt with empty content): {e}", e=exc)
        else:
            log.info("pages.json present — reusing crawl data (set force_crawl to re-crawl)")

        # ── 2. Layout interpret → theme-map → optimized_*.json (no AI) ───────
        log.info(
            "Running layout migration pipeline (DOM → sections → {theme})…",
            theme=data.theme_id or "kadence",
        )
        try:
            migrated_slugs = write_layout_migrated_pages(
                data_dir,
                theme_id=data.theme_id or "",
                source_url=data.target_url,
            )
            log.info("Layout-migrated content written for {n} page(s)", n=len(migrated_slugs))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"layout pipeline: {exc}")
            migrated_slugs = []
            log.error("Layout migration pipeline failed: {e}", e=exc)

        # ── 3. Count crawled assets ───────────────────────────────────────────
        images_dir   = data_dir / "images"
        assets_copied = len(list(images_dir.glob("*.*"))) if images_dir.is_dir() else 0

        # ── 4. Install theme ──────────────────────────────────────────────────
        generator = self._get_generator()

        if data.theme_id:
            log.info("Installing theme stack: {t}", t=data.theme_id)
            try:
                generator.install_theme_stack(data.theme_id)
                log.info("Theme installed: {t}", t=data.theme_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"theme install: {exc}")
                log.error("Theme install error (continuing): {e}", e=exc)

            # ── 4b. Import starter template (best-effort) ─────────────────────
            if data.template_id:
                log.info(
                    "Importing starter template: {p} ({t})",
                    p=data.template_id, t=data.theme_id,
                )
                try:
                    generator.import_starter_template(data.template_id, data.theme_id)
                    log.info("Starter template imported: {p}", p=data.template_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Starter template import non-fatal (continuing): {e}", e=exc
                    )
                    errors.append(f"template import (non-fatal): {exc}")

        # ── 5. Generate WordPress pages from pass-through content ─────────────
        log.info("Generating WordPress pages from pass-through content…")
        success = False
        try:
            if data.theme_id and data.template_id:
                # Template was imported — hydrate its placeholders with client content.
                result = generator.hydrate_template_content(
                    data_dir,
                    page_slugs=migrated_slugs or None,
                )
            else:
                # No template (or template not selected) — full generate.
                result = generator.generate_from_directory(
                    data_dir,
                    reset=True,        # wipe any old demo pages first
                    update_only=False,
                )

            if getattr(result, "errors", None):
                errors.extend(str(e) for e in result.errors)

            success = bool(getattr(result, "success", False))
            wp_url  = getattr(result, "wp_url", "") or wp_url
            log.info(
                "WordPress generation done — success={s}  pages={n}",
                s=success,
                n=len(getattr(result, "pages_created", [])),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"wordpress generation: {exc}")
            log.error("WordPress generation error: {e}", e=exc)

        # ── 6. Open browser preview ───────────────────────────────────────────
        open_browser = bool(self._ctx.extras.get("open_browser", True))
        if success and open_browser:
            try:
                from webmaker.agents.live_demo_renderer.live_preview import refresh_preview
                refresh_preview(wp_url, open_browser=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not open browser preview: {e}", e=exc)

        return MigrationResult(
            target_url=data.target_url,
            theme_id=data.theme_id,
            template_id=data.template_id,
            wp_url=wp_url,
            pages_migrated=migrated_slugs,
            assets_copied=assets_copied,
            success=success,
            errors=errors,
        )
