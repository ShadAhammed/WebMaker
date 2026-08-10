"""
Stage 8 — Assemble website_package JSON files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from webmaker.core.logging import get_logger

log = get_logger("acquisition.package")


def write_package(
    data_dir: Path,
    package_dir: Path,
    *,
    target_url: str,
    content: dict[str, Any],
    assets: dict[str, Any],
    brand: dict[str, Any],
    sections: dict[str, Any],
    html_meta: list[dict[str, Any]],
    screenshots: dict[str, Any],
) -> dict[str, Any]:
    """Write business/pages/navigation JSON and return package summary paths."""
    data_dir = Path(data_dir)
    package_dir = Path(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    # Navigation — copy or slim from crawl
    nav_src = data_dir / "json" / "navigation.json"
    navigation: Any = {}
    if nav_src.is_file():
        try:
            navigation = json.loads(nav_src.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            navigation = {}
    _write(package_dir / "navigation.json", navigation)

    # Pages index
    pages_payload = []
    for p in content.get("pages") or []:
        slug = p.get("slug") or ""
        sec_page = next(
            (s for s in (sections.get("pages") or []) if s.get("slug") == slug),
            {},
        )
        shot = next(
            (s for s in (screenshots.get("pages") or []) if s.get("slug") == slug),
            {},
        )
        html_row = next((h for h in html_meta if h.get("slug") == slug), {})
        pages_payload.append({
            "slug": slug,
            "url": p.get("url") or "",
            "title": p.get("title") or "",
            "section_count": sec_page.get("section_count") or 0,
            "section_types": sec_page.get("section_types") or [],
            "has_screenshot": bool(shot.get("full_page")),
            "has_html": bool(html_row),
            "counts": {
                "h1": len(p.get("h1") or []),
                "h2": len(p.get("h2") or []),
                "h3": len(p.get("h3") or []),
                "paragraphs": len(p.get("paragraphs") or []),
                "buttons": len(p.get("buttons") or []),
                "images": len(p.get("images") or []) if isinstance(p.get("images"), list) else 0,
                "forms": len(p.get("forms") or []),
                "lists": len(p.get("lists") or []),
            },
        })
    _write(package_dir / "pages.json", {"target_url": target_url, "pages": pages_payload})

    # Business stub from content (no AI)
    phones: list[str] = []
    emails: list[str] = []
    addresses: list[str] = []
    hours: list[str] = []
    for p in content.get("pages") or []:
        phones.extend(p.get("phones") or [])
        emails.extend(p.get("emails") or [])
        addresses.extend(p.get("addresses") or [])
        hours.extend(p.get("opening_hours") or [])
    business = {
        "source_url": target_url,
        "name": "",
        "phones": list(dict.fromkeys(phones))[:10],
        "emails": list(dict.fromkeys(emails))[:10],
        "addresses": list(dict.fromkeys(addresses))[:10],
        "opening_hours": list(dict.fromkeys(hours))[:10],
        "logo": (assets.get("logo") or [None])[0],
    }
    # Name heuristic from home title
    home = next(
        (p for p in pages_payload if p.get("slug") in ("home", "homepage", "index")),
        pages_payload[0] if pages_payload else {},
    )
    business["name"] = (home.get("title") or "").split("|")[0].strip()[:120]
    _write(package_dir / "business.json", business)

    # brand/assets/sections/content already written by earlier stages —
    # ensure copies exist if callers passed payloads only
    if not (package_dir / "brand.json").is_file():
        _write(package_dir / "brand.json", brand)
    if not (package_dir / "assets.json").is_file():
        _write(package_dir / "assets.json", assets)
    if not (package_dir / "sections.json").is_file():
        _write(package_dir / "sections.json", sections)

    summary = {
        "package_dir": str(package_dir),
        "files": sorted(p.name for p in package_dir.glob("*.json")),
        "page_count": len(pages_payload),
    }
    log.info("Website package written → {d} ({n} pages)", d=package_dir, n=len(pages_payload))
    return summary


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
