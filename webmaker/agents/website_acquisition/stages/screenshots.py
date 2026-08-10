"""
Stage 7 — Screenshots inventory (reuse crawler shots; optional viewport).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from webmaker.core.logging import get_logger

log = get_logger("acquisition.screenshots")


def collect_screenshots(
    data_dir: Path,
    package_dir: Path,
    *,
    settings: object | None = None,
    target_url: str = "",
) -> dict[str, Any]:
    """Ensure per-page screenshots are referenced under website_package/screenshots."""
    data_dir = Path(data_dir)
    package_dir = Path(package_dir)
    shot_src = data_dir / "screenshots"
    shot_dst = package_dir / "screenshots"
    shot_dst.mkdir(parents=True, exist_ok=True)

    pages: list[dict[str, Any]] = []
    raw_dir = data_dir / "raw"
    slugs = [p.stem for p in sorted(raw_dir.glob("*.html"))] if raw_dir.is_dir() else []
    if not slugs and shot_src.is_dir():
        slugs = [p.stem for p in sorted(shot_src.glob("*.png"))]

    notes: list[str] = []
    for slug in slugs:
        src = shot_src / f"{slug}.png"
        entry: dict[str, Any] = {
            "slug": slug,
            "full_page": "",
            "viewport": "",
            "section_crops": [],
        }
        if src.is_file():
            dst = shot_dst / f"{slug}.png"
            if not dst.is_file() or dst.stat().st_mtime < src.stat().st_mtime:
                shutil.copy2(src, dst)
            entry["full_page"] = f"screenshots/{slug}.png"
        else:
            notes.append(f"Missing full-page screenshot for {slug}")
            # Best-effort capture if crawler left a gap
            if settings and target_url:
                try:
                    from webmaker.modules.website_crawler import WebsiteCrawler
                    crawler = WebsiteCrawler(settings)
                    # Only try home-like URLs without guessing deep paths
                    if slug in ("home", "index", "homepage"):
                        out = shot_src / f"{slug}.png"
                        shot_src.mkdir(parents=True, exist_ok=True)
                        crawler.take_screenshot(target_url, out)
                        if out.is_file():
                            shutil.copy2(out, shot_dst / f"{slug}.png")
                            entry["full_page"] = f"screenshots/{slug}.png"
                            notes = [n for n in notes if slug not in n]
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"Could not capture screenshot for {slug}: {exc}")

        pages.append(entry)

    notes.append(
        "Section-level screenshot crops not generated in v1 "
        "(full-page shots used for validation)."
    )

    payload = {"pages": pages, "notes": notes}
    out = package_dir / "screenshots_index.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Screenshots indexed — {n} page(s)", n=len(pages))
    return payload
