"""
Stage 9 — Validation checklist + completeness scores.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from webmaker.core.logging import get_logger
from webmaker.schemas.acquisition import (
    CategoryScores,
    ChecklistItem,
    PageContentStats,
    PageValidation,
    ValidationReport,
)

log = get_logger("acquisition.validator")


def validate_package(
    package_dir: Path,
    *,
    stats: list[PageContentStats],
    sections: dict[str, Any],
    assets: dict[str, Any],
    brand: dict[str, Any],
    navigation: Any,
    threshold: float = 0.95,
) -> ValidationReport:
    """Compare extracted structure against expected signals; compute scores."""
    package_dir = Path(package_dir)
    page_validations: list[PageValidation] = []
    gaps: list[str] = []

    sec_by_slug = {
        p.get("slug"): p for p in (sections.get("pages") or []) if isinstance(p, dict)
    }

    for st in stats:
        items: list[ChecklistItem] = []
        types = set(sec_by_slug.get(st.slug, {}).get("section_types") or [])

        def add(key: str, label: str, ok: bool, detail: str = "") -> None:
            items.append(ChecklistItem(key=key, label=label, ok=ok, detail=detail))
            if not ok:
                gaps.append(f"{st.slug}: {label}" + (f" ({detail})" if detail else ""))

        add("raw_html", "Raw HTML stored", st.has_raw_html)
        add("screenshot", "Screenshot found", st.has_screenshot)
        add("navigation", "Navigation signals", _nav_ok(navigation) or st.links > 0)
        add("hero", "Hero / primary heading", st.h1 > 0 or "hero" in types,
            f"h1={st.h1}")
        add("text", "Paragraph text", st.paragraphs > 0, f"paragraphs={st.paragraphs}")
        add("images", "Images extracted", st.images > 0 or bool(assets.get("images")),
            f"images={st.images}")
        add("buttons", "Buttons / CTAs", st.buttons > 0 or "cta" in types,
            f"buttons={st.buttons}")
        add("sections", "Layout sections detected", st.sections > 0 or bool(types),
            f"sections={st.sections or len(types)}")

        # Homepage-specific extras
        if st.slug in ("home", "homepage", "index"):
            add("services", "Services / cards section",
                any(t in types for t in ("services_grid", "features_grid", "cards", "labeled_sections")),
                ",".join(sorted(types))[:80])
            add("footer_or_contact", "Contact / footer signals",
                any(t in types for t in ("contact", "footer", "cta")) or st.forms > 0)

        ok_n = sum(1 for i in items if i.ok)
        score = ok_n / len(items) if items else 0.0
        page_validations.append(PageValidation(
            slug=st.slug, url=st.url, items=items, score=round(score, 4),
        ))

    # Category scores
    n_pages = max(len(stats), 1)
    pages_score = sum(1 for s in stats if s.has_raw_html) / n_pages
    text_score = sum(1 for s in stats if (s.h1 + s.h2 + s.paragraphs) > 0) / n_pages
    images_score = 1.0 if (assets.get("counts") or {}).get("images", 0) > 0 else (
        sum(1 for s in stats if s.images > 0) / n_pages
    )
    buttons_score = sum(1 for s in stats if s.buttons > 0) / n_pages
    sections_score = sum(1 for s in stats if s.sections > 0) / n_pages
    if sections_score == 0 and sec_by_slug:
        sections_score = sum(
            1 for s in stats if (sec_by_slug.get(s.slug) or {}).get("section_count", 0) > 0
        ) / n_pages
    nav_score = 1.0 if _nav_ok(navigation) else (
        sum(1 for s in stats if s.links > 0) / n_pages
    )
    brand_score = _brand_score(brand, assets)

    scores = CategoryScores(
        pages=round(pages_score, 4),
        text=round(text_score, 4),
        images=round(images_score, 4),
        buttons=round(buttons_score, 4),
        sections=round(sections_score, 4),
        navigation=round(nav_score, 4),
        brand_assets=round(brand_score, 4),
    )
    overall = round(
        (
            scores.pages
            + scores.text
            + scores.images
            + scores.buttons
            + scores.sections
            + scores.navigation
            + scores.brand_assets
        ) / 7.0,
        4,
    )
    passed = overall >= threshold

    notes: list[str] = []
    if not passed:
        notes.append(
            f"Overall completeness {overall:.1%} is below threshold {threshold:.0%}. "
            "Review gaps before migration."
        )
    shot_missing = [s.slug for s in stats if not s.has_screenshot]
    if shot_missing:
        notes.append("Missing screenshots: " + ", ".join(shot_missing))

    report = ValidationReport(
        threshold=threshold,
        overall=overall,
        passed=passed,
        scores=scores,
        pages=page_validations,
        gaps=gaps[:80],
        notes=notes,
    )
    out = package_dir / "validation_report.json"
    out.write_text(
        json.dumps(report.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info(
        "Validation overall={o:.1%} passed={p} gaps={g}",
        o=overall, p=passed, g=len(gaps),
    )
    return report


def _nav_ok(navigation: Any) -> bool:
    if not navigation:
        return False
    if isinstance(navigation, list) and navigation:
        return True
    if isinstance(navigation, dict):
        for key in ("items", "links", "pages", "menu"):
            v = navigation.get(key)
            if isinstance(v, list) and v:
                return True
        return bool(navigation)
    return False


def _brand_score(brand: dict[str, Any], assets: dict[str, Any]) -> float:
    points = 0
    total = 4
    if brand.get("primary_colors"):
        points += 1
    if brand.get("fonts"):
        points += 1
    if (assets.get("logo") or assets.get("counts", {}).get("logos")):
        points += 1
    if assets.get("favicon") or assets.get("counts", {}).get("favicons"):
        points += 1
    return points / total
