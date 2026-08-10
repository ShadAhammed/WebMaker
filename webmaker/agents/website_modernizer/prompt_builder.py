"""
webmaker.agents.website_modernizer.prompt_builder
=================================================
Build the Claude prompt context from the website_package produced by Agent 0.

Keeps the prompt concise: only the information Claude needs for intelligent
content mapping (no raw HTML, no full DOM trees).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_context(
    package_dir: Path,
    theme_id: str,
    template_id: str,
    theme_catalog_entry: dict[str, Any] | None,
) -> str:
    """Return a structured prompt context string for Claude.

    Reads: business.json, content.json, brand.json, navigation.json,
           sections.json, assets.json, validation_report.json.
    """
    package_dir = Path(package_dir)

    business = _load(package_dir / "business.json") or {}
    content   = _load(package_dir / "content.json") or {}
    brand     = _load(package_dir / "brand.json") or {}
    nav       = _load(package_dir / "navigation.json") or {}
    assets    = _load(package_dir / "assets.json") or {}

    parts: list[str] = []

    # ── Business ──────────────────────────────────────────────────────────────
    parts.append("## Business Profile")
    name   = business.get("name", "")
    phones = business.get("phones", [])
    emails = business.get("emails", [])
    addrs  = business.get("addresses", [])
    hours  = business.get("opening_hours", [])
    logo   = (business.get("logo") or {})

    if name:
        parts.append(f"Name: {name}")
    if phones:
        parts.append(f"Phone: {phones[0]}")
    if emails:
        parts.append(f"Email: {emails[0]}")
    if addrs:
        parts.append(f"Address: {addrs[0]}")
    if hours:
        # Extract the simplest opening hours string
        for h in hours:
            if len(h) < 120:
                parts.append(f"Hours: {h}")
                break
    logo_file = logo.get("filename") or logo.get("local_path", "")
    if logo_file:
        parts.append(f"Logo file: {Path(logo_file).name}")

    # ── Content (all pages) ───────────────────────────────────────────────────
    parts.append("\n## Content Inventory (all pages)")
    pages_data = content.get("pages", [])
    for pg in pages_data:
        slug  = pg.get("slug", "")
        title = pg.get("title", "")
        h1s   = pg.get("h1", [])
        h2s   = pg.get("h2", [])
        h3s   = pg.get("h3", [])
        paras = pg.get("paragraphs", [])
        btns  = pg.get("buttons", [])
        lists = pg.get("lists", [])
        faq   = pg.get("faq", [])
        imgs  = pg.get("images", [])

        parts.append(f"\n### Page: {slug} — {title}")
        if h1s:
            parts.append(f"H1: {' | '.join(str(h) for h in h1s[:5])}")
        if h2s:
            parts.append(f"H2: {' | '.join(str(h) for h in h2s[:6])}")
        if h3s:
            parts.append(f"H3: {' | '.join(str(h) for h in h3s[:4])}")
        if paras:
            for p in paras[:4]:
                if p and len(p) > 20:
                    parts.append(f"P: {str(p)[:220]}")
        if btns:
            clean = [b for b in btns if b and b.lower() not in (
                "akzeptieren", "ablehnen", "cookie", "alle akzeptieren",
                "nur notwendige akzeptieren", "speichern", "count", "price",
            )]
            if clean:
                parts.append(f"Buttons: {' | '.join(str(b) for b in clean[:6])}")
        if lists:
            for lst in lists[:2]:
                if isinstance(lst, list) and len(lst) > 1:
                    items = [str(x) for x in lst if str(x).strip() and len(str(x)) < 80]
                    if items:
                        parts.append(f"List: {' | '.join(items[:6])}")
        if faq:
            for q in faq[:5]:
                if isinstance(q, dict):
                    parts.append(f"FAQ Q: {q.get('question', '')}")
                elif isinstance(q, (list, tuple)) and len(q) >= 2:
                    parts.append(f"FAQ Q: {q[0]}")
                elif isinstance(q, str):
                    parts.append(f"FAQ: {q[:120]}")
        if imgs:
            # List image filenames (not full URLs) so Claude can reference them
            for im in imgs[:6]:
                src = im.get("src", "") if isinstance(im, dict) else str(im)
                fn  = Path(src.split("?")[0]).name if src else ""
                alt = (im.get("alt", "") if isinstance(im, dict) else "") or ""
                if fn:
                    parts.append(f"Image: {fn}" + (f' (alt: {alt})' if alt else ""))

    # ── Available local images ────────────────────────────────────────────────
    parts.append("\n## Available Local Images (downloaded)")
    parts.append(
        "IMPORTANT: For hero/about/gallery image fields, use ONLY these exact filenames. "
        "Never invent names like hero-team.jpg."
    )
    logo_entry = assets.get("logo") or []
    all_imgs   = assets.get("images", [])

    if isinstance(logo_entry, list):
        for lo in logo_entry[:1]:
            fn = Path((lo.get("local_path") or lo.get("filename") or "")).name
            if fn:
                parts.append(f"LOGO: {fn}")
    elif isinstance(logo_entry, dict):
        fn = Path((logo_entry.get("local_path") or logo_entry.get("filename") or "")).name
        if fn:
            parts.append(f"LOGO: {fn}")

    for im in all_imgs[:12]:
        fn   = Path(im.get("filename") or im.get("local_path", "")).name if isinstance(im, dict) else ""
        alt  = (im.get("alt", "") if isinstance(im, dict) else "") or ""
        if fn:
            parts.append(f"IMG: {fn}" + (f' — {alt}' if alt else ""))

    # ── Brand ─────────────────────────────────────────────────────────────────
    parts.append("\n## Brand")
    colors = brand.get("primary_colors", [])
    fonts  = brand.get("fonts", [])
    if colors:
        parts.append(f"Primary color: {colors[0]}")
    if fonts:
        parts.append(f"Font: {fonts[0]}")

    # ── Navigation ────────────────────────────────────────────────────────────
    nav_items = nav.get("items", nav.get("links", []))
    if nav_items:
        labels = [
            str(ni.get("label") or ni.get("text") or ni.get("title") or "")
            for ni in nav_items[:8]
            if isinstance(ni, dict)
        ]
        labels = [l for l in labels if l]
        if labels:
            parts.append(f"\n## Navigation: {' | '.join(labels)}")

    # ── Theme + Template ──────────────────────────────────────────────────────
    parts.append(f"\n## Selected Theme: {theme_id or 'kadence'}")
    if template_id:
        parts.append(f"Selected Template: {template_id}")
    if theme_catalog_entry:
        desc = theme_catalog_entry.get("description", "")
        if desc:
            parts.append(f"Theme description: {desc[:200]}")
        # Find matching template entry
        for tmpl in theme_catalog_entry.get("templates", []):
            if tmpl.get("id") == template_id:
                parts.append(f"Template name: {tmpl.get('name', '')}")
                tags = tmpl.get("tags", [])
                if tags:
                    parts.append(f"Template tags: {', '.join(tags)}")
                break

    return "\n".join(parts)


def _load(path: Path) -> dict | None:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return None
