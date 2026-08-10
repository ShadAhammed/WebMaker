"""
Stage 3 — Visual assets inventory (logo, favicon, images, icons, bg, video, pdf).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from webmaker.core.logging import get_logger

log = get_logger("acquisition.assets")

_LOGO_RE = re.compile(r"logo|brand|site[-_]?icon", re.I)
_ICON_RE = re.compile(r"icon|favicon|apple-touch", re.I)
_BG_RE = re.compile(r"url\(['\"]?([^)'\"]+)['\"]?\)", re.I)


def inventory_assets(data_dir: Path, package_dir: Path) -> dict[str, Any]:
    """Build assets.json from downloads + HTML heuristics."""
    data_dir = Path(data_dir)
    package_dir = Path(package_dir)

    images: list[dict[str, Any]] = []
    images_json = data_dir / "json" / "images.json"
    if images_json.is_file():
        try:
            raw = json.loads(images_json.read_text(encoding="utf-8"))
            items = raw.get("items", raw) if isinstance(raw, dict) else raw
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict):
                        images.append({
                            "filename": it.get("filename") or "",
                            "source_url": it.get("source_url") or "",
                            "local_path": it.get("local_path") or "",
                            "alt": it.get("alt_text") or it.get("alt") or "",
                            "width": it.get("width") or 0,
                            "height": it.get("height") or 0,
                        })
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("images.json unreadable: {e}", e=exc)

    images_dir = data_dir / "images"
    if images_dir.is_dir() and not images:
        for f in sorted(images_dir.iterdir()):
            if f.is_file():
                images.append({"filename": f.name, "local_path": str(f), "source_url": ""})

    logos: list[dict[str, Any]] = []
    favicons: list[dict[str, Any]] = []
    icons: list[dict[str, Any]] = []
    for img in images:
        name = str(img.get("filename") or img.get("source_url") or "").lower()
        if _LOGO_RE.search(name):
            logos.append(img)
        if _ICON_RE.search(name):
            icons.append(img)

    pdfs: list[str] = []
    videos: list[str] = []
    bg_images: list[str] = []
    assets_dir = data_dir / "assets"
    if assets_dir.is_dir():
        for f in assets_dir.iterdir():
            if not f.is_file():
                continue
            low = f.name.lower()
            if low.endswith(".pdf"):
                pdfs.append(str(f))
            elif low.endswith((".mp4", ".webm", ".mov")):
                videos.append(str(f))

    # Parse raw HTML for favicon / bg / video / pdf links
    raw_dir = data_dir / "raw"
    if raw_dir.is_dir():
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            BeautifulSoup = None  # type: ignore[misc, assignment]
        if BeautifulSoup is not None:
            for raw_path in raw_dir.glob("*.html"):
                try:
                    html = raw_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                soup = BeautifulSoup(html, "lxml")
                for link in soup.find_all("link"):
                    rel = " ".join(link.get("rel") or []).lower()
                    href = (link.get("href") or "").strip()
                    if href and ("icon" in rel or "shortcut" in rel):
                        favicons.append({"href": href, "rel": rel, "page": raw_path.stem})
                for tag in soup.find_all(style=True):
                    for m in _BG_RE.finditer(tag.get("style") or ""):
                        bg_images.append(m.group(1))
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if href.lower().endswith(".pdf"):
                        pdfs.append(href)
                for v in soup.find_all(["video", "source"]):
                    src = (v.get("src") or "").strip()
                    if src:
                        videos.append(src)

    # Deduplicate simple lists
    bg_images = list(dict.fromkeys(bg_images))[:40]
    pdfs = list(dict.fromkeys(pdfs))[:40]
    videos = list(dict.fromkeys(videos))[:40]

    # Prefer first logo-like header image if none matched
    if not logos and images:
        logos = [images[0]]

    payload = {
        "logo": logos[:5],
        "favicon": favicons[:10],
        "images": images,
        "icons": icons[:30],
        "background_images": bg_images,
        "videos": videos,
        "pdfs": pdfs,
        "counts": {
            "images": len(images),
            "logos": len(logos),
            "favicons": len(favicons),
            "icons": len(icons),
            "background_images": len(bg_images),
            "videos": len(videos),
            "pdfs": len(pdfs),
        },
    }
    out = package_dir / "assets.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(
        "Assets inventoried — images={i} logos={l} favicons={f}",
        i=len(images), l=len(logos), f=len(favicons),
    )
    return payload
