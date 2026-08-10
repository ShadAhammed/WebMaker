"""
webmaker.modules.wordpress_generator
======================================
Builds a complete local WordPress demo site from structured JSON outputs
produced by previous WebMaker modules.

Pipeline
--------
1. Verify local WordPress + WP-CLI + PHP are available
2. Load business_profile.json, optimized_*.json, meta_data.json, images.json
3. Configure site settings (title, tagline, timezone, language, permalinks)
4. Import downloaded images into the Media Library
5. Create / update pages from optimised content (Homepage, About, Services,
   Contact, FAQ, plus per-service pages when present)
6. Apply SEO meta titles / descriptions from meta_data.json
7. Build primary navigation menu and assign it
8. Set the static front page
9. Write generation_report.json

Constraints
-----------
- Never crawl websites
- Never call AI models
- Never download / install themes or plugins
- Never rewrite content — use JSON as-is
- Continue on non-critical errors whenever possible

Primary class: WordPressGenerator
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from webmaker.core.exceptions import GenerationError, WordPressError
from webmaker.core.logging import get_logger
from webmaker.core.schema import unwrap_json, write_versioned_json
from webmaker.core.types import AnalysisResult, GenerationResult

if TYPE_CHECKING:
    from webmaker.config.settings import Settings
    from webmaker.modules.content_optimizer import PageContent

log = get_logger("wordpress_generator")


# ── Constants ─────────────────────────────────────────────────────────────────

_STANDARD_PAGES: tuple[str, ...] = (
    "homepage", "about", "services", "contact", "faq",
)

# Display titles for standard page slugs (short nav labels — NOT SEO headlines)
_PAGE_TITLES: dict[str, str] = {
    "homepage": "Startseite",
    "about":    "Über uns",
    "services": "Leistungen",
    "contact":  "Kontakt",
    "faq":      "FAQ",
}

# WP slug used when creating pages (homepage → front page, no slug needed)
_WP_SLUGS: dict[str, str] = {
    "homepage": "home",
    "about":    "about",
    "services": "services",
    "contact":  "contact",
    "faq":      "faq",
}

# Allowed published demo pages (anything else is pruned on full generate)
_ALLOWED_WP_SLUGS: frozenset[str] = frozenset(_WP_SLUGS.values())

# Image extensions accepted by WordPress media import
_IMAGE_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
})

# Max images to import (avoid flooding Media Library on large crawls)
_MAX_MEDIA_IMPORT = 40

# Primary menu name and location
_MENU_NAME = "Primary"
_MENU_LOCATION = "primary"

# SEO post-meta keys (generic + Yoast-compatible, no plugin required)
_SEO_TITLE_KEYS = (
    "_webmaker_seo_title",
    "_yoast_wpseo_title",
)
_SEO_DESC_KEYS = (
    "_webmaker_seo_description",
    "_yoast_wpseo_metadesc",
)


# ── Module-local report model ─────────────────────────────────────────────────

class GenerationReport(BaseModel):
    """Structured report written to generation_report.json."""

    project_dir:       str       = ""
    wp_url:            str       = ""
    wp_path:           str       = ""
    site_title:        str       = ""
    pages_created:     list[dict[str, Any]] = Field(default_factory=list)
    images_imported:   list[dict[str, Any]] = Field(default_factory=list)
    menu_created:      bool      = False
    menu_items:        list[str] = Field(default_factory=list)
    seo_applied:       list[dict[str, Any]] = Field(default_factory=list)
    settings_updated:  list[str] = Field(default_factory=list)
    theme_active:      str       = ""
    homepage_id:       int | None = None
    warnings:          list[str] = Field(default_factory=list)
    errors:            list[str] = Field(default_factory=list)
    success:           bool      = False
    generated_at:      str       = ""


# ── Main class ────────────────────────────────────────────────────────────────

class WordPressGenerator:
    """Creates a functional WordPress demo site from structured JSON outputs.

    Responsibilities:
    - Verify the local WordPress installation prepared in Phase 1.
    - Invoke WP-CLI (via the portable PHP binary) for all WP operations.
    - Create pages from ``optimized_*.json`` content.
    - Import previously downloaded images into the Media Library.
    - Build the primary navigation menu.
    - Apply site settings and SEO metadata from prior modules.
    - Write a generation report for QA and auditing.

    Args:
        settings:   Application settings instance.
        wp_path:    Override for WordPress installation directory.
        wpcli_path: Override for wp-cli.phar path.
    """

    def __init__(
        self,
        settings:   "Settings",
        wp_path:    Path | None = None,
        wpcli_path: Path | None = None,
    ) -> None:
        self._settings   = settings
        self._wp_path    = Path(wp_path    or settings.wordpress_dir)
        self._wpcli_path = Path(wpcli_path or settings.wpcli_path)
        self._php_exe    = settings.php_exe
        self._php_ini    = settings.php_ini
        self._timeout_s  = 120

        log.debug(
            "WordPressGenerator initialised (wp={wp})", wp=self._wp_path
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def generate_from_directory(
        self,
        project_dir: Path,
        *,
        reset: bool = False,
        update_only: bool = False,
    ) -> GenerationResult:
        """Build the demo site from a project directory's JSON outputs.

        This is the primary entry point for Phase 7.

        Args:
            project_dir: Client crawler project directory containing ``json/``.
            reset:       If True, wipe existing pages/menus before generating.
            update_only: If True, only create/update page content in place —
                         skip theme switch, media re-import, and menu rebuild
                         so an existing demo stays intact aside from page edits.

        Returns:
            GenerationResult summarising success / failure.

        Raises:
            GenerationError: If WordPress is unavailable or a critical step fails.
        """
        project_dir = Path(project_dir)
        report = GenerationReport(
            project_dir  = str(project_dir),
            wp_url       = self._settings.wordpress_url,
            wp_path      = str(self._wp_path),
            generated_at = datetime.now(timezone.utc).isoformat(),
        )

        log.info(
            "=== WordPress generation started: {d} (reset={r}, update_only={u}) ===",
            d=project_dir.name, r=reset, u=update_only,
        )

        # ── 1. Verify installation ────────────────────────────────────────────
        try:
            self.verify_installation()
            log.info("WordPress connection verified")
        except WordPressError as exc:
            report.errors.append(str(exc))
            self._save_report(project_dir, report)
            raise GenerationError(
                f"WordPress verification failed: {exc}",
                wp_path=str(self._wp_path),
            ) from exc

        # ── 2. Optional reset (full regenerate only) ──────────────────────────
        if reset and not update_only:
            try:
                self.reset_wordpress()
                log.info("WordPress reset complete")
            except WordPressError as exc:
                report.warnings.append(f"Reset warning: {exc}")
                log.warning("Reset failed (continuing): {e}", e=exc)

        # ── 3. Load inputs ────────────────────────────────────────────────────
        biz      = self._load_business_profile(project_dir)
        pages    = self._load_optimized_pages(project_dir)
        meta     = self._load_meta_data(project_dir)
        images   = self._load_image_metadata(project_dir)

        if not pages:
            msg = (
                "No optimized_*.json files found. "
                "Run ContentOptimizer before WordPressGenerator."
            )
            report.errors.append(msg)
            self._save_report(project_dir, report)
            raise GenerationError(msg, project_dir=str(project_dir))

        report.site_title = (
            biz.get("company_name")
            or self._settings.wp_admin_user
            or "WebMaker Demo"
        )

        # ── 4. Site settings ──────────────────────────────────────────────────
        if not update_only:
            try:
                updated = self._configure_site_settings(biz)
                report.settings_updated = updated
                log.info("Site settings updated: {s}", s=updated)
            except WordPressError as exc:
                report.warnings.append(f"Settings: {exc}")
                log.warning("Settings update failed: {e}", e=exc)

            # ── 5. Theme (activate existing only) ─────────────────────────────
            try:
                theme = self._activate_default_theme()
                report.theme_active = theme
                log.info("Active theme: {t}", t=theme)
            except WordPressError as exc:
                report.warnings.append(f"Theme: {exc}")
                log.warning("Theme activation skipped: {e}", e=exc)

            # ── 6. Import media ───────────────────────────────────────────────
            media_ids = self._import_images(images, report)
        else:
            media_ids = []
            log.info("Update-only mode — skipping settings/theme/media/menu rebuild")

        # ── 7. Create / update pages ──────────────────────────────────────────
        page_ids: dict[str, int] = {}
        for slug, content in pages.items():
            try:
                page_id = self._create_page_from_json(slug, content, meta, media_ids)
                page_ids[slug] = page_id
                title = self._page_title(slug, content, biz)
                report.pages_created.append({
                    "slug":    _WP_SLUGS.get(slug, slug),
                    "title":   title,
                    "post_id": page_id,
                })
                log.info("Page created/updated: {s} (id={i})", s=slug, i=page_id)

                # SEO
                seo_title = (
                    meta.get(slug, {}).get("title")
                    or content.get("meta_title", "")
                )
                seo_desc = (
                    meta.get(slug, {}).get("description")
                    or content.get("meta_description", "")
                )
                if seo_title or seo_desc:
                    try:
                        self.set_seo_meta(page_id, seo_title, seo_desc)
                        report.seo_applied.append({
                            "post_id":     page_id,
                            "slug":        slug,
                            "title":       seo_title,
                            "description": seo_desc,
                        })
                    except WordPressError as exc:
                        report.warnings.append(f"SEO meta {slug}: {exc}")

            except WordPressError as exc:
                report.errors.append(f"Page {slug}: {exc}")
                log.error("Page creation failed for {s}: {e}", s=slug, e=exc)

        # Per-service sub-pages are intentionally NOT created.
        # They flooded the header when block themes list all pages, and the
        # original sites usually keep services on one Leistungen page.
        if media_ids and "homepage" in page_ids and not update_only:
            try:
                self._set_featured_image(page_ids["homepage"], media_ids[0])
            except WordPressError as exc:
                report.warnings.append(f"Featured image: {exc}")

        # Drop leftover service/orphan pages so the demo stays lean
        if not update_only:
            try:
                pruned = self._prune_orphan_pages(keep_ids=set(page_ids.values()))
                if pruned:
                    report.warnings.append(f"Pruned orphan pages: {pruned}")
                    log.info("Pruned {n} orphan page(s)", n=len(pruned))
            except WordPressError as exc:
                report.warnings.append(f"Orphan prune: {exc}")

        # ── 8. Homepage ───────────────────────────────────────────────────────
        if "homepage" in page_ids and not update_only:
            try:
                self.set_homepage(page_ids["homepage"])
                report.homepage_id = page_ids["homepage"]
                log.info("Homepage set to page id={i}", i=page_ids["homepage"])
            except WordPressError as exc:
                report.warnings.append(f"Homepage setting: {exc}")

        # ── 9. Menu (full build only — preserve existing nav on update) ───────
        if not update_only:
            try:
                menu_items = self._create_primary_menu(page_ids)
                report.menu_created = True
                report.menu_items   = menu_items
                log.info("Primary menu created with {n} items", n=len(menu_items))
            except WordPressError as exc:
                report.warnings.append(f"Menu: {exc}")
                log.warning("Menu creation failed: {e}", e=exc)

        # ── 9b. Branding + Agent 1 design system CSS ──────────────────────────
        try:
            self._apply_branding(biz, media_ids, report, project_dir=project_dir)
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(f"Branding: {exc}")

        # ── 10. Persist report ────────────────────────────────────────────────
        report.success = (
            len(report.pages_created) > 0 and len(report.errors) == 0
        ) or (
            len(report.pages_created) > 0
            and all("Page " not in e for e in report.errors)
        )
        # Soft success: at least one page created
        if report.pages_created:
            report.success = True

        self._save_report(project_dir, report)
        log.info(
            "WordPress generation complete — {n} pages, {m} images, "
            "success={s}",
            n=len(report.pages_created),
            m=len(report.images_imported),
            s=report.success,
        )

        return GenerationResult(
            wp_url             = self._settings.wordpress_url,
            wp_path            = self._wp_path,
            theme_slug         = report.theme_active,
            plugins_installed  = [],
            pages_created      = [p["slug"] for p in report.pages_created],
            admin_url          = f"{self._settings.wordpress_url}/wp-admin",
            success            = report.success,
            errors             = report.errors + report.warnings,
        )

    def generate(
        self,
        analysis:   AnalysisResult,
        pages:      dict[str, "PageContent"],
        project_id: str,
    ) -> GenerationResult:
        """Build the demo site from in-memory AnalysisResult + PageContent.

        Writes temporary optimised JSON into the project directory then
        delegates to :meth:`generate_from_directory`.

        Args:
            analysis:   Full analysis result (provides business name / industry).
            pages:      Page content from ContentOptimizer.
            project_id: Project identifier used as the subdirectory name.

        Returns:
            GenerationResult with URL, paths, and status.

        Raises:
            GenerationError: If no pages are provided or WordPress is unavailable.
        """
        if not pages:
            raise GenerationError(
                "No page content provided. "
                "Run ContentOptimizer before WordPressGenerator.",
            )

        project_dir = self._settings.projects_dir / project_id
        json_dir = project_dir / "json"
        json_dir.mkdir(parents=True, exist_ok=True)

        # Persist PageContent as optimized_*.json for the directory pipeline
        meta: dict[str, dict] = {}
        for slug, pc in pages.items():
            content = {
                "meta_title":       pc.meta_title,
                "meta_description": pc.meta_description,
                "body_html":        pc.body_html,
                "title":            pc.title,
                "headings":         pc.headings,
            }
            self._write_json(json_dir / f"optimized_{slug}.json", content)
            meta[slug] = {
                "title":       pc.meta_title,
                "description": pc.meta_description,
            }

        self._write_json(json_dir / "meta_data.json", meta)

        # Minimal business profile from analysis
        biz = analysis.business
        self._write_json(json_dir / "business_profile.json", {
            "company_name":  biz.name,
            "industry":      biz.industry,
            "main_services": biz.services,
            "service_areas": [biz.location] if biz.location else [],
            "brand_tone":    biz.tone_of_voice,
            "unique_value":  biz.unique_value,
            "contact_email": biz.contact_email,
            "contact_phone": biz.contact_phone,
        })

        return self.generate_from_directory(project_dir)

    def verify_installation(self) -> None:
        """Verify WordPress, PHP, and WP-CLI are available and WP is installed.

        Raises:
            WordPressError: If any prerequisite is missing.
        """
        if not self._wp_path.exists():
            raise WordPressError(
                f"WordPress directory not found: {self._wp_path}",
                path=str(self._wp_path),
            )
        if not (self._wp_path / "wp-config.php").exists():
            raise WordPressError(
                f"wp-config.php not found in {self._wp_path}",
                path=str(self._wp_path),
            )
        if not self._php_exe.exists():
            raise WordPressError(
                f"PHP executable not found: {self._php_exe}",
                path=str(self._php_exe),
            )
        if not self._wpcli_path.exists():
            raise WordPressError(
                f"WP-CLI not found: {self._wpcli_path}",
                path=str(self._wpcli_path),
            )

        # Confirm WordPress is fully installed
        try:
            self._wpcli("core", "is-installed")
        except WordPressError as exc:
            raise WordPressError(
                "WordPress is not installed. Run setup.ps1 first.",
                detail=str(exc),
            ) from exc

        log.info("WordPress installation verified at {p}", p=self._wp_path)

    def reset_wordpress(self) -> None:
        """Reset pages, menus, and media to a clean generation state.

        Does NOT drop the database or uninstall WordPress.  Deletes all
        pages (post_type=page), removes custom menus, and leaves posts /
        theme / plugins intact.

        Raises:
            WordPressError: If a critical WP-CLI call fails.
        """
        log.info("Resetting WordPress pages and menus…")

        # Delete all pages
        try:
            raw = self._wpcli(
                "post", "list",
                "--post_type=page",
                "--format=ids",
            )
            ids = raw.strip().split()
            for pid in ids:
                if pid.isdigit():
                    try:
                        self._wpcli("post", "delete", pid, "--force")
                    except WordPressError as exc:
                        log.warning("Could not delete page {i}: {e}", i=pid, e=exc)
        except WordPressError as exc:
            log.warning("Could not list pages for reset: {e}", e=exc)

        # Delete menus
        try:
            raw = self._wpcli("menu", "list", "--format=json")
            menus = json.loads(raw) if raw.strip() else []
            for menu in menus:
                term_id = str(menu.get("term_id", ""))
                if term_id:
                    try:
                        self._wpcli("menu", "delete", term_id)
                    except WordPressError as exc:
                        log.warning("Could not delete menu {i}: {e}", i=term_id, e=exc)
        except (WordPressError, json.JSONDecodeError) as exc:
            log.warning("Could not list menus for reset: {e}", e=exc)

        # Reset front page to latest posts
        try:
            self._wpcli("option", "update", "show_on_front", "posts")
        except WordPressError as exc:
            log.warning("Could not reset show_on_front: {e}", e=exc)

        log.info("WordPress reset complete")

    def create_page(
        self,
        slug:    str,
        content: "PageContent",
    ) -> int:
        """Create or update a WordPress page via WP-CLI.

        Args:
            slug:    WordPress page slug (or logical slug like ``homepage``).
            content: PageContent model from ContentOptimizer.

        Returns:
            WordPress post ID of the created/updated page.

        Raises:
            WordPressError: If WP-CLI reports an error.
        """
        wp_slug = _WP_SLUGS.get(slug, slug)
        title   = content.title or _PAGE_TITLES.get(slug, slug.replace("-", " ").title())
        body    = content.body_html or ""

        existing_id = self._find_page_id(wp_slug)
        if existing_id:
            self._wpcli(
                "post", "update", str(existing_id),
                f"--post_title={title}",
                f"--post_content={body}",
                "--post_status=publish",
            )
            log.info("Updated page {s} (id={i})", s=wp_slug, i=existing_id)
            return existing_id

        raw = self._wpcli(
            "post", "create",
            "--post_type=page",
            f"--post_title={title}",
            f"--post_name={wp_slug}",
            f"--post_content={body}",
            "--post_status=publish",
            "--porcelain",
        )
        page_id = self._parse_id(raw)
        log.info("Created page {s} (id={i})", s=wp_slug, i=page_id)
        return page_id

    def install_theme(self, theme_slug: str) -> None:
        """Activate an already-installed theme.  Does NOT download themes.

        Args:
            theme_slug: Theme directory slug (e.g. ``"twentytwentyfour"``).

        Raises:
            WordPressError: If the theme is not installed or activation fails.
        """
        # Check whether the theme is already present
        try:
            raw = self._wpcli("theme", "list", "--field=name", "--format=csv")
            installed = {t.strip() for t in raw.splitlines() if t.strip()}
        except WordPressError:
            installed = set()

        if theme_slug not in installed:
            raise WordPressError(
                f"Theme {theme_slug!r} is not installed. "
                "WordPressGenerator does not download themes. "
                "Use a theme already present in the local environment.",
                theme=theme_slug,
            )

        self._wpcli("theme", "activate", theme_slug)
        log.info("Theme activated: {t}", t=theme_slug)

    def install_plugin(self, plugin_slug: str) -> None:
        """Activate an already-installed plugin.  Does NOT download plugins.

        Args:
            plugin_slug: Plugin directory slug.

        Raises:
            WordPressError: If the plugin is not installed or activation fails.
        """
        try:
            raw = self._wpcli("plugin", "list", "--field=name", "--format=csv")
            installed = {p.strip() for p in raw.splitlines() if p.strip()}
        except WordPressError:
            installed = set()

        if plugin_slug not in installed:
            raise WordPressError(
                f"Plugin {plugin_slug!r} is not installed. "
                "WordPressGenerator does not download plugins.",
                plugin=plugin_slug,
            )

        self._wpcli("plugin", "activate", plugin_slug)
        log.info("Plugin activated: {p}", p=plugin_slug)

    def upload_media(self, local_path: Path, title: str = "") -> int:
        """Upload a local media file to the WordPress media library.

        Args:
            local_path: Absolute path to the image or file.
            title:      Optional attachment title.

        Returns:
            WordPress attachment post ID.

        Raises:
            WordPressError: If the file is missing or the upload fails.
        """
        local_path = Path(local_path)
        if not local_path.exists():
            raise WordPressError(
                f"Media file not found: {local_path}",
                path=str(local_path),
            )

        args = ["media", "import", str(local_path), "--porcelain"]
        if title:
            args.append(f"--title={title}")

        raw = self._wpcli(*args)
        media_id = self._parse_id(raw)
        log.info(
            "Media imported: {f} (id={i})",
            f=local_path.name, i=media_id,
        )
        return media_id

    def set_seo_meta(
        self,
        post_id:          int,
        meta_title:       str,
        meta_description: str,
    ) -> None:
        """Write SEO meta fields for a page via WP-CLI post meta.

        Stores values under both WebMaker and Yoast-compatible keys so
        SEO plugins (if already present) pick them up automatically.
        Does not install any SEO plugin.

        Args:
            post_id:          WordPress post ID.
            meta_title:       SEO title string.
            meta_description: SEO description string.

        Raises:
            WordPressError: If a critical meta update fails.
        """
        errors: list[str] = []

        if meta_title:
            for key in _SEO_TITLE_KEYS:
                try:
                    self._wpcli(
                        "post", "meta", "update",
                        str(post_id), key, meta_title,
                    )
                except WordPressError as exc:
                    errors.append(f"{key}: {exc}")

            # Also set the WordPress excerpt as a fallback description surface
            try:
                self._wpcli(
                    "post", "update", str(post_id),
                    f"--post_excerpt={meta_description[:200] if meta_description else meta_title}",
                )
            except WordPressError as exc:
                errors.append(f"excerpt: {exc}")

        if meta_description:
            for key in _SEO_DESC_KEYS:
                try:
                    self._wpcli(
                        "post", "meta", "update",
                        str(post_id), key, meta_description,
                    )
                except WordPressError as exc:
                    errors.append(f"{key}: {exc}")

        if errors and len(errors) >= len(_SEO_TITLE_KEYS) + len(_SEO_DESC_KEYS):
            raise WordPressError(
                f"All SEO meta updates failed for post {post_id}",
                detail="; ".join(errors),
            )

        log.info("SEO meta applied to post {i}", i=post_id)

    def set_homepage(self, page_id: int) -> None:
        """Configure WordPress to use a static page as the front page.

        Args:
            page_id: Post ID of the page to set as home.

        Raises:
            WordPressError: If the option update fails.
        """
        self._wpcli("option", "update", "show_on_front", "page")
        self._wpcli("option", "update", "page_on_front", str(page_id))
        log.info("Static homepage set to page id={i}", i=page_id)

    # ── Theme-stack / starter-template API ────────────────────────────────────

    def install_theme_stack(self, theme_id: str) -> None:
        """Download, install, and activate a theme and its required plugins.

        Unlike :meth:`install_theme` (which only activates already-installed
        themes), this method downloads from the WordPress.org repository using
        ``wp theme install`` / ``wp plugin install``.  It is intentionally
        restricted to the curated entries in :mod:`webmaker.data.theme_catalog`.

        Args:
            theme_id: One of the ``id`` values from ``THEMES`` in
                      ``webmaker.data.theme_catalog``.

        Raises:
            WordPressError:  If installation or activation fails.
            ValueError:      If ``theme_id`` is not in the catalog.
        """
        from webmaker.data.theme_catalog import THEME_BY_ID

        entry = THEME_BY_ID.get(theme_id)
        if not entry:
            raise ValueError(
                f"Unknown theme id {theme_id!r}. "
                f"Valid ids: {list(THEME_BY_ID)}"
            )

        wp_slug = entry["wp_slug"]
        log.info("Installing theme stack for {t} (slug={s})", t=theme_id, s=wp_slug)

        # Install + activate theme (allow download)
        try:
            self._wpcli("theme", "install", wp_slug, "--activate", "--force")
            log.info("Theme installed and activated: {s}", s=wp_slug)
        except WordPressError as exc:
            # If already active, ignore; otherwise re-raise
            if "already installed" not in str(exc).lower():
                raise

        # Install + activate required plugins (allow download)
        for plugin_slug in entry.get("plugins", []):
            try:
                self._wpcli("plugin", "install", plugin_slug, "--activate", "--force")
                log.info("Plugin installed and activated: {p}", p=plugin_slug)
            except WordPressError as exc:
                log.warning(
                    "Could not install plugin {p}: {e}", p=plugin_slug, e=exc
                )

    def import_starter_template(
        self,
        template_id: str,
        theme_id: str,
        *,
        timeout_s: int = 300,
    ) -> None:
        """Import a Kadence (or Astra) starter template into WordPress.

        Strategy:
        1. Try ``wp kadence-starter-templates import --id=<template_id>``
           (the plugin ships a WP-CLI command as of v2.x).
        2. Fall back to a minimal ``wp eval`` PHP snippet that calls the
           plugin's REST-like import logic directly.
        3. For non-Kadence themes, log a notice — those import via a
           dashboard wizard that requires a browser; a direct WP-CLI path
           is not universally available.

        After import the demo pages may contain placeholder content from the
        template.  Call :meth:`hydrate_template_content` afterwards to replace
        that placeholder content with the client's AI-generated material.

        Args:
            template_id: Template ``id`` from the catalog entry.
            theme_id:    Parent theme ``id`` (used to pick the import strategy).
            timeout_s:   WP-CLI timeout override (template downloads can take
                         a while on slow connections).

        Raises:
            WordPressError: If every import strategy fails.
        """
        from webmaker.data.theme_catalog import THEME_BY_ID, get_template

        entry = THEME_BY_ID.get(theme_id)
        if not entry:
            raise ValueError(f"Unknown theme id {theme_id!r}")

        tmpl = get_template(theme_id, template_id)
        if not tmpl:
            raise ValueError(
                f"Template {template_id!r} not found under theme {theme_id!r}"
            )

        log.info(
            "Importing template '{n}' for theme '{t}'",
            n=tmpl["name"], t=theme_id,
        )

        old_timeout = self._timeout_s
        self._timeout_s = timeout_s

        try:
            if theme_id == "kadence":
                self._import_kadence_template(template_id)
            elif theme_id == "astra":
                self._import_astra_template(template_id)
            else:
                log.warning(
                    "No automated import path for theme '{t}'. "
                    "Open the WordPress dashboard and import the template manually, "
                    "then call hydrate_template_content() to push client content.",
                    t=theme_id,
                )
        finally:
            self._timeout_s = old_timeout

    def hydrate_template_content(
        self,
        project_dir: Path,
        *,
        page_slugs: list[str] | None = None,
    ) -> GenerationResult:
        """Replace a starter template's placeholder content with client data.

        After importing a starter template (which fills the demo with generic
        stock content), this method walks the client's ``optimized_*.json``
        files and overwrites matching WordPress pages with the real content.
        It also uploads client images, applies branding (logo, Kadence palette),
        and updates SEO meta.

        This is intentionally content-only: no theme/plugin changes, no menu
        rebuild, no page pruning — the template structure is preserved.

        Args:
            project_dir:  Client project directory (contains ``json/``).
            page_slugs:   Optional subset of page slugs to hydrate.
                          ``None`` means all available pages.

        Returns:
            GenerationResult summarising what was applied.

        Raises:
            GenerationError: If WordPress is unavailable.
        """
        project_dir = Path(project_dir)

        report = GenerationReport(
            project_dir  = str(project_dir),
            wp_url       = self._settings.wordpress_url,
            wp_path      = str(self._wp_path),
            generated_at = datetime.now(timezone.utc).isoformat(),
        )

        log.info(
            "=== Template hydration started: {d} ===", d=project_dir.name
        )

        try:
            self.verify_installation()
        except WordPressError as exc:
            report.errors.append(str(exc))
            self._save_report(project_dir, report)
            raise GenerationError(
                f"WordPress verification failed: {exc}",
                wp_path=str(self._wp_path),
            ) from exc

        biz    = self._load_business_profile(project_dir)
        pages  = self._load_optimized_pages(project_dir)
        meta   = self._load_meta_data(project_dir)
        images = self._load_image_metadata(project_dir)

        if not pages:
            msg = (
                "No optimized_*.json files found. "
                "Run ContentOptimizer before hydrating a template."
            )
            report.errors.append(msg)
            self._save_report(project_dir, report)
            raise GenerationError(msg)

        # Filter to requested slugs
        if page_slugs:
            pages = {s: c for s, c in pages.items() if s in page_slugs}

        # Upload client images
        media_ids = self._import_images(images, report)

        # Overwrite page content (create or update)
        page_ids: dict[str, int] = {}
        for slug, content in pages.items():
            try:
                page_id = self._create_page_from_json(slug, content, meta, media_ids)
                page_ids[slug] = page_id
                title = self._page_title(slug, content, biz)
                report.pages_created.append({
                    "slug":    _WP_SLUGS.get(slug, slug),
                    "title":   title,
                    "post_id": page_id,
                })
                log.info(
                    "Hydrated page {s} (id={i})", s=slug, i=page_id
                )

                seo_title = (
                    meta.get(slug, {}).get("title")
                    or content.get("meta_title", "")
                )
                seo_desc = (
                    meta.get(slug, {}).get("description")
                    or content.get("meta_description", "")
                )
                if seo_title or seo_desc:
                    try:
                        self.set_seo_meta(page_id, seo_title, seo_desc)
                    except WordPressError as exc:
                        report.warnings.append(f"SEO meta {slug}: {exc}")

            except WordPressError as exc:
                report.errors.append(f"Page {slug}: {exc}")
                log.error(
                    "Hydration failed for {s}: {e}", s=slug, e=exc
                )

        # Apply client logo if available
        self._apply_branding(biz, media_ids, report, project_dir=project_dir)

        report.success = bool(report.pages_created)
        self._save_report(project_dir, report)

        log.info(
            "Template hydration complete — {n} pages updated",
            n=len(report.pages_created),
        )

        return GenerationResult(
            wp_url             = self._settings.wordpress_url,
            wp_path            = self._wp_path,
            theme_slug         = "",
            plugins_installed  = [],
            pages_created      = [p["slug"] for p in report.pages_created],
            admin_url          = f"{self._settings.wordpress_url}/wp-admin",
            success            = report.success,
            errors             = report.errors + report.warnings,
        )

    # ── Internal: theme import helpers ────────────────────────────────────────

    def _import_kadence_template(self, template_id: str) -> None:
        """Attempt to import a Kadence starter template via WP-CLI."""
        # Strategy 1: use the plugin's own WP-CLI command (v2.1+ ships it)
        try:
            self._wpcli(
                "kadence-starter-templates", "import",
                f"--template={template_id}",
                "--yes",
            )
            log.info("Kadence template imported via WP-CLI command: {t}", t=template_id)
            return
        except WordPressError as exc:
            log.debug("WP-CLI kadence command failed ({e}), trying eval fallback", e=exc)

        # Strategy 2: PHP eval — call the plugin REST endpoint internally
        php_snippet = (
            "if (class_exists('Kadence_Starter_Templates\\\\Importer')) {"
            f"$importer = new Kadence_Starter_Templates\\\\Importer();"
            f"$importer->import_template('{template_id}');"
            "} else { echo 'Plugin not loaded'; }"
        )
        try:
            out = self._wpcli("eval", php_snippet)
            log.info(
                "Kadence template import eval result: {o}",
                o=(out or "").strip()[:200],
            )
        except WordPressError as exc:
            log.warning(
                "Kadence template eval import failed: {e}. "
                "The template preview URL was opened but content was not "
                "automatically imported. Run hydrate_template_content() "
                "to push client content onto the existing demo.",
                e=exc,
            )

    def _import_astra_template(self, template_id: str) -> None:
        """Attempt to import an Astra starter site via WP-CLI."""
        try:
            self._wpcli(
                "astra", "import",
                f"--template={template_id}",
                "--yes",
            )
            log.info("Astra template imported: {t}", t=template_id)
        except WordPressError as exc:
            log.warning(
                "Astra WP-CLI import failed ({e}). "
                "Open the WordPress dashboard → Astra Sites to import manually.",
                e=exc,
            )

    def _apply_branding(
        self,
        biz:       dict,
        media_ids: list[int],
        report:    GenerationReport,
        project_dir: Path | None = None,
    ) -> None:
        """Apply client logo, site title, and Agent 1 design-system CSS."""
        # Prefer design_tokens short_name when available
        tokens_path = (
            Path(project_dir) / "json" / "design_tokens.json"
            if project_dir else None
        )
        tokens: dict = {}
        if tokens_path and tokens_path.is_file():
            try:
                raw = json.loads(tokens_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    tokens = raw
            except Exception:
                tokens = {}

        # Site title + tagline
        company = (
            tokens.get("short_name")
            or tokens.get("company_name")
            or biz.get("company_name")
            or ""
        )
        if company:
            try:
                self._wpcli("option", "update", "blogname", company)
                tagline = (
                    biz.get("tagline")
                    or biz.get("unique_value", "")[:80]
                    or "Schnell · Zuverlässig · Festpreis"
                )
                if tagline:
                    self._wpcli("option", "update", "blogdescription", tagline)
                log.info("Site title set to: {t}", t=company)
            except WordPressError as exc:
                report.warnings.append(f"Site title: {exc}")

        # Logo — use the first uploaded media item as the custom logo
        if media_ids:
            try:
                self._wpcli(
                    "option", "update",
                    "site_icon", str(media_ids[0]),
                )
                # Set custom_logo theme-mod (works for most classic+block themes)
                self._wpcli(
                    "theme", "mod", "set",
                    "custom_logo", str(media_ids[0]),
                )
                log.info(
                    "Custom logo set to attachment id={i}", i=media_ids[0]
                )
            except WordPressError as exc:
                report.warnings.append(f"Logo: {exc}")

        # Additional CSS from Agent 1 design system (fonts + accent)
        if tokens:
            try:
                from webmaker.agents.website_modernizer.design_system import (
                    DesignTokens,
                    wp_additional_css,
                )
                dt = DesignTokens(
                    company_name=str(tokens.get("company_name") or ""),
                    short_name=str(tokens.get("short_name") or ""),
                    phone=str(tokens.get("phone") or ""),
                    email=str(tokens.get("email") or ""),
                    accent=str(tokens.get("accent") or "#e85d04"),
                    font_display=str(tokens.get("font_display") or "Sora"),
                    font_body=str(tokens.get("font_body") or "Source Sans 3"),
                    font_url=str(tokens.get("font_url") or ""),
                )
                if not dt.font_url:
                    dt.font_url = (
                        "https://fonts.googleapis.com/css2?"
                        "family=Sora:wght@500;600;700;800&"
                        "family=Source+Sans+3:ital,wght@0,400;0,600;0,700;1,400&"
                        "display=swap"
                    )
                css = wp_additional_css(dt)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".css",
                    delete=False,
                    encoding="utf-8",
                ) as tmp:
                    tmp.write(css)
                    css_path = tmp.name
                try:
                    php = (
                        "$p=" + json.dumps(css_path) + ";"
                        "$css=file_get_contents($p);"
                        "if(function_exists('wp_update_custom_css_post')){"
                        "wp_update_custom_css_post($css);echo 'ok';}"
                    )
                    self._wpcli("eval", php)
                    log.info("Applied Agent 1 design-system Additional CSS")
                finally:
                    try:
                        Path(css_path).unlink(missing_ok=True)
                    except OSError:
                        pass
            except Exception as exc:  # noqa: BLE001
                report.warnings.append(f"Design CSS: {exc}")
                log.warning("Design CSS apply failed: {e}", e=exc)

    # ── Internal: WP-CLI / PHP runners ─────────────────────────────────────────

    def _wpcli(self, *args: str) -> str:
        """Execute a WP-CLI command and return stdout.

        Invokes::

            php.exe -c php.ini wp-cli.phar --path=<wp> --allow-root <args…>

        Args:
            *args: WP-CLI arguments (e.g. ``"post"``, ``"list"``).

        Returns:
            Decoded stdout from the WP-CLI process.

        Raises:
            WordPressError: If the process exits with a non-zero code.
        """
        cmd = [
            str(self._php_exe),
            "-c", str(self._php_ini),
            str(self._wpcli_path),
            f"--path={self._wp_path}",
            "--allow-root",
            *args,
        ]

        env = os.environ.copy()
        # WP-CLI needs HOME on Windows
        env.setdefault("HOME", str(self._settings.project_root))

        log.debug("WP-CLI: {a}", a=" ".join(args))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                env=env,
                cwd=str(self._wp_path),
            )
        except subprocess.TimeoutExpired as exc:
            raise WordPressError(
                f"WP-CLI timed out after {self._timeout_s}s",
                args=list(args),
            ) from exc
        except OSError as exc:
            raise WordPressError(
                f"Failed to execute WP-CLI: {exc}",
                args=list(args),
            ) from exc

        if result.returncode != 0:
            stderr = (result.stderr or result.stdout or "").strip()
            raise WordPressError(
                f"WP-CLI failed ({result.returncode}): {stderr[:400]}",
                args=list(args),
                returncode=result.returncode,
            )

        return result.stdout or ""

    def _php(self, *args: str) -> str:
        """Execute a PHP CLI command using the configured PHP binary.

        Args:
            *args: PHP arguments.

        Returns:
            Decoded stdout.

        Raises:
            WordPressError: If the process exits with a non-zero code.
        """
        cmd = [str(self._php_exe), "-c", str(self._php_ini), *args]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise WordPressError(f"PHP execution failed: {exc}") from exc

        if result.returncode != 0:
            raise WordPressError(
                f"PHP failed ({result.returncode}): "
                f"{(result.stderr or '')[:300]}",
                returncode=result.returncode,
            )
        return result.stdout or ""

    # ── Internal: page / content builders ──────────────────────────────────────

    def _create_page_from_json(
        self,
        slug:      str,
        content:   dict,
        meta:      dict[str, dict],
        media_ids: list[int],
    ) -> int:
        """Create or update a page from an optimised JSON content dict.

        Args:
            slug:      Logical page slug (homepage, about, …).
            content:   Parsed optimized_<slug>.json.
            meta:      meta_data.json mapping.
            media_ids: Imported attachment IDs (for optional image insertion).

        Returns:
            WordPress post ID.
        """
        wp_slug = _WP_SLUGS.get(slug, self._slugify(slug))
        title   = self._extract_page_title(slug, content)
        html    = self._render_html(slug, content, media_ids)

        existing_id = self._find_page_id(wp_slug)
        if existing_id:
            # Write content via temp file to avoid shell escaping issues
            self._update_page(existing_id, title, html)
            return existing_id

        return self._create_page_raw(wp_slug, title, html)

    def _create_service_page(self, svc: dict, svc_slug: str) -> int:
        """Create a dedicated page for one service entry.

        Args:
            svc:      Service dict from optimized_services.json.
            svc_slug: URL-safe slug.

        Returns:
            WordPress post ID.
        """
        title = svc.get("name") or svc.get("heading") or svc_slug
        html  = self._render_service_html(svc)
        existing = self._find_page_id(svc_slug)
        if existing:
            self._update_page(existing, title, html)
            return existing
        return self._create_page_raw(svc_slug, title, html)

    def _create_page_raw(self, wp_slug: str, title: str, html: str) -> int:
        """Create a new page, writing content via a temporary file.

        Args:
            wp_slug: WordPress post_name.
            title:   Post title.
            html:    HTML body.

        Returns:
            New post ID.
        """
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".html",
            encoding="utf-8",
            delete=False,
        ) as tmp:
            tmp.write(html)
            tmp_path = tmp.name

        try:
            raw = self._wpcli(
                "post", "create",
                tmp_path,
                "--post_type=page",
                f"--post_title={title}",
                f"--post_name={wp_slug}",
                "--post_status=publish",
                "--porcelain",
            )
            return self._parse_id(raw)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _update_page(self, page_id: int, title: str, html: str) -> None:
        """Update an existing page's title and content via a temp file."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".html",
            encoding="utf-8",
            delete=False,
        ) as tmp:
            tmp.write(html)
            tmp_path = tmp.name

        try:
            self._wpcli(
                "post", "update", str(page_id),
                tmp_path,
                f"--post_title={title}",
                "--post_status=publish",
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # ── Internal: HTML rendering ───────────────────────────────────────────────

    def _render_html(
        self,
        slug:      str,
        content:   dict,
        media_ids: list[int],
    ) -> str:
        """Convert optimised JSON into HTML suitable for a WP page body.

        If ``body_html`` is already present (from PageContent), use it
        directly.  Otherwise render from structured fields.

        Args:
            slug:      Page slug.
            content:   Optimised content dict.
            media_ids: Available media IDs for optional image tags.

        Returns:
            HTML string.
        """
        if content.get("body_html"):
            return str(content["body_html"])

        renderers = {
            "homepage": self._render_homepage,
            "about":    self._render_about,
            "services": self._render_services,
            "contact":  self._render_contact,
            "faq":      self._render_faq,
        }
        renderer = renderers.get(slug, self._render_generic)
        return renderer(content, media_ids)

    def _render_homepage(self, content: dict, media_ids: list[int]) -> str:
        parts: list[str] = []

        hero = content.get("hero", {})
        if isinstance(hero, dict):
            if hero.get("heading"):
                parts.append(f"<h1>{escape(str(hero['heading']))}</h1>")
            if hero.get("subheading"):
                parts.append(f"<p class=\"hero-sub\">{escape(str(hero['subheading']))}</p>")
            ctas = []
            if hero.get("cta_primary"):
                ctas.append(
                    f'<a class="btn btn-primary" href="/contact/">'
                    f'{escape(str(hero["cta_primary"]))}</a>'
                )
            if hero.get("cta_secondary"):
                ctas.append(
                    f'<a class="btn btn-secondary" href="/services/">'
                    f'{escape(str(hero["cta_secondary"]))}</a>'
                )
            if ctas:
                parts.append('<p class="cta-group">' + " ".join(ctas) + "</p>")

        if media_ids:
            parts.append(
                f'<!-- wp:image {{"id":{media_ids[0]}}} -->'
                f'<figure class="wp-block-image">'
                f'[caption id="attachment_{media_ids[0]}"]'
                f'[/caption]</figure><!-- /wp:image -->'
            )

        if content.get("intro"):
            parts.append(f"<p>{escape(str(content['intro']))}</p>")

        svc_ov = content.get("services_overview", {})
        if isinstance(svc_ov, dict):
            if svc_ov.get("heading"):
                parts.append(f"<h2>{escape(str(svc_ov['heading']))}</h2>")
            for svc in svc_ov.get("services", [])[:12]:
                if isinstance(svc, dict):
                    parts.append(f"<h3>{escape(str(svc.get('name', '')))}</h3>")
                    parts.append(
                        f"<p>{escape(str(svc.get('short_description', '')))}</p>"
                    )

        wcu = content.get("why_choose_us", {})
        if isinstance(wcu, dict):
            if wcu.get("heading"):
                parts.append(f"<h2>{escape(str(wcu['heading']))}</h2>")
            for pt in wcu.get("points", []):
                if isinstance(pt, dict):
                    parts.append(f"<h3>{escape(str(pt.get('heading', '')))}</h3>")
                    parts.append(f"<p>{escape(str(pt.get('text', '')))}</p>")

        cta = content.get("cta_section", {})
        if isinstance(cta, dict) and cta.get("heading"):
            parts.append(f"<h2>{escape(str(cta['heading']))}</h2>")
            if cta.get("text"):
                parts.append(f"<p>{escape(str(cta['text']))}</p>")
            if cta.get("cta_button"):
                parts.append(
                    f'<p><a class="btn btn-primary" href="/contact/">'
                    f'{escape(str(cta["cta_button"]))}</a></p>'
                )

        return "\n".join(p for p in parts if p.strip())

    def _render_about(self, content: dict, media_ids: list[int]) -> str:
        parts: list[str] = []
        if content.get("hero_heading"):
            parts.append(f"<h1>{escape(str(content['hero_heading']))}</h1>")
        if content.get("company_story"):
            parts.append(f"<p>{escape(str(content['company_story']))}</p>")
        if content.get("mission_statement"):
            parts.append(
                f"<h2>Our Mission</h2>"
                f"<p>{escape(str(content['mission_statement']))}</p>"
            )
        values = content.get("values", [])
        if values:
            parts.append("<h2>Our Values</h2>")
            for v in values:
                if isinstance(v, dict):
                    parts.append(f"<h3>{escape(str(v.get('name', '')))}</h3>")
                    parts.append(f"<p>{escape(str(v.get('description', '')))}</p>")
        if content.get("team_intro"):
            parts.append(f"<h2>Our Team</h2><p>{escape(str(content['team_intro']))}</p>")
        cta = content.get("cta_section", {})
        if isinstance(cta, dict) and cta.get("heading"):
            parts.append(f"<h2>{escape(str(cta['heading']))}</h2>")
            if cta.get("text"):
                parts.append(f"<p>{escape(str(cta['text']))}</p>")
        return "\n".join(p for p in parts if p.strip())

    def _render_services(self, content: dict, media_ids: list[int]) -> str:
        parts: list[str] = []
        if content.get("hero_heading"):
            parts.append(f"<h1>{escape(str(content['hero_heading']))}</h1>")
        if content.get("intro"):
            parts.append(f"<p>{escape(str(content['intro']))}</p>")
        for svc in content.get("services", []):
            if isinstance(svc, dict):
                parts.append(self._render_service_html(svc))
        return "\n".join(p for p in parts if p.strip())

    def _render_service_html(self, svc: dict) -> str:
        parts: list[str] = []
        heading = svc.get("heading") or svc.get("name") or ""
        if heading:
            parts.append(f"<h2>{escape(str(heading))}</h2>")
        if svc.get("description"):
            parts.append(f"<p>{escape(str(svc['description']))}</p>")
        benefits = svc.get("benefits", [])
        if benefits:
            parts.append("<ul>")
            for b in benefits:
                parts.append(f"<li>{escape(str(b))}</li>")
            parts.append("</ul>")
        steps = svc.get("process_steps", [])
        if steps:
            parts.append("<ol>")
            for s in steps:
                parts.append(f"<li>{escape(str(s))}</li>")
            parts.append("</ol>")
        if svc.get("cta"):
            parts.append(
                f'<p><a class="btn" href="/contact/">'
                f'{escape(str(svc["cta"]))}</a></p>'
            )
        return "\n".join(parts)

    def _render_contact(self, content: dict, media_ids: list[int]) -> str:
        parts: list[str] = []
        if content.get("hero_heading"):
            parts.append(f"<h1>{escape(str(content['hero_heading']))}</h1>")
        if content.get("intro"):
            parts.append(f"<p>{escape(str(content['intro']))}</p>")
        section = content.get("contact_section", {})
        if isinstance(section, dict):
            if section.get("heading"):
                parts.append(f"<h2>{escape(str(section['heading']))}</h2>")
            if section.get("text"):
                parts.append(f"<p>{escape(str(section['text']))}</p>")
            # Simple HTML contact form placeholder
            btn = escape(str(section.get("form_cta") or "Send Message"))
            parts.append(
                '<form class="contact-form" method="post" action="#">'
                '<p><label>Name<br><input type="text" name="name" required></label></p>'
                '<p><label>Email<br><input type="email" name="email" required></label></p>'
                '<p><label>Message<br><textarea name="message" rows="5" required>'
                '</textarea></label></p>'
                f'<p><button type="submit">{btn}</button></p>'
                '</form>'
            )
        return "\n".join(p for p in parts if p.strip())

    def _render_faq(self, content: dict, media_ids: list[int]) -> str:
        parts: list[str] = []
        if content.get("hero_heading"):
            parts.append(f"<h1>{escape(str(content['hero_heading']))}</h1>")
        if content.get("intro"):
            parts.append(f"<p>{escape(str(content['intro']))}</p>")
        for item in content.get("faqs", []):
            if isinstance(item, dict):
                q = escape(str(item.get("question", "")))
                a = escape(str(item.get("answer", "")))
                parts.append(f"<h3>{q}</h3>")
                parts.append(f"<p>{a}</p>")
        return "\n".join(p for p in parts if p.strip())

    def _render_generic(self, content: dict, media_ids: list[int]) -> str:
        if content.get("intro"):
            return f"<p>{escape(str(content['intro']))}</p>"
        return "<p></p>"

    # ── Internal: settings / theme / menu / media ──────────────────────────────

    def _configure_site_settings(self, biz: dict) -> list[str]:
        """Update WordPress options from business_profile.json.

        Args:
            biz: Parsed business_profile dict.

        Returns:
            List of option names that were successfully updated.
        """
        updated: list[str] = []

        title = biz.get("company_name") or ""
        if title:
            self._wpcli("option", "update", "blogname", title)
            updated.append("blogname")

        # Tagline from unique value or industry
        tagline = (
            biz.get("unique_value")
            or biz.get("industry")
            or ""
        )
        if tagline:
            self._wpcli("option", "update", "blogdescription", str(tagline)[:160])
            updated.append("blogdescription")

        # Timezone — German local businesses default
        try:
            self._wpcli("option", "update", "timezone_string", "Europe/Berlin")
            updated.append("timezone_string")
        except WordPressError:
            pass

        # Language — prefer profile languages, else de_DE
        langs = biz.get("languages") or []
        locale = "de_DE"
        if langs:
            first = str(langs[0]).lower()
            if first.startswith("en"):
                locale = "en_US"
            elif first.startswith("de"):
                locale = "de_DE"
        try:
            self._wpcli("option", "update", "WPLANG", locale)
            updated.append("WPLANG")
        except WordPressError:
            pass

        # Permalinks
        try:
            self._wpcli("rewrite", "structure", "/%postname%/", "--hard")
            updated.append("permalink_structure")
        except WordPressError as exc:
            log.warning("Permalink update failed: {e}", e=exc)

        return updated

    def _activate_default_theme(self) -> str:
        """Activate a theme that supports classic menus when possible.

        Block themes (Twenty Twenty-Three/Four/Five) often register **no**
        classic menu locations, so WordPress falls back to listing every page
        in the header — which looks broken. Prefer classic themes first.
        """
        preferred = (
            "twentytwentyone",   # classic menus
            "twentytwenty",
            "twentytwentytwo",
            "twentytwentyfour",
            "twentytwentythree",
            "twentytwentyfive",
        )
        try:
            raw = self._wpcli("theme", "list", "--format=json")
            themes = json.loads(raw) if raw.strip() else []
        except (WordPressError, json.JSONDecodeError):
            return ""

        installed = {t.get("name", ""): t for t in themes if isinstance(t, dict)}

        # Prefer classic menu themes even if a block theme is already active
        for slug in preferred:
            if slug not in installed:
                continue
            info = installed[slug]
            if info.get("status") == "active":
                return slug
            try:
                self._wpcli("theme", "activate", slug)
                log.info("Activated theme: {t}", t=slug)
                return slug
            except WordPressError as exc:
                log.warning("Could not activate {t}: {e}", t=slug, e=exc)

        # Try installing Twenty Twenty-One once (needs network)
        if "twentytwentyone" not in installed:
            try:
                self._wpcli("theme", "install", "twentytwentyone", "--activate")
                log.info("Installed and activated twentytwentyone")
                return "twentytwentyone"
            except WordPressError as exc:
                log.warning("Could not install twentytwentyone: {e}", e=exc)

        for name, info in installed.items():
            if info.get("status") == "active":
                return name

        if installed:
            first = next(iter(installed))
            self._wpcli("theme", "activate", first)
            return first

        return ""

    def _import_images(
        self,
        images: list[dict],
        report: GenerationReport,
    ) -> list[int]:
        """Import downloaded images into the Media Library.

        Args:
            images: Image metadata list from images.json.
            report: Mutable generation report.

        Returns:
            List of successfully imported attachment IDs.
        """
        media_ids: list[int] = []
        count = 0

        for img in images:
            if count >= _MAX_MEDIA_IMPORT:
                report.warnings.append(
                    f"Media import capped at {_MAX_MEDIA_IMPORT} images"
                )
                break

            local = img.get("local_path") or ""
            if not local:
                continue
            path = Path(local)
            if not path.exists():
                # Try relative to project images dir via filename
                report.warnings.append(f"Image missing: {local}")
                continue
            if path.suffix.lower() not in _IMAGE_EXTS:
                continue

            title = img.get("alt_text") or img.get("filename") or path.stem
            try:
                mid = self.upload_media(path, title=str(title)[:100])
                media_ids.append(mid)
                report.images_imported.append({
                    "attachment_id": mid,
                    "filename":      path.name,
                    "source_url":    img.get("source_url", ""),
                    "alt_text":      img.get("alt_text", ""),
                })
                count += 1
            except WordPressError as exc:
                report.warnings.append(f"Media import {path.name}: {exc}")
                log.warning("Media import failed for {f}: {e}", f=path.name, e=exc)

        log.info("Imported {n} images into Media Library", n=len(media_ids))
        return media_ids

    def _create_primary_menu(self, page_ids: dict[str, int]) -> list[str]:
        """Create the Primary menu and assign it to an available location.

        Args:
            page_ids: Mapping of logical slug → WordPress post ID.

        Returns:
            List of menu item labels added.
        """
        # Delete existing Primary menu if present
        try:
            raw = self._wpcli("menu", "list", "--format=json")
            menus = json.loads(raw) if raw.strip() else []
            for menu in menus:
                if menu.get("name") == _MENU_NAME:
                    self._wpcli("menu", "delete", str(menu["term_id"]))
        except (WordPressError, json.JSONDecodeError, KeyError):
            pass

        self._wpcli("menu", "create", _MENU_NAME)

        # ONLY the five standard pages — short labels
        order = [s for s in _STANDARD_PAGES if s in page_ids]
        items: list[str] = []
        for slug in order:
            pid = page_ids.get(slug)
            if not pid:
                continue
            label = _PAGE_TITLES.get(slug, slug.replace("-", " ").title())
            try:
                self._wpcli(
                    "menu", "item", "add-post", _MENU_NAME, str(pid),
                    f"--title={label}",
                )
                items.append(label)
            except WordPressError as exc:
                log.warning("Menu item {s} failed: {e}", s=slug, e=exc)

        location = self._resolve_menu_location()
        if location:
            try:
                self._wpcli("menu", "location", "assign", _MENU_NAME, location)
                log.info("Assigned Primary menu to location '{loc}'", loc=location)
            except WordPressError as exc:
                log.warning(
                    "Could not assign menu to '{loc}': {e}",
                    loc=location, e=exc,
                )
        else:
            log.warning(
                "Theme has no classic menu locations — header may list all pages. "
                "Prefer twentytwentyone."
            )

        return items

    def _resolve_menu_location(self) -> str:
        """Return a usable classic menu location slug for the active theme."""
        preferred = (
            _MENU_LOCATION,
            "primary",
            "menu-1",
            "header",
            "header-menu",
            "main",
            "main-menu",
            "top",
            "footer",
        )
        try:
            raw = self._wpcli("menu", "location", "list", "--format=json")
            locs = json.loads(raw) if raw.strip() else []
        except (WordPressError, json.JSONDecodeError):
            locs = []
        available = {
            str(x.get("location") or x.get("name") or "")
            for x in locs
            if isinstance(x, dict)
        }
        available.discard("")
        for loc in preferred:
            if loc in available:
                return loc
        return next(iter(available), "")

    def _prune_orphan_pages(self, *, keep_ids: set[int]) -> list[str]:
        """Delete published pages that are not part of the standard demo set."""
        raw = self._wpcli(
            "post", "list",
            "--post_type=page",
            "--post_status=publish",
            "--fields=ID,post_name,post_title",
            "--format=json",
        )
        pages = json.loads(raw) if raw.strip() else []
        pruned: list[str] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            pid = int(page.get("ID") or 0)
            slug = str(page.get("post_name") or "")
            if pid in keep_ids:
                continue
            if slug in _ALLOWED_WP_SLUGS:
                continue
            try:
                self._wpcli("post", "delete", str(pid), "--force")
                pruned.append(slug or str(pid))
            except WordPressError as exc:
                log.warning("Could not prune page {s}: {e}", s=slug, e=exc)
        return pruned

    def _set_featured_image(self, page_id: int, media_id: int) -> None:
        """Set the featured image for a page."""
        self._wpcli(
            "post", "meta", "update",
            str(page_id), "_thumbnail_id", str(media_id),
        )

    # ── Internal: file loaders ─────────────────────────────────────────────────

    def _load_business_profile(self, project_dir: Path) -> dict:
        path = project_dir / "json" / "business_profile.json"
        data = self._load_json(path, default={})
        return data if isinstance(data, dict) else {}

    def _load_optimized_pages(self, project_dir: Path) -> dict[str, dict]:
        """Load all optimized_<slug>.json files from the project.

        Returns:
            Mapping of slug → content dict.
        """
        json_dir = project_dir / "json"
        pages: dict[str, dict] = {}
        if not json_dir.exists():
            return pages

        for path in sorted(json_dir.glob("optimized_*.json")):
            slug = path.stem.removeprefix("optimized_")
            data = self._load_json(path, default=None)
            if isinstance(data, dict):
                pages[slug] = data
            else:
                log.warning("Skipping malformed {f}", f=path.name)

        return pages

    def _load_meta_data(self, project_dir: Path) -> dict[str, dict]:
        path = project_dir / "json" / "meta_data.json"
        data = self._load_json(path, default={})
        return data if isinstance(data, dict) else {}

    def _load_image_metadata(self, project_dir: Path) -> list[dict]:
        path = project_dir / "json" / "images.json"
        data = self._load_json(path, default=[])
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        return []

    def _save_report(self, project_dir: Path, report: GenerationReport) -> None:
        out = project_dir / "json" / "generation_report.json"
        self._write_json(out, report.model_dump())
        log.info("Saved generation report → {p}", p=out)

    # ── Internal: helpers ──────────────────────────────────────────────────────

    def _find_page_id(self, wp_slug: str) -> int | None:
        """Return the post ID of an existing page with *wp_slug*, or None."""
        try:
            raw = self._wpcli(
                "post", "list",
                "--post_type=page",
                f"--name={wp_slug}",
                "--field=ID",
                "--format=ids",
            )
            token = raw.strip().split()[0] if raw.strip() else ""
            return int(token) if token.isdigit() else None
        except (WordPressError, ValueError, IndexError):
            return None

    @staticmethod
    def _parse_id(raw: str) -> int:
        """Extract a single integer ID from WP-CLI porcelain output."""
        token = (raw or "").strip().split()[0] if (raw or "").strip() else ""
        if not token.isdigit():
            raise WordPressError(
                f"Could not parse post ID from WP-CLI output: {raw!r}"
            )
        return int(token)

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert *text* to a URL-safe slug."""
        text = text.lower().strip()
        text = re.sub(r"[^\w\s-]", "", text)
        text = re.sub(r"[\s_]+", "-", text)
        text = re.sub(r"-+", "-", text).strip("-")
        return text[:80]

    def _extract_page_title(self, slug: str, content: dict) -> str:
        """Short WordPress page title for nav — never use SEO hero headlines."""
        # Prefer fixed short labels for standard pages
        if slug in _PAGE_TITLES:
            return _PAGE_TITLES[slug]
        if content.get("nav_title"):
            return str(content["nav_title"])[:60]
        if content.get("title") and len(str(content["title"])) <= 40:
            return str(content["title"])[:60]
        return slug.replace("-", " ").title()[:60]

    def _page_title(self, slug: str, content: dict, biz: dict) -> str:
        """Human-readable title for the generation report."""
        return self._extract_page_title(slug, content)

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        write_versioned_json(path, data)

    @staticmethod
    def _load_json(path: Path, *, default: Any) -> Any:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.debug("Not found: {p}", p=path.name)
            return default
        except json.JSONDecodeError as exc:
            log.warning("Invalid JSON in {p}: {e}", p=path.name, e=exc)
            return default
        except OSError as exc:
            log.warning("Cannot read {p}: {e}", p=path.name, e=exc)
            return default
        data = unwrap_json(raw)
        if default is not None and type(default) is list and not isinstance(data, list):
            return default
        if default is not None and type(default) is dict and not isinstance(data, dict):
            return default
        return data
