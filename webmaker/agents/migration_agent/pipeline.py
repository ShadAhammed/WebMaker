"""
webmaker.agents.migration_agent.pipeline
========================================
Agent 0 internal pipeline:

    Website → DOM Extractor → Layout Analyzer → Semantic Layout Model
           → Theme Mapper → optimized_*.json (WordPress body_html)

Deterministic. No AI. External MigrationAgent API unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

from webmaker.agents.migration_agent.dom_extractor import extract_dom_from_file
from webmaker.agents.migration_agent.layout_analyzer import analyze_page
from webmaker.agents.migration_agent.passthrough_writer import (
    _DE_TITLES,
    _STANDARD_SLUGS,
    _build_empty_page,
    _coerce_to_str_list,
    _effective_type,
    _first_str,
    _page_priority,
    _resolve_raw_html,
    _resolve_slug_from_type,
    write_passthrough_pages,
)
from webmaker.agents.migration_agent.semantic_model import PageLayout, SiteLayoutModel
from webmaker.agents.migration_agent.theme_mapper import map_page_to_html
from webmaker.core.logging import get_logger
from webmaker.core.schema import write_versioned_json

log = get_logger("migration.pipeline")


def write_layout_migrated_pages(
    data_dir: Path,
    *,
    theme_id: str = "",
    source_url: str = "",
) -> list[str]:
    """Run the universal layout pipeline and write ``optimized_*.json``.

    Falls back to the legacy passthrough writer when raw HTML is unavailable
    or the analyzer produces no usable sections for a page.
    """
    data_dir = Path(data_dir)
    json_dir = data_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    per_page_dir = json_dir / "pages"
    page_files = sorted(per_page_dir.glob("*.json")) if per_page_dir.is_dir() else []

    if not page_files:
        log.warning("No json/pages/*.json — falling back to passthrough writer")
        return write_passthrough_pages(data_dir)

    page_data_list: list[dict] = []
    for f in page_files:
        try:
            page_data_list.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read {f}: {e}", f=f.name, e=exc)

    page_data_list.sort(key=lambda d: _page_priority(_effective_type(d)))

    site = SiteLayoutModel(source_url=source_url)
    written: list[str] = []
    seen_slugs: set[str] = set()
    used_pipeline = 0
    used_fallback = 0

    for data in page_data_list:
        slug = _resolve_slug_from_type(_effective_type(data), seen_slugs)
        seen_slugs.add(slug)

        title = (
            _first_str(data.get("meta_title"))
            or _first_str(data.get("title"))
            or _DE_TITLES.get(slug, slug.title())
        ).strip()
        meta_desc = (_first_str(data.get("meta_description")) or "").strip()
        url = str(data.get("url") or data.get("final_url") or "")

        page_layout: PageLayout | None = None
        body_html = ""
        primary_h1 = ""

        raw_path = _resolve_raw_html(data, data_dir)
        if raw_path is not None:
            try:
                doc = extract_dom_from_file(raw_path, url=url)
                page_layout = analyze_page(
                    doc,
                    slug=slug,
                    page_name=_first_str(data.get("title")) or title,
                )
                page_layout.title = page_layout.title or title
                page_layout.url = page_layout.url or url
                body_html = map_page_to_html(page_layout, theme_id=theme_id)
                if page_layout.sections:
                    for sec in page_layout.sections:
                        if sec.type == "hero" and sec.heading:
                            primary_h1 = sec.heading
                            break
                    if not primary_h1:
                        for lvl, text in (doc.headings or []):
                            if lvl == "h1" and text:
                                primary_h1 = text
                                break
                used_pipeline += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "Layout pipeline failed for {s} ({p}): {e} — will fallback",
                    s=slug, p=raw_path.name, e=exc,
                )
                page_layout = None
                body_html = ""

        # Fallback when no raw HTML or empty mapped output.
        if not body_html or len(body_html) < 120 or (
            page_layout is not None and not page_layout.sections
        ):
            log.info("Using passthrough fallback for slug={s}", s=slug)
            from webmaker.agents.migration_agent.passthrough_writer import (
                _build_from_rich_data,
                _build_image_index,
            )
            image_index = _build_image_index(data_dir)
            payload = _build_from_rich_data(slug, data, data_dir, image_index)
            write_versioned_json(json_dir / f"optimized_{slug}.json", payload)
            written.append(slug)
            used_fallback += 1
            # Still record a minimal layout entry for the artifact.
            site.pages.append(
                PageLayout(
                    page=title,
                    slug=slug,
                    title=title,
                    url=url,
                    sections=[],
                )
            )
            continue

        assert page_layout is not None
        site.pages.append(page_layout)

        h2_list = _coerce_to_str_list(data.get("h2", []))[:12]
        headings = [primary_h1 or title] + [
            h for h in h2_list if h and h != (primary_h1 or title)
        ]

        payload = {
            "slug":             slug,
            "title":            title,
            "nav_title":        title,
            "body_html":        body_html,
            "meta_title":       (_first_str(data.get("meta_title")) or title)[:60],
            "meta_description": meta_desc[:160],
            "headings":         headings[:20],
            "layout_sections":  [s.type for s in page_layout.sections],
            "hero": {
                "heading":     primary_h1 or title,
                "subheading":  meta_desc[:120] if meta_desc else "",
                "cta_primary": "",
            },
        }
        out = json_dir / f"optimized_{slug}.json"
        write_versioned_json(out, payload)
        written.append(slug)
        log.info(
            "Layout-migrated page → {f}  sections={types} body_len={n}",
            f=out.name,
            types=[s.type for s in page_layout.sections],
            n=len(body_html),
        )

    for std_slug in _STANDARD_SLUGS:
        if std_slug not in seen_slugs:
            out = json_dir / f"optimized_{std_slug}.json"
            write_versioned_json(out, _build_empty_page(std_slug))
            written.append(std_slug)
            log.info("Empty fallback page written → {f}", f=out.name)

    # Persist Semantic Layout Model for debugging / downstream inspection.
    layout_path = json_dir / "layout_model.json"
    write_versioned_json(layout_path, site.model_dump())
    artifacts = data_dir / "artifacts"
    if artifacts.is_dir() or True:
        artifacts.mkdir(parents=True, exist_ok=True)
        write_versioned_json(artifacts / "layout_model.json", site.model_dump())

    log.info(
        "Layout pipeline done — pages={n} pipeline={p} fallback={f} theme={t}",
        n=len(written), p=used_pipeline, f=used_fallback, t=theme_id or "(default)",
    )
    return written
