"""
webmaker.agents.live_demo_renderer.wordpress_renderer
=====================================================
Module 5.2 — WordPressRenderer.

Applies approved OP-Content patches to WordPress. Never invents content.
Only updates pages touched by the render request (no full-site wipe).
"""

from __future__ import annotations

from pathlib import Path

from webmaker.agents.live_demo_renderer.materialize_content import (
    materialize_optimized_pages,
    restore_last_render_backup,
)
from webmaker.core.logging import get_logger
from webmaker.schemas.render import RenderRequest, RenderResult

log = get_logger("renderer.wordpress")


class WordPressRenderer:
    """Apply a RenderRequest to the local WordPress site (no AI)."""

    def __init__(self, settings: object, data_dir: Path, *, generator=None) -> None:
        self._settings = settings
        self._data_dir = Path(data_dir)
        self._generator = generator

    def _get_generator(self):
        if self._generator is None:
            from webmaker.modules.wordpress_generator import WordPressGenerator
            self._generator = WordPressGenerator(self._settings)
        return self._generator

    def render(self, request: RenderRequest) -> RenderResult:
        """Patch optimized pages then hydrate only those pages into WordPress."""
        generator = self._get_generator()
        errors: list[str] = []
        wp_url = getattr(self._settings, "wordpress_url", "")

        try:
            written = materialize_optimized_pages(self._data_dir, request)
            if written:
                log.info(
                    "Patched {n} optimized page file(s) from approved OP-Content",
                    n=len(written),
                )
        except Exception as exc:  # noqa: BLE001
            log.exception("Could not materialize optimized pages")
            return RenderResult(
                wp_url=wp_url,
                success=False,
                errors=[f"Could not prepare page content: {exc}"],
            )

        # Only push pages that were actually patched (or explicitly requested)
        page_slugs = [
            p.stem.removeprefix("optimized_") for p in written
        ] or list(request.page_slugs or [])

        if not page_slugs:
            msg = (
                "No pages were patched. Tick a tip that includes ready German "
                "website text (proposed_html), then retry."
            )
            log.error(msg)
            return RenderResult(wp_url=wp_url, success=False, errors=[msg])

        try:
            # Prefer surgical hydrate of only affected pages — never reset WP
            if request.theme_id:
                try:
                    generator.install_theme_stack(request.theme_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Theme install warning: {e}", e=exc)
                    errors.append(f"theme: {exc}")
                if request.template_id:
                    try:
                        generator.import_starter_template(
                            request.template_id, request.theme_id
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Template import warning: {e}", e=exc)
                        errors.append(f"template import: {exc}")

            log.info("Hydrating only pages: {s}", s=page_slugs)
            result = generator.hydrate_template_content(
                self._data_dir,
                page_slugs=page_slugs,
            )
            pages = list(getattr(result, "pages_created", []) or [])
            if getattr(result, "errors", None):
                errors.extend(str(e) for e in result.errors)

            return RenderResult(
                wp_url=getattr(result, "wp_url", "") or wp_url,
                pages_rendered=pages,
                theme_applied=request.theme_id or "",
                template_applied=request.template_id or "",
                success=bool(getattr(result, "success", False)) and not _fatal(errors),
                errors=errors,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("WordPress render failed")
            errors.append(str(exc))
            return RenderResult(wp_url=wp_url, success=False, errors=errors)

    def undo_last_render(self) -> RenderResult:
        """Restore last-render backup and re-hydrate those pages into WordPress."""
        wp_url = getattr(self._settings, "wordpress_url", "")
        try:
            slugs = restore_last_render_backup(self._data_dir)
        except FileNotFoundError as exc:
            return RenderResult(
                wp_url=wp_url,
                success=False,
                errors=[str(exc)],
            )
        if not slugs:
            return RenderResult(
                wp_url=wp_url,
                success=False,
                errors=["Backup contained no pages to restore."],
            )
        try:
            generator = self._get_generator()
            result = generator.hydrate_template_content(
                self._data_dir,
                page_slugs=slugs,
            )
            pages = list(getattr(result, "pages_created", []) or [])
            errors = [str(e) for e in (getattr(result, "errors", None) or [])]
            return RenderResult(
                wp_url=getattr(result, "wp_url", "") or wp_url,
                pages_rendered=pages,
                success=bool(getattr(result, "success", False)) and not _fatal(errors),
                errors=errors,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Undo hydrate failed")
            return RenderResult(wp_url=wp_url, success=False, errors=[str(exc)])


def _fatal(errors: list[str]) -> bool:
    return any("Page " in e or "verification failed" in e for e in errors)
