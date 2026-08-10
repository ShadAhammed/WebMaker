"""
Stage 6 — Layout / section detection (reuses migration layout_analyzer).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from webmaker.core.logging import get_logger

log = get_logger("acquisition.layout")


def detect_layouts(data_dir: Path, package_dir: Path) -> dict[str, Any]:
    """Run CMS-agnostic layout analyzer on each raw HTML page."""
    data_dir = Path(data_dir)
    package_dir = Path(package_dir)

    from webmaker.agents.migration_agent.dom_extractor import extract_dom_from_file
    from webmaker.agents.migration_agent.layout_analyzer import analyze_page

    raw_dir = data_dir / "raw"
    pages_out: list[dict[str, Any]] = []
    if not raw_dir.is_dir():
        log.warning("No raw/ — skipping layout stage")
        return {"pages": []}

    for raw_path in sorted(raw_dir.glob("*.html")):
        slug = raw_path.stem
        try:
            doc = extract_dom_from_file(raw_path)
            layout = analyze_page(doc, slug=slug, page_name=slug)
            sections = [s.model_dump() for s in layout.sections]
            pages_out.append({
                "slug": slug,
                "title": layout.title,
                "url": layout.url,
                "sections": sections,
                "section_types": [s.get("type") for s in sections],
                "section_count": len(sections),
            })
            log.info(
                "Layout slug={s}: {n} sections → {t}",
                s=slug, n=len(sections), t=[s.get("type") for s in sections][:12],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Layout failed for {s}: {e}", s=slug, e=exc)
            pages_out.append({
                "slug": slug,
                "sections": [],
                "section_types": [],
                "section_count": 0,
                "error": str(exc),
            })

    payload = {"pages": pages_out}
    out = package_dir / "sections.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload
