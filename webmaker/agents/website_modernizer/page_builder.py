"""
webmaker.agents.website_modernizer.page_builder
===============================================
Convert the Claude content map + Design Blueprint into ``optimized_*.json``.

Layout variants (from blueprint ``layout_notes`` / ``layout_variant``) actually
change the HTML/CSS — not just the copy.
"""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path

from webmaker.utils.project_paths import project_path
from typing import Any

from webmaker.agents.website_modernizer.design_system import (
    DesignTokens,
    chrome_css,
    css_variables,
    icon,
    icon_for_label,
    load_design_tokens,
    premium_polish_css,
    site_footer_html,
    site_header_html,
)
from webmaker.agents.website_modernizer.image_bank import (
    ImageBank,
    load_image_bank,
    publish_local_for_wp,
)
from webmaker.agents.website_modernizer.inquiry_assistant import (
    inquiry_assistant_html,
)
from webmaker.agents.website_modernizer.services_guide import (
    collect_services_faq_items,
    render_services_section,
    services_guide_css,
)
from webmaker.core.logging import get_logger
from webmaker.core.schema import write_versioned_json

log = get_logger("modernizer.page_builder")

_STANDARD_PAGES = ("homepage", "services", "about", "contact", "faq")

_NAV_TITLES: dict[str, str] = {
    "homepage": "Startseite",
    "services": "Leistungen",
    "about":    "Über uns",
    "contact":  "Kontakt",
    "faq":      "FAQ",
}

# Blueprint section name → content-map section type(s)
_BP_TO_TYPES: dict[str, tuple[str, ...]] = {
    "hero": ("hero",),
    "services": ("services_grid",),
    "about": ("text_block",),
    "features": ("text_block", "trust_bar"),
    "process": ("process",),
    "gallery": ("gallery", "before_after"),
    "testimonials": ("reviews",),
    "faq": ("faq",),
    "cta": ("cta_banner",),
    "contact": ("contact_info",),
}


def build_pages(
    content_map: dict[str, Any],
    data_dir: Path,
    *,
    theme_id: str = "kadence",
    images_dir: Path | None = None,
    blueprint: dict[str, Any] | None = None,
) -> list[str]:
    """Write ``optimized_*.json`` applying blueprint layout variants + real images."""
    data_dir = Path(data_dir)
    json_dir = data_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    if images_dir is None:
        images_dir = data_dir / "images"

    package_dir = data_dir / "website_package"
    if not package_dir.is_dir():
        alt = data_dir.parent / "website_package"
        package_dir = alt if alt.is_dir() else package_dir
    bank = load_image_bank(data_dir, package_dir if package_dir.is_dir() else None)
    tokens = load_design_tokens(
        package_dir if package_dir.is_dir() else None,
        theme_id=theme_id,
    )
    # Prefer logo from image bank CDN when available
    if not tokens.logo_src:
        logo = bank.pick(role="logo", avoid_used=False)
        if logo:
            tokens.logo_src = logo.src

    css = _global_css(tokens)

    pages_map: dict[str, Any] = content_map.get("pages", {})
    written: list[str] = []

    for slug in _STANDARD_PAGES:
        page_data = pages_map.get(slug) or {}
        sections = list(page_data.get("sections") or [])
        sections = enrich_sections(sections, blueprint=blueprint, bank=bank, page_slug=slug)
        if slug == "homepage":
            sections = _ensure_homepage_trust_bar(sections)

        title = page_data.get("title") or _NAV_TITLES.get(slug, slug.title())
        meta_desc = page_data.get("meta_description") or ""

        hero_heading = ""
        for sec in sections:
            if sec.get("type") == "hero":
                hero_heading = sec.get("heading", "")
                break

        if page_data.get("meta_title"):
            meta_title = str(page_data.get("meta_title"))
        elif slug == "homepage" and hero_heading:
            meta_title = f"{hero_heading} | {tokens.short_name}"
        else:
            meta_title = title

        faq_items = _collect_faq_items(pages_map)
        if slug == "services":
            page_faq = collect_services_faq_items(page_data)
            faq_items = page_faq or faq_items
        body_html = _render_page(
            sections,
            css,
            tokens=tokens,
            bank=bank,
            include_chrome=True,
            page_slug=slug,
            faq_items=faq_items if slug in ("homepage", "faq", "services") else None,
        )

        payload = {
            "slug": slug,
            "title": title,
            "nav_title": _NAV_TITLES.get(slug, title),
            "body_html": body_html,
            "meta_title": str(meta_title)[:70],
            "meta_description": meta_desc[:160],
            "headings": [hero_heading or title] if (hero_heading or title) else [],
            "layout_sections": [
                f"{s.get('type', '')}:{s.get('layout_variant', 'default')}" for s in sections
            ],
            "hero": {
                "heading": hero_heading or title,
                "subheading": meta_desc[:120],
                "cta_primary": "",
            },
            "design_system": {
                "font_display": tokens.font_display,
                "font_body": tokens.font_body,
                "accent": tokens.accent,
                "company": tokens.short_name,
            },
        }
        out = json_dir / f"optimized_{slug}.json"
        write_versioned_json(out, payload)
        written.append(slug)
        log.info(
            "Built page → {f}  layouts={L}  body_len={n}",
            f=out.name,
            L=[s.get("layout_variant") for s in sections],
            n=len(body_html),
        )

    # Persist tokens for WP branding step
    try:
        (json_dir / "design_tokens.json").write_text(
            json.dumps({
                "company_name": tokens.company_name,
                "short_name": tokens.short_name,
                "phone": tokens.phone,
                "email": tokens.email,
                "accent": tokens.accent,
                "font_display": tokens.font_display,
                "font_body": tokens.font_body,
                "font_url": tokens.font_url,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass

    return written


# ── Blueprint enrichment ──────────────────────────────────────────────────────

def enrich_sections(
    sections: list[dict],
    *,
    blueprint: dict[str, Any] | None,
    bank: ImageBank,
    page_slug: str,
) -> list[dict]:
    """Apply layout_variant + real images from blueprint / image bank."""
    bp_by_section = _index_blueprint(blueprint)
    out: list[dict] = []

    for sec in sections:
        s = dict(sec)
        stype = (s.get("type") or "").lower()

        # Match blueprint entry
        bp = _match_blueprint(stype, bp_by_section, page_slug=page_slug)
        if bp:
            s.setdefault("design_ref", bp.get("reference", ""))
            s.setdefault("layout_notes", bp.get("layout_notes", ""))
            if not s.get("layout_variant"):
                s["layout_variant"] = infer_variant(
                    stype,
                    notes=str(bp.get("layout_notes") or ""),
                    ref=str(bp.get("reference") or ""),
                )
            # Prefer blueprint client text when section fields empty
            cc = bp.get("client_content") or {}
            if isinstance(cc, dict):
                if not s.get("heading") and cc.get("heading"):
                    s["heading"] = cc["heading"]
                if not s.get("subheading") and cc.get("subheading"):
                    s["subheading"] = cc["subheading"]
                if stype == "text_block" and not s.get("text") and cc.get("body"):
                    s["text"] = cc["body"]
                if stype == "hero" and not s.get("cta_label") and cc.get("cta"):
                    s["cta_label"] = cc["cta"]

        if not s.get("layout_variant"):
            s["layout_variant"] = infer_variant(
                stype,
                notes=str(s.get("layout_notes") or ""),
                ref=str(s.get("design_ref") or ""),
            )

        # Bind real images
        s = _bind_images(s, bank)
        out.append(s)

    return out


def infer_variant(section_type: str, *, notes: str = "", ref: str = "") -> str:
    """Map blueprint language → concrete renderer variant."""
    blob = f"{notes} {ref}".lower()
    t = section_type.lower()

    if t == "hero":
        if any(k in blob for k in ("split", "50/50", "text left", "image right", "mrhandyman", "neat")):
            return "split"
        if any(k in blob for k in (
            "full-bleed", "full bleed", "floating", "overlay", "floated",
            "trust badge", "background image", "action photo", "1800gotjunk",
        )):
            return "overlay_card"
        return "split"  # brighter default for local-service demos

    if t == "services_grid":
        if any(k in blob for k in (
            "photo", "image card", "image-led", "photography",
            "leistung", "mehr erfahren", "gradient overlay",
        )):
            return "photo_cards"
        if any(k in blob for k in (
            "no cards", "no borders", "icon column", "icon grid",
            "illustrated icon", "whitespace", "equal column",
        )):
            return "icon_columns"
        return "cards"

    if t in ("text_block",):
        if any(k in blob for k in (
            "why choose", "feature row", "stacked", "icon badge", "arms crossed",
        )):
            return "feature_stack"
        if any(k in blob for k in (
            "rounded", "50/50", "split", "gradient", "inner white", "soft gradient",
        )):
            return "split_rounded"
        return "split_rounded"

    if t == "reviews":
        return "band_cards"

    if t == "cta_banner":
        return "band"

    if t == "process":
        return "numbered"

    if t == "gallery":
        return "grid"

    if t == "before_after":
        return "transformations"

    if t == "trust_bar":
        return "stats"

    if t == "contact_info":
        return "split_info"

    if t == "faq":
        return "accordion_list"

    return "default"


def _index_blueprint(blueprint: dict[str, Any] | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not blueprint:
        return out
    for sec in blueprint.get("sections") or []:
        if isinstance(sec, dict) and sec.get("section"):
            out[str(sec["section"]).lower()] = sec
    return out


def _match_blueprint(
    stype: str,
    bp_by_section: dict[str, dict],
    *,
    page_slug: str,
) -> dict | None:
    for bp_name, types in _BP_TO_TYPES.items():
        if stype in types and bp_name in bp_by_section:
            bp = bp_by_section[bp_name]
            # On homepage, prefer included sections; on about prefer about bp
            if page_slug == "about" and bp_name == "about":
                return bp
            if page_slug == "services" and bp_name == "services":
                return bp
            if stype == "hero" and bp_name == "hero":
                return bp
            if stype in types:
                return bp
    # Fallback: first matching type
    for bp_name, types in _BP_TO_TYPES.items():
        if stype in types and bp_name in bp_by_section:
            return bp_by_section[bp_name]
    return None


def _bind_images(sec: dict, bank: ImageBank) -> dict:
    stype = (sec.get("type") or "").lower()
    variant = (sec.get("layout_variant") or "").lower()

    if stype == "hero":
        raw = sec.get("image") or ""
        src = bank.resolve_src(raw) if raw else ""
        # Avoid gloomy prop stills (dustpan / dark wall) for hero
        if src and _is_weak_hero_image(src, raw):
            src = ""
        if not src:
            a = bank.pick(
                role="photo",
                avoid_used=False,
                avoid_names=("print-3", "dustpan", "feedback-g9b", "logo", "1.png"),
                prefer_bright=True,
            )
            src = _browser_src(a)
        if not src:
            a = bank.pick(role="photo", avoid_names=("logo", "1.png"))
            src = _browser_src(a)
        sec["image"] = src

    elif stype in ("text_block",):
        raw = sec.get("image") or ""
        src = bank.resolve_src(raw) if raw else ""
        if variant == "feature_stack":
            # Prefer the curated Warum-uns worker crop over stock photos.
            if not src or "pexels" in (raw or src).lower() or "jan-kop" in (raw or src).lower():
                for cand in (
                    project_path("images", "warum-uns-worker.png"),
                ):
                    if cand.is_file():
                        published = publish_local_for_wp(cand)
                        if published:
                            src = published
                            break
        if not src:
            a = bank.pick(role="photo")
            src = _browser_src(a)
        sec["image"] = src
        if variant == "feature_stack" and not sec.get("items"):
            # leave items as-is; renderer can use text
            pass

    elif stype == "gallery":
        imgs = sec.get("images") or []
        resolved: list[str] = []
        for ref in imgs:
            src = bank.resolve_src(str(ref))
            if src:
                resolved.append(src)
        if len(resolved) < 3:
            for a in bank.pick_many(6 - len(resolved)):
                src = _browser_src(a)
                if src and src not in resolved:
                    resolved.append(src)
        sec["images"] = resolved[:9]

    elif stype == "before_after":
        variant = (sec.get("layout_variant") or "").lower()
        if variant == "magic_wipe":
            # Resolve each card's before/after images
            cards = list(sec.get("cards") or [])
            for card in cards:
                if not isinstance(card, dict):
                    continue
                for key in ("before_image", "after_image"):
                    raw = (card.get(key) or "").strip()
                    src = bank.resolve_src(raw) if raw else ""
                    card[key.replace("_image", "_src")] = src
            sec["cards"] = cards
            video_name = Path(
                (sec.get("worker_video") or "cat-3-sheet.png").strip()
            ).name
            # Prefer canvas spritesheet (no WebP frame-ghosting)
            names = [video_name]
            for alt in (
                "cat-3-sheet.png",
                "cat-3-alpha.webp",
                "Cat 3.mp4",
                "cat-3.mp4",
            ):
                if alt not in names:
                    names.append(alt)
            video_src = ""
            worker_root = None
            for name in names:
                video_candidates = [
                    project_path("images", "Worker") / name,
                ]
                for cand in video_candidates:
                    if cand.is_file():
                        video_src = publish_local_for_wp(cand)
                        if video_src:
                            worker_root = cand.parent
                            break
                if video_src:
                    break
            if not video_src:
                video_src = bank.resolve_src(video_name)
            sheet_meta = {
                "frames": 100,
                "cols": 10,
                "fps": 10,
                "fw": 202,
                "fh": 360,
            }
            if worker_root is not None:
                meta_path = worker_root / "cat-3-sheet.json"
                if meta_path.is_file():
                    try:
                        sheet_meta.update(json.loads(meta_path.read_text(encoding="utf-8")))
                    except Exception:
                        pass
            poster_src = ""
            sec["worker_video_src"] = video_src
            sec["worker_sheet_meta"] = sheet_meta
            sec["worker_poster_src"] = poster_src
            sec["worker_src"] = video_src
            sec["worker_frames"] = []
            sec["worker_anim_src"] = ""
            sec["worker_still_src"] = poster_src
        else:
            # Illustration cards only — never the full Sehen mockup with baked-in CTAs.
            raw = (sec.get("image") or "ba-cards-only.png").strip()
            src = bank.resolve_src(str(raw)) if raw else ""
            if not src:
                for prefer in ("ba-cards-only", "ba-grid-full", "Sehen-Sie-Selbst"):
                    a = bank.pick(prefer=prefer, role="photo", avoid_used=False)
                    src = _browser_src(a)
                    if src:
                        break
            sec["image"] = src
            items = list(sec.get("items") or [])
            for it in items:
                if not isinstance(it, dict):
                    continue
                iraw = it.get("image") or ""
                isrc = bank.resolve_src(str(iraw)) if iraw else ""
                it["image"] = isrc
            sec["items"] = items

    elif stype == "services_grid":
        items = list(sec.get("items") or [])
        for it in items:
            if not isinstance(it, dict):
                continue
            raw = it.get("image") or ""
            src = bank.resolve_src(str(raw)) if raw else ""
            if not src:
                # Prefer semantically named svc-* assets, then any photo
                prefer = _service_image_hint(str(it.get("title") or ""))
                a = bank.pick(prefer=prefer, role="photo", avoid_used=True) if prefer else None
                if a is None:
                    a = bank.pick(role="photo", avoid_used=True, avoid_names=("logo", "1.png", "feedback"))
                src = _browser_src(a)
            it["image"] = src
        sec["items"] = items

    elif stype == "process":
        # One complete illustration strip + HTML captions (never split into crops).
        raw = (sec.get("image") or "schritt-art-only.png").strip()
        src = bank.resolve_src(raw)
        if not src:
            for prefer in ("schritt-art-only", "schritt-process-visual", "Schritt-Homepage"):
                a = bank.pick(prefer=prefer, role="photo", avoid_used=False)
                src = _browser_src(a)
                if src:
                    break
        sec["image"] = src
        steps = list(sec.get("steps") or [])
        for step in steps:
            if isinstance(step, dict):
                step.pop("image", None)
        sec["steps"] = steps

    return sec


def _process_image_hint(step: dict) -> str:
    """Legacy hint kept for callers; process section uses one strip artwork."""
    title = str(step.get("title") or "").lower()
    try:
        num = int(step.get("step") or 0)
    except (TypeError, ValueError):
        num = 0
    if num == 1 or "besicht" in title or "anfahrt" in title:
        return "schritt-step-01"
    if num == 2 or "angebot" in title:
        return "schritt-step-02"
    if num == 3 or "entrümp" in title or "entruemp" in title:
        return "schritt-step-03"
    if num:
        return f"schritt-step-{num:02d}"
    return ""


def _service_image_hint(title: str) -> str:
    low = (title or "").lower()
    if "keller" in low:
        return "svc-keller"
    if "wohnung" in low:
        return "svc-wohnung"
    if "haushalt" in low:
        return "svc-haushalt"
    if "gewerbe" in low or "büro" in low or "buero" in low:
        return "svc-gewerbe"
    if "sperr" in low or "müll" in low or "mull" in low:
        return "svc-sperrmuell"
    if "besen" in low or "übergabe" in low or "uebergabe" in low:
        return "svc-besenrein"
    return ""


# ── Page renderer ─────────────────────────────────────────────────────────────

def _render_page(
    sections: list[dict],
    css: str,
    *,
    tokens: DesignTokens,
    bank: ImageBank,
    include_chrome: bool = True,
    page_slug: str = "",
    faq_items: list[dict] | None = None,
) -> str:
    if not sections and not include_chrome:
        return ""
    parts = [css]
    if include_chrome:
        parts.append(site_header_html(tokens))
    for sec in sections:
        html = _render_section(sec, tokens=tokens, bank=bank)
        if html.strip():
            parts.append(html)
    if include_chrome:
        parts.append(site_footer_html(tokens))
    schema = _seo_schema_html(tokens, page_slug=page_slug, faq_items=faq_items)
    if schema:
        parts.append(schema)
    # Local FAQ inquiry assistant — all pages (no external AI)
    if include_chrome:
        assistant = inquiry_assistant_html()
        if assistant.strip():
            parts.append(assistant)
    return "\n\n".join(p for p in parts if p.strip())


def _ensure_homepage_trust_bar(sections: list[dict]) -> list[dict]:
    """Insert a dedicated trust strip directly under the hero."""
    if any((s.get("type") or "").lower() == "trust_bar" for s in sections):
        return sections
    trust = {
        "type": "trust_bar",
        "heading": "Warum Kunden uns vertrauen",
        "label": "Vertrauen",
        "subheading": (
            "Von der kostenlosen Besichtigung bis zur besenreinen Übergabe "
            "begleiten wir Privat- und Gewerbekunden zuverlässig in Siegen und Umgebung."
        ),
        "items": [
            {"icon": "check", "value": "500+", "label": "abgeschlossene Projekte"},
            {
                "icon": "calendar",
                "value": "Kostenlose Besichtigung",
                "label": "in Siegen & Umgebung",
            },
            {
                "icon": "shield",
                "value": "Transparenter Festpreis",
                "label": "ohne Nachkalkulation",
            },
            {
                "icon": "leaf",
                "value": "Umweltgerechte Entsorgung",
                "label": "Recycling mit Verantwortung",
            },
            {
                "icon": "pin",
                "value": "Regional in Siegen",
                "label": "persönlich vor Ort",
            },
            {
                "icon": "clock",
                "value": "Schnelle Termine",
                "label": "kurze Reaktionszeiten",
            },
        ],
    }
    out: list[dict] = []
    inserted = False
    for sec in sections:
        out.append(sec)
        if not inserted and (sec.get("type") or "").lower() == "hero":
            out.append(trust)
            inserted = True
    if not inserted:
        out.insert(0, trust)
    return out


def _collect_faq_items(pages_map: dict[str, Any]) -> list[dict]:
    faq_page = pages_map.get("faq") or {}
    items: list[dict] = []
    for sec in faq_page.get("sections") or []:
        if (sec.get("type") or "").lower() != "faq":
            continue
        for it in sec.get("items") or sec.get("faqs") or []:
            if not isinstance(it, dict):
                continue
            q = str(it.get("question") or it.get("title") or "").strip()
            a = str(it.get("answer") or it.get("text") or it.get("description") or "").strip()
            if q and a:
                items.append({"question": q, "answer": a})
    return items


def _seo_schema_html(
    tokens: DesignTokens,
    *,
    page_slug: str = "",
    faq_items: list[dict] | None = None,
) -> str:
    """LocalBusiness (+ optional FAQPage) JSON-LD for Google visibility."""
    graphs: list[dict[str, Any]] = []
    phone = (tokens.phone or "").strip()
    email = (tokens.email or "").strip()
    address = (tokens.address or "").strip()
    postal = ""
    m = re.search(r"(\d{5})", address)
    if m:
        postal = m.group(1)
    # Prefer structured fields from tokens; never hardcode a client identity.
    street = (getattr(tokens, "street", None) or "").strip()
    if not street and address:
        street = address.split(",")[0].strip()
    locality = (getattr(tokens, "city", None) or "").strip()
    site_url = (getattr(tokens, "site_url", None) or "").strip()
    local: dict[str, Any] = {
        "@type": "LocalBusiness",
        "@id": f"{site_url.rstrip('/')}/#business" if site_url else "#business",
        "name": tokens.company_name or "",
        "image": tokens.logo_src or "",
        "url": site_url or "",
        "telephone": phone,
        "email": email,
        "priceRange": "€€",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": street,
            "addressLocality": locality,
            "postalCode": postal,
            "addressCountry": "DE",
        },
        "openingHoursSpecification": {
            "@type": "OpeningHoursSpecification",
            "dayOfWeek": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            "opens": "08:00",
            "closes": "20:00",
        },
        "description": (getattr(tokens, "description", None) or "").strip(),
    }
    if not local["description"]:
        local.pop("description", None)
    if not local["image"]:
        local.pop("image", None)
    if not phone:
        local.pop("telephone", None)
    if not email:
        local.pop("email", None)
    if not local["name"]:
        local.pop("name", None)
    if not local["url"]:
        local.pop("url", None)
    addr = local.get("address") or {}
    if not any(addr.get(k) for k in ("streetAddress", "addressLocality", "postalCode")):
        local.pop("address", None)
    graphs.append(local)

    if faq_items and page_slug in ("homepage", "faq", "services", ""):
        graphs.append(
            {
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": it["question"],
                        "acceptedAnswer": {
                            "@type": "Answer",
                            "text": it["answer"],
                        },
                    }
                    for it in faq_items[:12]
                ],
            }
        )

    payload = {
        "@context": "https://schema.org",
        "@graph": graphs,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Prevent script-breakers in HTML embedding
    raw = raw.replace("</", "<\\/")
    return (
        "<!-- wp:html -->\n"
        f'<script type="application/ld+json">{raw}</script>\n'
        "<!-- /wp:html -->"
    )


def _render_section(sec: dict, *, tokens: DesignTokens, bank: ImageBank) -> str:
    t = (sec.get("type") or "text_block").lower()
    v = (sec.get("layout_variant") or "default").lower()

    # Leistungen guide (must not fall through to homepage layouts)
    guided = render_services_section(sec)
    if guided is not None:
        return guided

    if t == "hero":
        if v == "split":
            return _hero_split(sec)
        # overlay_card / full_bleed / default → cinematic full-bleed
        return _hero_overlay_card(sec)
    if t == "services_grid":
        if v == "photo_cards":
            return _services_photo_cards(sec)
        if v == "icon_columns":
            return _services_icon_columns(sec)
        return _services_cards(sec)
    if t == "process":
        return _section_process(sec)
    if t == "before_after":
        if v == "magic_wipe":
            return _section_before_after_magic(sec)
        return _section_before_after(sec)
    if t == "text_block":
        if v == "feature_stack":
            return _feature_stack(sec)
        return _split_rounded(sec)
    if t == "trust_bar":
        return _section_trust_bar(sec)
    if t == "gallery":
        return _section_gallery(sec)
    if t == "cta_banner":
        return _section_cta(sec)
    if t == "reviews":
        return _section_reviews(sec)
    if t == "contact_info":
        return _section_contact(sec)
    if t == "faq":
        return _section_faq(sec)
    return _split_rounded(sec)


# ── Hero variants ─────────────────────────────────────────────────────────────

def _browser_src(a) -> str:
    """Only emit URLs the WordPress browser can load (no raw disk paths)."""
    if a is None:
        return ""
    if (a.src or "").startswith(("http://", "https://")):
        return a.src
    if (a.source_url or "").startswith(("http://", "https://")):
        return a.source_url
    local = a.local_path or ""
    if local:
        from webmaker.agents.website_modernizer.image_bank import publish_local_for_wp
        return publish_local_for_wp(local)
    return ""


def _is_weak_hero_image(src: str, raw: str = "") -> bool:
    blob = f"{src} {raw}".lower()
    return any(k in blob for k in ("print-3", "dustpan", "feedback-g9b", "logo", "/1.png", "1_1.png"))


def _clean_heading(text: str) -> str:
    """Keep H1 free of glued CTA phrases (KostenJetzt…)."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    t = re.sub(
        r"(Kosten|Festpreis|Siegen)(Jetzt|jetzt)\b",
        r"\1",
        t,
    )
    t = re.sub(
        r"\s*(Jetzt\s+(kostenlos\s+)?(anfragen|anrufen).*)\s*$",
        "",
        t,
        flags=re.I,
    )
    return t.strip(" –-") or text.strip()


def _single_hero_cta(sec: dict) -> tuple[str, str]:
    """Return (label, href) for one primary CTA — never a twin phone ghost."""
    phone = (sec.get("phone") or "").strip()
    cta_label = (sec.get("cta_label") or "").strip()
    cta_url = (sec.get("cta_url") or "").strip()
    tel = ""
    if phone:
        tel = "tel:" + re.sub(r"[^\d+]", "", phone)
    # Prefer short action label; phone number alone → "Jetzt anrufen"
    label_is_phone = bool(
        phone and cta_label and re.sub(r"\D", "", cta_label) == re.sub(r"\D", "", phone)
    )
    if tel and (not cta_label or label_is_phone or "whatsapp" in cta_label.lower()
                or "telefon" in cta_label.lower()):
        return "Jetzt anrufen", tel
    if cta_label and cta_url:
        return cta_label, cta_url
    if cta_label and tel:
        return cta_label, tel
    if tel:
        return "Jetzt anrufen", tel
    if cta_label:
        return cta_label, cta_url or "#kontakt"
    return "", ""


def _hero_overlay_card(sec: dict) -> str:
    """Full-bleed photo hero with on-image copy (premium local-service pattern)."""
    heading = _clean_heading(sec.get("heading", ""))
    subheading = sec.get("subheading", "")
    image = sec.get("image") or ""
    cta_label, cta_href = _single_hero_cta(sec)

    _TRUST_BADGES = [
        "Kostenlose Besichtigung",
        "Transparente Festpreise",
        "Besenreine Übergabe",
        "Kurzfristige Termine",
    ]

    bits: list[str] = []
    if heading:
        bits.append(f'<h1 class="wm3-hero-bleed__title">{escape(heading)}</h1>')
    if subheading:
        bits.append(f'<p class="wm3-hero-bleed__sub">{escape(subheading)}</p>')
    if cta_label and cta_href:
        bits.append(
            '<div class="wm3-hero-bleed__actions">'
            f'<a class="wm3-btn wm3-btn--hero-call" href="{escape(cta_href, quote=True)}">'
            f'{icon("phone", 18)}'
            f"<span>{escape(cta_label)}</span></a>"
            '<a class="wm3-hero-bleed__softlink" href="/contact/">'
            '<span class="wm3-hero-bleed__softlink-ico" aria-hidden="true">📅</span>'
            "Kostenlose Besichtigung vereinbaren</a>"
            "</div>"
        )

    badges_html = (
        '<ul class="wm3-hero-bleed__badges" aria-label="Ihre Vorteile">'
        + "".join(
            f'<li>'
            f'<span class="wm3-badge-check" aria-hidden="true">&#10003;</span>'
            f'{escape(b)}</li>'
            for b in _TRUST_BADGES
        )
        + "</ul>"
    )
    bits.append(badges_html)

    bg_style = (
        f'style="background-image:url(\'{escape(image, quote=True)}\')"'
        if image
        else ""
    )
    aria = f'aria-label="{escape(heading or "Entrümpelung Siegen", quote=True)}"'
    html = (
        f'<section class="wm3-hero-overlay wm3-hero-bleed" {aria} {bg_style}>\n'
        '  <div class="wm3-hero-bleed__shade" aria-hidden="true"></div>\n'
        '  <div class="wm3-hero-bleed__vignette" aria-hidden="true"></div>\n'
        '  <div class="wm3-hero-bleed__inner">\n'
        + "\n".join(f"    {b}" for b in bits)
        + "\n  </div>\n"
        "</section>"
    )
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def _hero_split(sec: dict) -> str:
    """Navy copy panel + bright photo (Mr Handyman / Neat style)."""
    heading = _clean_heading(sec.get("heading", ""))
    subheading = sec.get("subheading", "")
    trust = sec.get("trust_line") or sec.get("micro_trust") or (
        "Kostenlose Besichtigung · Transparente Festpreise"
    )
    image = sec.get("image") or ""
    cta_label, cta_href = _single_hero_cta(sec)
    phone = (sec.get("phone") or "").strip()

    copy_bits: list[str] = []
    if heading:
        copy_bits.append(f'<h1 class="wm3-hero-split__title">{escape(heading)}</h1>')
    if subheading:
        copy_bits.append(f'<p class="wm3-hero-split__sub">{escape(subheading)}</p>')
    if cta_label and cta_href:
        copy_bits.append(
            '<div class="wm3-hero-split__actions">'
            f'<a class="wm3-btn wm3-btn--primary" href="{escape(cta_href, quote=True)}">'
            f"{escape(cta_label)}</a>"
            + (
                f'<span class="wm3-hero-split__phone">{escape(phone)}</span>'
                if phone and "anrufen" in cta_label.lower()
                else ""
            )
            + "</div>"
        )
    if trust:
        copy_bits.append(f'<p class="wm3-hero-split__trust">{escape(trust)}</p>')

    media = ""
    if image:
        media = (
            f'<div class="wm3-hero-split__media">'
            f'<img src="{escape(image, quote=True)}" '
            f'alt="{escape(heading or "Entrümpelung Siegen")}" loading="eager" /></div>'
        )
    else:
        media = '<div class="wm3-hero-split__media wm3-hero-split__media--empty"></div>'

    html = (
        '<section class="wm3-hero-split">\n'
        '  <div class="wm3-hero-split__inner">\n'
        '    <div class="wm3-hero-split__copy">\n'
        + "\n".join(f"      {b}" for b in copy_bits)
        + "\n    </div>\n"
        f"    {media}\n"
        "  </div>\n"
        "</section>"
    )
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


# ── Services variants ─────────────────────────────────────────────────────────

def _atmosphere_bg_url(*, version: str = "band1", preferred: tuple[str, ...] = ()) -> str:
    """Publish atmospheric plate for trust-like section washes."""
    root = project_path("images")
    names = tuple(preferred) + (
        "vertrauen-bg.jpg",
        "BG-Homepage.png",
        "Vertrauen-Homepage.png",
    )
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        candidate = root / name
        if candidate.is_file():
            published = publish_local_for_wp(candidate)
            if published:
                return f"{published}?v={escape(version, quote=True)}"
    return ""


def _services_photo_cards(sec: dict) -> str:
    """Image-led 3×2 service grid + benefits strip (Leistung-homepage pattern)."""
    title = (
        (sec.get("heading") or "").strip()
        or (sec.get("label") or "").strip()
        or "Unsere Leistungen"
    )
    label = (sec.get("label") or "").strip()
    # Orange eyebrow above H2 — never duplicate the same string as both
    if not label or label.lower() == title.lower():
        label = "Leistungen"
    # No lede under this title — photo cards carry the message; a line of filler weakens the header.
    items = sec.get("items") or []
    benefits = sec.get("benefits") or []

    head_bits: list[str] = [
        '<header class="wm3-svc-photo__head">',
        f'<p class="wm3-svc-photo__eyebrow">{escape(label)}</p>',
        f'<h2 id="wm3-svc-photo-heading" class="wm3-svc-photo__title">{escape(title)}</h2>',
        "</header>",
    ]

    cards: list[str] = []
    used_icons: set[str] = set()
    for idx, it in enumerate(items):
        card_title = (it.get("title") or "").strip()
        desc = _clean_inline_text(it.get("description") or "")
        img = (it.get("image") or "").strip()
        alt = (it.get("alt") or card_title or "Leistung").strip()
        href = str(it.get("href") or it.get("link") or it.get("url") or "").strip()
        if not href and card_title:
            href = f"/services/#{_slugify(card_title)}"
        icon_key = _unique_service_icon(card_title, index=idx, used=used_icons)
        used_icons.add(icon_key)
        svg = icon(icon_key, 28)
        img_html = (
            f'<img class="wm3-svc-card__img" src="{escape(img, quote=True)}" '
            f'alt="{escape(alt)}" loading="lazy" width="1200" height="800" />'
            if img else
            '<div class="wm3-svc-card__img wm3-svc-card__img--empty" aria-hidden="true"></div>'
        )
        body = (
            f'<div class="wm3-svc-card__media">{img_html}</div>'
            f'<div class="wm3-svc-card__body">'
            f'<div class="wm3-svc-card__icon" aria-hidden="true">{svg}</div>'
            f'<div class="wm3-svc-card__copy">'
            + (f'<h3 class="wm3-svc-card__title">{escape(card_title)}</h3>' if card_title else "")
            + (f'<p class="wm3-svc-card__desc">{escape(desc)}</p>' if desc else "")
            + "</div></div>"
        )
        slug_id = escape(_slugify(card_title))
        if href:
            cards.append(
                f'<a class="wm3-svc-card" href="{escape(href, quote=True)}" '
                f'id="{slug_id}">'
                f"{body}</a>"
            )
        else:
            cards.append(
                f'<article class="wm3-svc-card" id="{slug_id}">'
                f"{body}</article>"
            )

    grid = '<div class="wm3-svc-photo__grid">' + "".join(cards) + "</div>"

    benefits_html = ""
    if benefits:
        cells: list[str] = []
        for bi, b in enumerate(benefits):
            if not isinstance(b, dict):
                continue
            bt = (b.get("title") or "").strip()
            # Titles only — body copy under these claims reads weak next to photo cards.
            bicon = (b.get("icon") or "").strip()
            svg = icon(bicon, 20) if bicon else icon_for_label(bt, index=bi, size=20)
            cells.append(
                '<div class="wm3-svc-benefit">'
                f'<div class="wm3-svc-benefit__icon" aria-hidden="true">{svg}</div>'
                + (f'<strong class="wm3-svc-benefit__title">{escape(bt)}</strong>' if bt else "")
                + "</div>"
            )
        if cells:
            benefits_html = (
                '<div class="wm3-svc-photo__benefits" role="list">'
                + "".join(f'<div role="listitem">{c}</div>' for c in cells)
                + "</div>"
            )

    html = (
        '<section class="wm3-section wm3-svc-photo" aria-labelledby="wm3-svc-photo-heading">'
        '<div class="wm3-svc-photo__inner">'
        + "".join(head_bits)
        + grid
        + benefits_html
        + "</div></section>"
    )
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


_SERVICE_ICON_BY_KEYWORD: tuple[tuple[tuple[str, ...], str], ...] = (
    (("keller", "dach", "speicher"), "box"),
    (("wohnung",), "home"),
    (("haushalt",), "door"),
    (("gewerb", "büro", "buero", "office", "lager"), "building"),
    (("sperrmüll", "sperrmull", "müll", "muell"), "truck"),
    (("besenrein", "übergabe", "uebergabe", "sauber"), "spark"),
    (("entsorg",), "recycle"),
)


def _unique_service_icon(title: str, *, index: int, used: set[str]) -> str:
    """Pick a distinct icon per service card (Wohnung ≠ Haushalt)."""
    low = (title or "").lower()
    preferred = ""
    for keys, name in _SERVICE_ICON_BY_KEYWORD:
        if any(k in low for k in keys):
            preferred = name
            break
    cycle = (
        "box", "home", "door", "building", "truck", "spark",
        "recycle", "shield", "check", "leaf", "clock", "users",
    )
    if preferred and preferred not in used:
        return preferred
    for name in cycle:
        if name not in used:
            return name
    return cycle[index % len(cycle)]


def _clean_inline_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _shorten_desc(text: str, max_len: int = 78) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) <= max_len:
        return t
    cut = t[: max_len - 1].rsplit(" ", 1)[0].rstrip(" ,.;:–-")
    return (cut or t[: max_len - 1]) + "…"


def _slugify(text: str) -> str:
    t = (text or "").lower().strip()
    repl = (
        ("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"),
    )
    for a, b in repl:
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t or "leistung"


def _services_icon_columns(sec: dict) -> str:
    """Light background, icon columns, no heavy cards (1800gotjunk-ish)."""
    heading = sec.get("heading", "")
    items = sec.get("items") or []
    bits: list[str] = []
    if heading:
        bits.append(f'<h2 class="wm3-section-title">{escape(heading)}</h2>')

    cols = max(2, min(3, len(items) or 3))
    cells: list[str] = []
    for idx, it in enumerate(items):
        title = it.get("title", "")
        desc = it.get("description", "")
        svg = icon_for_label(title, index=idx, size=36)
        cells.append(
            '<div class="wm3-icon-col">'
            f'<div class="wm3-icon-col__icon">{svg}</div>'
            + (f'<h3 class="wm3-icon-col__title">{escape(title)}</h3>' if title else "")
            + (f'<p class="wm3-icon-col__desc">{escape(desc)}</p>' if desc else "")
            + "</div>"
        )

    # chunk into rows of cols
    rows = []
    for i in range(0, len(cells), cols):
        chunk = cells[i : i + cols]
        width = f"{100 // max(len(chunk), 1)}%"
        col_parts = [(f"<!-- wp:html -->\n{c}\n<!-- /wp:html -->", width) for c in chunk]
        rows.append(_columns(col_parts, cls="wm3-icon-row"))
    bits_html = "\n".join(
        ([_h2(heading, "wm3-section-title")] if heading else [])
        + rows
    )
    return _group(bits_html, cls="wm3-section wm3-services-icons")


def _services_cards(sec: dict) -> str:
    heading = sec.get("heading", "")
    items = sec.get("items") or []
    parts: list[str] = []
    if heading:
        parts.append(_h2(heading, "wm3-section-title"))
    if not items:
        return _group("\n".join(parts), cls="wm3-section wm3-services-cards") if parts else ""

    cols = max(2, min(3, len(items)))
    width = f"{100 // cols}%"
    card_parts: list[tuple[str, str]] = []
    for idx, it in enumerate(items):
        title = it.get("title", "")
        desc = it.get("description", "")
        svg = icon_for_label(title, index=idx, size=32)
        card = (
            '<div class="wm3-card">'
            f'<div class="wm3-card__icon">{svg}</div>'
            + (f'<h3 class="wm3-card__title">{escape(title)}</h3>' if title else "")
            + (f'<p class="wm3-card__desc">{escape(desc)}</p>' if desc else "")
            + "</div>"
        )
        card_parts.append((f"<!-- wp:html -->\n{card}\n<!-- /wp:html -->", width))

    for i in range(0, len(card_parts), cols):
        parts.append(_columns(card_parts[i : i + cols], cls="wm3-grid-row"))
    return _group("\n".join(parts), cls="wm3-section wm3-services-cards")


# ── About / features variants ─────────────────────────────────────────────────

def _split_rounded(sec: dict) -> str:
    """50/50 rounded photo + copy (about pattern)."""
    heading = sec.get("heading", "")
    text = sec.get("text", "")
    image = sec.get("image", "")
    side = (sec.get("image_side") or "left").lower()
    items = sec.get("items") or []

    text_bits: list[str] = []
    if heading:
        text_bits.append(_h2(heading))
    if text:
        for para in text.split("\n\n"):
            if para.strip():
                text_bits.append(_p(para.strip()))
    if items:
        labels = []
        for it in items:
            if isinstance(it, dict):
                labels.append(str(it.get("title") or it.get("label") or ""))
            else:
                labels.append(str(it))
        labels = [l for l in labels if l]
        if labels:
            chips = "".join(f'<span class="wm3-chip">{escape(l)}</span>' for l in labels[:6])
            text_bits.append(
                f'<!-- wp:html -->\n<div class="wm3-chips">{chips}</div>\n<!-- /wp:html -->'
            )

    text_html = "\n".join(text_bits)
    img_html = _image_block(image, cls="wm3-rounded-img") if image else ""
    if image and side == "left":
        cols = [(img_html, "45%"), (text_html, "55%")]
    elif image:
        cols = [(text_html, "55%"), (img_html, "45%")]
    else:
        return _group(text_html, cls="wm3-section wm3-about-split")
    return _group(_columns(cols, cls="wm3-about-row"), cls="wm3-section wm3-about-split")


_DEFAULT_WARUM_ITEMS: list[dict[str, str]] = [
    {
        "title": "Erfahrenes Team",
        "description": "Geschult, freundlich und mit viel Erfahrung an Ihrer Seite.",
    },
    {
        "title": "Transparente Preise",
        "description": "Festpreisgarantie – ohne versteckte Kosten, ohne Überraschungen.",
    },
    {
        "title": "Kundenorientierter Service",
        "description": "Wir hören zu, beraten Sie und finden die beste Lösung.",
    },
    {
        "title": "Umweltfreundliche Entsorgung",
        "description": "Wir trennen, recyceln und entsorgen verantwortungsbewusst.",
    },
]


def _normalize_warum_items(items: list) -> list[dict[str, str]]:
    """Normalize feature items; fill missing descriptions from curated defaults."""
    defaults_by_key = {
        re.sub(r"[^a-z0-9]+", "", (d["title"]).lower()): d
        for d in _DEFAULT_WARUM_ITEMS
    }
    out: list[dict[str, str]] = []
    for idx, it in enumerate(items or []):
        if isinstance(it, dict):
            title = (it.get("title") or it.get("label") or "").strip()
            desc = (it.get("description") or it.get("text") or "").strip()
        else:
            title, desc = str(it).strip(), ""
        if not title and not desc:
            continue
        if not desc:
            key = re.sub(r"[^a-z0-9]+", "", title.lower())
            for dkey, dval in defaults_by_key.items():
                if dkey and (dkey in key or key in dkey):
                    # Prefer shorter curated titles when the source title is a long blob
                    if len(title) > 28:
                        title = dval["title"]
                    desc = dval["description"]
                    break
            if not desc and idx < len(_DEFAULT_WARUM_ITEMS):
                if len(title) > 28:
                    title = _DEFAULT_WARUM_ITEMS[idx]["title"]
                desc = _DEFAULT_WARUM_ITEMS[idx]["description"]
        out.append({"title": title, "description": desc})
    if not out:
        return [dict(x) for x in _DEFAULT_WARUM_ITEMS]
    return out[:4]


def _feature_stack(sec: dict) -> str:
    """Full-width Warum-uns: worker photo left + editable 4-benefit text panel."""
    raw_heading = (sec.get("heading") or "").strip()
    label = (sec.get("label") or "").strip()
    title = (sec.get("title") or sec.get("subheading") or "").strip()
    image = (sec.get("image") or "").strip()
    alt = (sec.get("alt") or "Unser Team").strip()

    # Recommendation layout: orange eyebrow + navy H2
    if not label and raw_heading and "warum" in raw_heading.lower():
        label = raw_heading
    if not title:
        title = (
            raw_heading
            if raw_heading and "warum" not in raw_heading.lower()
            else "Ihre Vorteile auf einen Blick"
        )
    if not label:
        label = "Warum uns wählen?"

    items = _normalize_warum_items(list(sec.get("items") or []))
    if not items and (sec.get("text") or "").strip():
        # Fall back to paragraph splits if no structured items
        paras = [p.strip() for p in str(sec.get("text")).split("\n\n") if p.strip()][:4]
        items = [{"title": p, "description": ""} for p in paras] or items

    cols_html: list[str] = []
    for idx, it in enumerate(items):
        title_i = it.get("title") or ""
        desc_i = it.get("description") or ""
        # Prefer semantic icons matching the recommendation (users/tag/headset/leaf)
        if any(k in title_i.lower() for k in ("service", "kunden")):
            svg = icon("headset", 26)
        elif any(k in title_i.lower() for k in ("preis", "festpreis", "transparent")):
            svg = icon("tag", 26)
        elif any(k in title_i.lower() for k in ("umwelt", "entsorg", "recycl")):
            svg = icon("leaf", 26)
        elif any(k in title_i.lower() for k in ("team", "erfahren")):
            svg = icon("users", 26)
        else:
            svg = icon_for_label(title_i, index=idx, size=26)
        cols_html.append(
            '<div class="wm3-warum__col">'
            f'<div class="wm3-warum__icon" aria-hidden="true">{svg}</div>'
            + (f'<h3 class="wm3-warum__col-title">{escape(title_i)}</h3>' if title_i else "")
            + (f'<p class="wm3-warum__col-desc">{escape(desc_i)}</p>' if desc_i else "")
            + "</div>"
        )

    # Publish worker crop if a local path slipped through
    if image and not image.startswith(("http://", "https://", "/")):
        local = Path(image)
        if not local.is_file():
            local = project_path("images", Path(image).name)
        if local.is_file():
            published = publish_local_for_wp(local)
            if published:
                image = published

    media = ""
    if image:
        media = (
            f'<div class="wm3-warum__media">'
            f'<img class="wm3-warum__img" src="{escape(image, quote=True)}" '
            f'alt="{escape(alt, quote=True)}" loading="lazy" decoding="async" />'
            f"</div>"
        )

    panel = (
        '<div class="wm3-warum__panel">'
        '<div class="wm3-warum__panel-inner">'
        f'<p class="wm3-warum__eyebrow">{escape(label)}</p>'
        f'<h2 class="wm3-warum__title">{escape(title)}</h2>'
        f'<div class="wm3-warum__grid">{"".join(cols_html)}</div>'
        "</div></div>"
    )

    html = (
        "<!-- wp:html -->\n"
        f'<section class="wm3-warum" aria-label="{escape(label, quote=True)}">'
        '<div class="wm3-warum__frame">'
        f"{media}{panel}"
        "</div></section>\n"
        "<!-- /wp:html -->"
    )
    return _group(html, cls="wm3-section wm3-features wm3-features--warum")


# ── Shared sections ───────────────────────────────────────────────────────────

def _process_step_image(index: int, step: dict) -> str:
    """Resolve per-step illustration (not the baked full-strip composite)."""
    root = project_path("images")
    n = index + 1
    title = str(step.get("title") or "").lower()
    candidates: list[str] = []
    explicit = str(step.get("image") or "").strip()
    if explicit:
        candidates.append(Path(explicit).name)
    if "besicht" in title:
        candidates += ["step-01-besichtigung.png", "schritt-step-01.png"]
    elif "festpreis" in title or "angebot" in title:
        candidates += ["step-02-angebot.png", "schritt-step-02.png"]
    elif "entrümpel" in title or "entruempel" in title:
        candidates += ["step-03-entruempelung.png", "schritt-step-03.png"]
    candidates += [
        f"step-0{n}-besichtigung.png" if n == 1 else "",
        f"step-0{n}-angebot.png" if n == 2 else "",
        f"step-0{n}-entruempelung.png" if n == 3 else "",
        f"schritt-step-0{n}.png",
        f"step-0{n}.png",
    ]
    seen: set[str] = set()
    for name in candidates:
        if not name or name in seen:
            continue
        seen.add(name)
        path = root / name
        if path.is_file():
            published = publish_local_for_wp(path)
            if published:
                return published
    return ""


def _section_process(sec: dict) -> str:
    """Process steps — live HTML cards over photo bg (not a baked strip in a white box)."""
    heading = (sec.get("heading") or "").strip()
    sub = (sec.get("subheading") or "").strip()
    label = (sec.get("label") or "So einfach geht's").strip()
    steps = [s for s in (sec.get("steps") or []) if isinstance(s, dict)]
    if not steps:
        return ""

    bg_url = _atmosphere_bg_url(
        version="schritte3",
        preferred=("schritte-bg.png", "schritte-bg.jpg"),
    )
    style = (
        f' style="--wm-band-bg:url(\'{escape(bg_url, quote=True)}\')"'
        if bg_url
        else ""
    )

    cards: list[str] = []
    for idx, step in enumerate(steps):
        num_raw = step.get("step", idx + 1)
        try:
            num = f"{int(num_raw):02d}"
        except (TypeError, ValueError):
            num = str(num_raw).strip() or f"{idx + 1:02d}"
        title = (step.get("title") or "").strip()
        desc = (step.get("description") or "").strip()
        img = _process_step_image(idx, step)
        alt = title or f"Schritt {num}"
        visual = ""
        if img:
            visual = (
                f'<div class="wm3-process__visual">'
                f'<img class="wm3-process__visual-img" src="{escape(img, quote=True)}" '
                f'alt="{escape(alt, quote=True)}" loading="lazy" decoding="async" />'
                f"</div>"
            )
        cards.append(
            f'<li class="wm3-process__card" aria-label="Schritt {escape(num)}">'
            + visual
            + (f'<h3 class="wm3-process__card-title">{escape(title)}</h3>' if title else "")
            + (f'<p class="wm3-process__card-desc">{escape(desc)}</p>' if desc else "")
            + "</li>"
        )

    labelled = ' aria-labelledby="wm3-process-heading"' if heading else ""
    html = (
        f'<section class="wm3-section wm3-process wm3-process--live"{style}{labelled}>'
        '<div class="wm3-process__wash" aria-hidden="true"></div>'
        '<div class="wm3-process__inner">'
        '<header class="wm3-process__head">'
        + (f'<p class="wm3-process__label">{escape(label)}</p>' if label else "")
        + (
            f'<h2 id="wm3-process-heading" class="wm3-section-title wm3-process__title">'
            f"{escape(heading)}</h2>"
            if heading
            else ""
        )
        + (f'<p class="wm3-process__sub">{escape(sub)}</p>' if sub else "")
        + "</header>"
        f'<ol class="wm3-process__grid">{"".join(cards)}</ol>'
        "</div></section>"
    )
    # Scroll-triggered staggered entrance (left → middle → right, 3.5s). No content change.
    script = """<script>
(function(){
  var sec=document.querySelector('.wm3-process--live');
  if(!sec||sec.dataset.wmProcessAnim==='1') return;
  sec.dataset.wmProcessAnim='1';
  if(window.matchMedia('(prefers-reduced-motion: reduce)').matches){
    sec.classList.add('is-inview');
    return;
  }
  sec.classList.add('wm3-process--js');
  var io=new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      if(!e.isIntersecting) return;
      sec.classList.add('is-inview');
      io.disconnect();
    });
  },{threshold:0.28,rootMargin:'0px 0px -10% 0px'});
  io.observe(sec);
})();
</script>"""
    return f"<!-- wp:html -->\n{html}\n{script}\n<!-- /wp:html -->"


def _section_before_after_magic(sec: dict) -> str:
    """Interactive Vorher→Nachher: worker video pushes wipe, resets after 10s."""
    from html import escape as _esc

    eyebrow = _esc((sec.get("label") or "ECHTE TRANSFORMATIONEN").strip())
    heading = _esc((sec.get("heading") or "Vorher Chaos. Nachher Besenrein.").strip())
    # No subtitle — keeps header tight and synced with the gallery block
    sub_html = ""
    cards_data = [c for c in (sec.get("cards") or []) if isinstance(c, dict)][:4]
    trust_items = sec.get("trust_items") or []

    video_src = str(sec.get("worker_video_src") or "").strip()
    if not video_src:
        for name in (
            "cat-3-sheet.png",
            "cat-3-alpha.webp",
            "Cat 3.mp4",
            "cat-3.mp4",
        ):
            for cand in (
                project_path("images", "Worker") / name,
            ):
                if cand.is_file():
                    video_src = publish_local_for_wp(cand)
                    if video_src:
                        break
            if video_src:
                break

    meta = sec.get("worker_sheet_meta") or {}
    try:
        meta_path = project_path("images", "Worker", "cat-3-sheet.json")
        if meta_path.is_file() and not meta:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        meta = meta or {}

    frames_n = int(meta.get("frames") or 100)
    cols_n = int(meta.get("cols") or 10)
    fps_n = int(meta.get("fps") or 10)
    fw_n = int(meta.get("fw") or 202)
    fh_n = int(meta.get("fh") or 360)

    video_esc = _esc(video_src, quote=True)
    if video_esc and "cat-3-sheet" in video_src.lower():
        # Canvas spritesheet — clearRect each frame (no WebP ghost trails)
        worker_html_template = (
            f'<canvas class="mba-cat__video" width="{fw_n}" height="{fh_n}" '
            f'data-sheet="{video_esc}" data-frames="{frames_n}" data-cols="{cols_n}" '
            f'data-fps="{fps_n}" data-fw="{fw_n}" data-fh="{fh_n}"></canvas>'
        )
    elif video_esc and video_src.lower().endswith((".webp", ".png", ".gif")):
        worker_html_template = (
            f'<img class="mba-cat__video" src="{video_esc}" alt="" '
            'decoding="async" draggable="false">'
        )
    elif video_esc:
        worker_html_template = (
            f'<video class="mba-cat__video" src="{video_esc}" '
            'muted playsinline preload="auto" disablepictureinpicture></video>'
        )
    else:
        worker_html_template = ""

    card_blocks: list[str] = []
    for i, card in enumerate(cards_data):
        lbl = _esc(str(card.get("label", "")))
        b_src = _esc(str(card.get("before_src", "")), quote=True)
        a_src = _esc(str(card.get("after_src", "")), quote=True)
        # No corner hint labels — photos stay the focus
        # Cat walk video on every card
        worker_block = (
            f'<div class="mba-worker" aria-hidden="true">{worker_html_template}</div>'
            if worker_html_template
            else ""
        )
        card_blocks.append(f"""
<article class="mba-card" data-idx="{i}" tabindex="0">
  <div class="mba-media" aria-label="Vorher und Nachher: {lbl}">
    <img class="mba-before" src="{b_src}" alt="Vorher – {lbl}" loading="lazy" decoding="async" draggable="false">
    <div class="mba-reveal" aria-hidden="true">
      <img class="mba-after" src="{a_src}" alt="Nachher – {lbl}" loading="lazy" decoding="async" draggable="false">
    </div>
    {worker_block}
  </div>
</article>""")

    # Elegant tonal cards — SEO defaults for short labels
    _trust_seo = {
        "festpreisgarantie": (
            "Festpreisgarantie",
            "Transparente Preise ohne versteckte Kosten – verbindlich nach der kostenlosen Besichtigung.",
            "shield",
        ),
        "diskret & zuverlässig": (
            "Diskret & Zuverlässig",
            "Diskrete Abwicklung und termintreue Ausführung – auch bei sensiblen Haushaltsauflösungen.",
            "users",
        ),
        "kurzfristige termine": (
            "Kurzfristige Termine",
            "Wir sind schnell vor Ort in Siegen und Umgebung – flexible Terminvereinbarung für Ihre Entrümpelung.",
            "clock",
        ),
        "fachgerechte arbeit": (
            "Fachgerechte Arbeit",
            "Unser erfahrenes Team arbeitet sorgfältig, diskret und termintreu bis zur besenreinen Übergabe.",
            "users",
        ),
        "umweltgerechte entsorgung": (
            "Umweltgerechte Entsorgung",
            "Fachgerechte Trennung, Recycling und Entsorgung – umweltfreundlich und vorschriftskonform.",
            "leaf",
        ),
        "nachhaltige entsorgung": (
            "Nachhaltige Entsorgung",
            "Fachgerechte Trennung, Recycling und Entsorgung – umweltfreundlich und vorschriftskonform.",
            "leaf",
        ),
        "regional für sie da": (
            "Regional für Sie da",
            "Persönlich vor Ort in Siegen und Umgebung – kurze Wege und schnelle Termine.",
            "pin",
        ),
    }
    # Refined monoline marks (stronger geometry than generic set)
    _trust_svgs = {
        "shield": (
            '<svg class="mba-trust__svg" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            '<path d="M12 3.2 19 6.2v5.4c0 4.35-2.95 8-7 9.6-4.05-1.6-7-5.25-7-9.6V6.2L12 3.2z" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linejoin="round"/>'
            '<path d="M8.8 12.1 11.1 14.4 15.4 9.9" fill="none" stroke="currentColor" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round"/>'
            "</svg>"
        ),
        "clock": (
            '<svg class="mba-trust__svg" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            '<circle cx="12" cy="12" r="8.1" fill="none" stroke="currentColor" stroke-width="1.65"/>'
            '<path d="M12 8.2V12l3.1 1.85" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/>'
            "</svg>"
        ),
        "users": (
            '<svg class="mba-trust__svg" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            '<circle cx="9" cy="8.2" r="2.55" fill="none" stroke="currentColor" stroke-width="1.65"/>'
            '<path d="M4.4 17.6c.35-2.45 2.2-3.85 4.6-3.85s4.25 1.4 4.6 3.85" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round"/>'
            '<circle cx="16.2" cy="8.6" r="2.1" fill="none" stroke="currentColor" stroke-width="1.55"/>'
            '<path d="M15.1 13.95c1.55-.35 3.2-.15 4.5 1.05.55.5.9 1.15 1.05 1.9" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round"/>'
            "</svg>"
        ),
        "leaf": (
            '<svg class="mba-trust__svg" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            '<path d="M5.2 18.5C5.5 11.2 11.4 5.4 19.2 5c-.1 7.6-5.7 13.8-13.4 14.2-1.15 0-2.2-.25-3.1-.85 2.55-.85 4.45-2.7 5.35-5.2" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round"/>'
            '<path d="M8.4 15.2c2.2-2.45 4.7-4.1 7.7-5.15" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round"/>'
            "</svg>"
        ),
        "pin": (
            '<svg class="mba-trust__svg" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            '<path d="M12 20.5s5.6-4.85 5.6-10.1a5.6 5.6 0 1 0-11.2 0c0 5.25 5.6 10.1 5.6 10.1z" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linejoin="round"/>'
            '<circle cx="12" cy="10.2" r="2.05" fill="none" stroke="currentColor" stroke-width="1.65"/>'
            "</svg>"
        ),
        "recycle": (
            '<svg class="mba-trust__svg" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            '<path d="M7.2 18.4h4.1M4.1 13.1l2-3.7M9.1 5.6l2.8.95M16.2 5.7l2.35 3.7M20 13l-1.85 3.6M13.9 18.4l-1.4-.95" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round"/>'
            "</svg>"
        ),
    }
    _icon_cycle = ("shield", "clock", "users", "leaf")
    trust_lis_parts: list[str] = []
    for i, t in enumerate(trust_items):
        icon_name = ""
        if isinstance(t, dict):
            title = str(t.get("title") or t.get("label") or "").strip()
            desc = str(t.get("description") or t.get("text") or "").strip()
            icon_name = str(t.get("icon") or "").strip()
        else:
            title = str(t).strip()
            desc = ""
        if not title:
            continue
        seo = _trust_seo.get(title.lower())
        if not desc and seo:
            desc = seo[1]
        if not icon_name:
            icon_name = (seo[2] if seo else "") or _icon_cycle[i % len(_icon_cycle)]
        if icon_name == "recycle":
            icon_name = "leaf"
        title_e = _esc(title)
        desc_e = _esc(desc) if desc else ""
        svg = _trust_svgs.get(icon_name) or _trust_svgs[_icon_cycle[i % 4]]
        trust_lis_parts.append(
            f'<li class="mba-trust__item mba-trust__item--{i % 4}">'
            f'<div class="mba-trust__mark" aria-hidden="true">{svg}</div>'
            f'<h3 class="mba-trust__title">{title_e}</h3>'
            + (f'<p class="mba-trust__desc">{desc_e}</p>' if desc_e else "")
            + "</li>"
        )
    trust_heading = _esc(
        (sec.get("trust_heading") or "Ihre Garantien bei der Entrümpelung").strip()
    )
    trust_block = ""
    if trust_lis_parts:
        trust_block = (
            '<header class="mba-promises__head">'
            '<p class="mba-promises__eyebrow">Unsere Versprechen</p>'
            f'<h2 id="mba-promises-heading" class="mba-promises__title">{trust_heading}</h2>'
            "</header>"
            '<ul class="mba-trust">'
            + "".join(trust_lis_parts)
            + "</ul>"
        )
    cards_html = "\n".join(card_blocks)

    css = """<style>
/* ── Magic Before/After — video wipe ───────── */
.wm3-mba{background-color:#fff;background-image:url('/wp-content/uploads/webmaker/mba-texture.png');background-repeat:repeat;background-size:130px 118px;background-position:center top;padding:2.75rem 0 2rem;overflow:visible}
.wm3-mba.wm3-section{padding-left:0;padding-right:0}

.mba-head{text-align:center;max-width:min(100%,52rem);margin:0 auto 1.45rem;padding:0 1.5rem}
.mba-title{font-size:clamp(1.55rem,2.6vw,2.35rem)!important;font-weight:800!important;color:var(--wm-ink,#111827)!important;margin:0!important;line-height:1.15!important;white-space:nowrap}
.mba-sub{color:var(--wm-muted,#6b7280);font-size:clamp(1.05rem,1.5vw,1.15rem);line-height:1.5;margin:.7rem 0 0;font-weight:500}

.mba-wrap{position:relative;max-width:min(100%,1400px);margin:0 auto;padding:0 clamp(1rem,3vw,2rem)}
.mba-grid{display:grid;grid-template-columns:1fr 1fr;gap:1.35rem}

.mba-card{border-radius:var(--wm-radius,16px);overflow:hidden;background:#fff;box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));border:none;cursor:pointer;transition:box-shadow var(--wm-ease,.28s ease),transform var(--wm-ease,.28s ease)}
.mba-card:hover,.mba-card:focus-visible,.mba-card.mba-active,.mba-card.mba-open{box-shadow:var(--wm-shadow-card,0 10px 28px rgba(15,23,42,.08));transform:translateY(-3px);outline:none}

.mba-media{position:relative;aspect-ratio:3/2;overflow:hidden;background:#e5e7eb}
.mba-before{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:center;display:block;z-index:1;pointer-events:none;filter:var(--wm-img-grade,saturate(1.06) brightness(1.02) contrast(1.03) sepia(.06))}
.mba-reveal{position:absolute;top:0;left:0;bottom:0;width:0;overflow:hidden;z-index:2;pointer-events:none;border-right:none;box-shadow:none}
.mba-reveal::after{content:"";position:absolute;top:0;right:-1px;width:2px;height:100%;border-radius:1px;background:linear-gradient(180deg,rgba(255,200,120,0) 0%,#ffb347 18%,#fff1c1 50%,#ff8c42 82%,rgba(255,200,120,0) 100%);box-shadow:0 0 4px 1px rgba(255,176,70,.95),0 0 10px 2px rgba(232,93,4,.55),0 0 18px 3px rgba(255,200,120,.28);pointer-events:none}
.mba-after{position:absolute;top:0;left:0;height:100%;width:var(--mba-w,100%);max-width:none;object-fit:cover;object-position:center;display:block;filter:var(--wm-img-grade,saturate(1.06) brightness(1.02) contrast(1.03) sepia(.06))}
.mba-card.mba-closing .mba-reveal{width:0!important;transition:width .9s ease}
/* Vorher/Nachher corner labels — hidden so photos stay the focus */
.mba-tag,.mba-tag--before,.mba-tag--after,.mba-hint{display:none!important}

/* Cat parked at left edge — curtain catches hand, then both travel together */
.mba-worker{position:absolute;bottom:1.5%;left:0;z-index:5;height:195px;width:auto;opacity:.92;transform:translateZ(0);pointer-events:none;transition:opacity .25s ease;will-change:left}
.mba-card.mba-active .mba-worker{opacity:1}
.mba-card.mba-closing .mba-worker{opacity:.92;left:0!important;transition:left .9s ease,opacity .25s ease}
.mba-cat__video{display:block;height:195px;width:auto;max-width:none;object-fit:contain;background:transparent;pointer-events:none}

/* Promises — softer navy blend + lighter cards */
.mba-promises{margin:0;padding:2.1rem clamp(1rem,3vw,2rem);background:linear-gradient(165deg,#0f2740 0%,#16324a 42%,#1a3a56 100%);border:none;color:#fff;position:relative}
.mba-promises::before{content:"";position:absolute;left:0;right:0;top:0;height:2.5rem;pointer-events:none;background:linear-gradient(180deg,rgba(255,255,255,.12),transparent)}
.mba-promises__inner{position:relative;z-index:1;max-width:min(100%,1180px);margin:0 auto}
.mba-promises__head{text-align:center;max-width:40rem;margin:0 auto 1.35rem}
.mba-promises__eyebrow{margin:0 0 .4rem;color:var(--wm-accent,#e85d04);font-family:var(--wm-font-display),system-ui,sans-serif;font-size:.78rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase}
.mba-promises__eyebrow::after{content:"";display:block;width:2.4rem;height:2px;margin:.5rem auto 0;background:var(--wm-accent,#e85d04);border-radius:1px}
.mba-promises__title{margin:0!important;color:#fff!important;font-family:var(--wm-font-display),system-ui,sans-serif;font-size:clamp(1.3rem,2.1vw,1.65rem)!important;font-weight:800!important;line-height:1.18!important;letter-spacing:-.02em}
.mba-trust{list-style:none;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1.15rem;margin:0;padding:0;width:100%;box-sizing:border-box;align-items:stretch}
.mba-trust__item{display:flex;flex-direction:column;align-items:flex-start;gap:0;margin:0;padding:1.15rem 1.1rem 1.1rem;border:1px solid rgba(255,255,255,.35);border-radius:var(--wm-radius,16px);box-sizing:border-box;min-height:100%;box-shadow:0 8px 24px rgba(0,0,0,.16);transition:transform var(--wm-ease,.28s ease),box-shadow var(--wm-ease,.28s ease)}
.mba-trust__item:hover{transform:translateY(-3px);box-shadow:0 14px 32px rgba(0,0,0,.2)}
.mba-trust__item--0{background:linear-gradient(165deg,#fffaf5 0%,#f7eee4 100%);border-color:rgba(255,255,255,.55);color:#3f342b}
.mba-trust__item--1{background:linear-gradient(165deg,#f8f9ff 0%,#eef0fb 100%);border-color:rgba(255,255,255,.55);color:#2f3348}
.mba-trust__item--2{background:linear-gradient(165deg,#fff8f6 0%,#f8ebe7 100%);border-color:rgba(255,255,255,.55);color:#43312e}
.mba-trust__item--3{background:linear-gradient(165deg,#f5fcf7 0%,#e8f6ec 100%);border-color:rgba(255,255,255,.55);color:#274433}
.mba-trust__mark{flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;width:2.75rem;height:2.75rem;margin:0 0 .9rem;border-radius:999px;background:rgba(15,23,42,.06);border:1px solid rgba(15,23,42,.08)}
.mba-trust__item--0 .mba-trust__mark{color:#6a5340;background:rgba(106,83,64,.1);border-color:rgba(106,83,64,.14)}
.mba-trust__item--1 .mba-trust__mark{color:#4a5070;background:rgba(74,80,112,.1);border-color:rgba(74,80,112,.14)}
.mba-trust__item--2 .mba-trust__mark{color:#6b4a44;background:rgba(107,74,68,.1);border-color:rgba(107,74,68,.14)}
.mba-trust__item--3 .mba-trust__mark{color:#2f6b45;background:rgba(47,107,69,.12);border-color:rgba(47,107,69,.16)}
.mba-trust__svg{width:1.35rem;height:1.35rem;display:block}
.mba-trust__title{margin:0 0 .5rem;color:inherit;font-family:var(--wm-font-display),system-ui,sans-serif;font-size:clamp(1.02rem,1.2vw,1.12rem);font-weight:700;line-height:1.25;letter-spacing:-.01em}
.mba-trust__desc{margin:0;color:inherit;opacity:.78;font-size:.9rem;line-height:1.55;font-weight:500}

@media(max-width:900px){
  .mba-wrap{padding:0 1rem}
  .mba-grid{gap:1.15rem}
  .mba-promises{padding:1.75rem 1.15rem}
  .mba-promises__head{margin-bottom:1rem}
  .mba-trust{grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem}
}
@media(max-width:640px){
  .mba-head{margin-bottom:1.35rem;padding:0 1rem}
  .mba-title{font-size:clamp(1.15rem,4.6vw,1.55rem)!important;white-space:nowrap}
  .mba-grid{grid-template-columns:1fr;gap:1.15rem}
  .mba-worker{height:161px}
  .mba-cat__video{height:161px}
  .mba-promises{padding:1.45rem 1rem}
  .mba-trust{grid-template-columns:1fr;gap:.9rem}
  .mba-trust__item{padding:1.1rem 1.05rem}
  .mba-trust__desc{font-size:.88rem}
}
</style>"""

    sub_block = f'<p class="mba-sub">{sub_html}</p>' if sub_html else ""
    trust_section = (
        f'<section class="mba-promises" aria-labelledby="mba-promises-heading">'
        f'<div class="mba-promises__inner">{trust_block}</div></section>'
        if trust_block
        else ""
    )
    html = f"""<section class="wm3-section wm3-mba" aria-label="Vorher Nachher Transformationen">
  <div class="mba-head">
    <p class="mba-eyebrow">{eyebrow}</p>
    <h2 class="mba-title">{heading}</h2>
    {sub_block}
  </div>
  <div class="mba-wrap">
    <div class="mba-grid">
{cards_html}
    </div>
  </div>
</section>
{trust_section}"""

    js = """<script>
(function(){
  function boot(){
    var cards=document.querySelectorAll('.mba-card');
    if(!cards.length)return;
    var PRE_MS=5100;   /* curtain stays at 0 (full Vor) */
    var SYNC_MS=5500;  /* cat starts pushing — curtain must already be at hand */
    var TOTAL_MS=10000;
    var HOLD_MS=8000;
    var HAND_RATIO=1.02; /* wipe sits just ahead of the pushing hand (past face) */
    var sheetImgs={};

    function easeInOut(t){
      return t<.5 ? 2*t*t : 1-Math.pow(-2*t+2,2)/2;
    }

    function setWidths(){
      cards.forEach(function(c){
        var m=c.querySelector('.mba-media');
        if(!m)return;
        var w=m.clientWidth||m.offsetWidth||0;
        if(w>0){
          c.style.setProperty('--mba-w', w+'px');
          var after=c.querySelector('.mba-after');
          if(after) after.style.width=w+'px';
        }
      });
    }
    setWidths();
    window.addEventListener('resize',setWidths,{passive:true});
    var grid=document.querySelector('.mba-grid');
    if(grid && typeof ResizeObserver!=='undefined'){
      try{ new ResizeObserver(setWidths).observe(grid); }catch(e){}
    }

    function drawCatFrame(cv, frame){
      var src=cv.getAttribute('data-sheet');
      var img=sheetImgs[src];
      if(!img||!img.complete||!img.naturalWidth)return;
      var fw=+cv.getAttribute('data-fw')||202;
      var fh=+cv.getAttribute('data-fh')||360;
      var cols=+cv.getAttribute('data-cols')||10;
      var total=+cv.getAttribute('data-frames')||1;
      var f=Math.max(0, Math.min(total-1, frame|0));
      var col=f%cols;
      var row=Math.floor(f/cols);
      var ctx=cv.getContext('2d');
      ctx.clearRect(0,0,cv.width,cv.height);
      /* full frame — no inset (keeps hands/gloves) */
      ctx.drawImage(img, col*fw, row*fh, fw, fh, 0, 0, cv.width, cv.height);
      cv._mbaFrame=f;
    }

    function loadSheet(cv, cb){
      var src=cv.getAttribute('data-sheet');
      if(sheetImgs[src]&&sheetImgs[src].complete){ cb&&cb(); return; }
      var img=new Image();
      img.decoding='async';
      img.onload=function(){ sheetImgs[src]=img; cb&&cb(); };
      img.src=src;
      sheetImgs[src]=img;
    }

    function showStill(cv){
      loadSheet(cv, function(){ drawCatFrame(cv, 0); });
    }

    function mediaWidth(card){
      var media=card.querySelector('.mba-media');
      return media ? (media.clientWidth||media.offsetWidth||0) : 0;
    }

    function workerWidth(card){
      var worker=card.querySelector('.mba-worker');
      return worker ? (worker.offsetWidth||worker.getBoundingClientRect().width||80) : 80;
    }

    function handXAt(card, catLeft){
      return catLeft + workerWidth(card)*HAND_RATIO;
    }

    function setCatLeft(card, px){
      var worker=card.querySelector('.mba-worker');
      if(!worker)return;
      worker.style.transition='none';
      worker.style.left=Math.max(0, px)+'px';
    }

    function setRevealWidth(card, px){
      var rev=card.querySelector('.mba-reveal');
      if(!rev)return;
      rev.style.transition='none';
      rev.style.width=Math.max(0, px)+'px';
    }

    function stopAnim(card){
      if(card._mbaRaf){ cancelAnimationFrame(card._mbaRaf); card._mbaRaf=0; }
      clearTimeout(card._mbaOpenT);
      clearTimeout(card._mbaHoldT);
      clearTimeout(card._mbaCloseT);
    }

    function resetCard(card){
      stopAnim(card);
      card.classList.add('mba-closing');
      card.classList.remove('mba-active','mba-wiping','mba-open','mba-settled');
      setRevealWidth(card, 0);
      setCatLeft(card, 0);
      var cv=card.querySelector('canvas.mba-cat__video');
      if(cv) drawCatFrame(cv, 0);
      card._mbaCloseT=setTimeout(function(){
        card.classList.remove('mba-closing','mba-busy');
        var rev=card.querySelector('.mba-reveal');
        if(rev){ rev.style.width='0'; rev.style.transition=''; }
        var worker=card.querySelector('.mba-worker');
        if(worker){ worker.style.left=''; worker.style.transition=''; }
      },950);
    }

    function openCard(card){
      if(card.classList.contains('mba-busy')) return;
      setWidths();
      stopAnim(card);
      card.classList.add('mba-busy','mba-active');
      card.classList.remove('mba-closing','mba-wiping','mba-open','mba-settled');
      setRevealWidth(card, 0);
      setCatLeft(card, 0);

      var cv=card.querySelector('canvas.mba-cat__video');
      if(!cv)return;
      var fps=+cv.getAttribute('data-fps')||10;
      var total=+cv.getAttribute('data-frames')||100;
      var t0=performance.now();

      function frame(now){
        var elapsed=now-t0;
        var fi=Math.min(total-1, Math.floor(elapsed/1000*fps));
        drawCatFrame(cv, fi);

        var full=mediaWidth(card);
        var ww=workerWidth(card);
        var handPark=handXAt(card, 0); /* hand while cat sits at left edge */

        if(elapsed < PRE_MS){
          /* 0–5.1s: full Vor, curtain idle at 0 */
          setCatLeft(card, 0);
          setRevealWidth(card, 0);
        } else if(elapsed < SYNC_MS){
          /* 5.1–5.5s: curtain races from 0 to the parked hand */
          if(!card.classList.contains('mba-wiping')) card.classList.add('mba-wiping');
          var catchP=(elapsed-PRE_MS)/Math.max(1, SYNC_MS-PRE_MS);
          catchP=easeInOut(Math.max(0, Math.min(1, catchP)));
          setCatLeft(card, 0);
          setRevealWidth(card, handPark*catchP);
        } else {
          /* 5.5s+: cat walks right; curtain locked to hand */
          if(!card.classList.contains('mba-wiping')) card.classList.add('mba-wiping');
          var walkP=(elapsed-SYNC_MS)/Math.max(1, TOTAL_MS-SYNC_MS);
          walkP=easeInOut(Math.max(0, Math.min(1, walkP)));
          var maxLeft=Math.max(0, full-ww);
          var catLeft=maxLeft*walkP;
          setCatLeft(card, catLeft);
          setRevealWidth(card, Math.min(full, handXAt(card, catLeft)));
        }

        if(elapsed < TOTAL_MS){
          card._mbaRaf=requestAnimationFrame(frame);
        } else {
          card.classList.add('mba-open','mba-settled');
          drawCatFrame(cv, total-1);
          setCatLeft(card, Math.max(0, full-ww));
          setRevealWidth(card, full);
          card._mbaHoldT=setTimeout(function(){ resetCard(card); }, HOLD_MS);
        }
      }

      loadSheet(cv, function(){
        drawCatFrame(cv, 0);
        card._mbaRaf=requestAnimationFrame(frame);
      });
    }

    document.querySelectorAll('canvas.mba-cat__video').forEach(showStill);

    cards.forEach(function(card){
      card.addEventListener('click', function(e){
        e.preventDefault();
        openCard(card);
      });
      card.addEventListener('keydown', function(e){
        if(e.key==='Enter'||e.key===' '){ e.preventDefault(); openCard(card); }
      });
    });
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',boot);
  else boot();
})();
</script>"""

    return f"<!-- wp:html -->\n{css}\n{html}\n{js}\n<!-- /wp:html -->"



def _section_before_after(sec: dict) -> str:
    """Vorher/Nachher — card illustrations + live HTML header/CTAs."""
    label = (sec.get("label") or "").strip()
    heading = (sec.get("heading") or "").strip()
    sub = (sec.get("subheading") or "").strip()
    image = (sec.get("image") or "").strip()
    alt = (sec.get("alt") or heading or "Vorher Nachher Entrümpelung").strip()
    cta_label = (sec.get("cta_label") or "").strip()
    cta_url = (sec.get("cta_url") or "/services/").strip()
    cta2 = (sec.get("cta_secondary_label") or "").strip()
    cta2_url = (sec.get("cta_secondary_url") or sec.get("phone_url") or "").strip()
    phone = (sec.get("phone") or "").strip()
    if not cta2_url and phone:
        digits = re.sub(r"\D+", "", phone)
        cta2_url = f"tel:{digits}" if digits else "#kontakt"
    trust = sec.get("trust_items") or []

    image_name = Path(image.split("?")[0]).name.lower() if image else ""
    # Full Sehen mockup already has header/CTAs baked in
    complete_design = "sehen-sie-selbst-homepage" in image_name

    parts: list[str] = []
    if image and complete_design:
        parts.append(
            "<!-- wp:html -->\n"
            f'<div class="wm3-ba__visual">'
            f'<img class="wm3-ba__img" src="{escape(image, quote=True)}" '
            f'alt="{escape(alt, quote=True)}" loading="lazy" decoding="async" />'
            f"</div>\n"
            "<!-- /wp:html -->"
        )
        if heading:
            parts.append(
                f'<!-- wp:heading {{"level":2,"className":"wm3-section-title screen-reader-text"}} -->\n'
                f'<h2 class="wm3-section-title screen-reader-text">{escape(heading)}</h2>\n'
                f"<!-- /wp:heading -->"
            )
        return _group("\n".join(parts), cls="wm3-section wm3-ba")

    head_bits: list[str] = ['<div class="wm3-ba__head">']
    if label:
        head_bits.append(f'<p class="wm3-ba__label">{escape(label)}</p>')
    if heading:
        head_bits.append(f'<h2 class="wm3-section-title wm3-ba__title">{escape(heading)}</h2>')
    if sub:
        head_bits.append(f'<p class="wm3-ba__sub">{escape(sub)}</p>')
    head_bits.append("</div>")
    parts.append("<!-- wp:html -->\n" + "\n".join(head_bits) + "\n<!-- /wp:html -->")

    if image:
        parts.append(
            "<!-- wp:html -->\n"
            f'<div class="wm3-ba__visual">'
            f'<img class="wm3-ba__img" src="{escape(image, quote=True)}" '
            f'alt="{escape(alt, quote=True)}" loading="lazy" decoding="async" />'
            f"</div>\n"
            "<!-- /wp:html -->"
        )

    actions: list[str] = ['<div class="wm3-ba__actions">']
    if cta_label:
        actions.append(
            f'<a class="wm3-ba__btn wm3-ba__btn--primary" href="{escape(cta_url, quote=True)}">'
            f"{escape(cta_label)}</a>"
        )
    if cta2:
        actions.append(
            f'<a class="wm3-ba__btn wm3-ba__btn--ghost" href="{escape(cta2_url or "#kontakt", quote=True)}">'
            f"{escape(cta2)}</a>"
        )
    actions.append("</div>")
    if trust:
        actions.append('<ul class="wm3-ba__trust">')
        for item in trust:
            text = item.get("label") if isinstance(item, dict) else str(item)
            if text:
                actions.append(f"<li>{escape(str(text))}</li>")
        actions.append("</ul>")
    if cta_label or cta2 or trust:
        parts.append("<!-- wp:html -->\n" + "\n".join(actions) + "\n<!-- /wp:html -->")
    return _group("\n".join(parts), cls="wm3-section wm3-ba")


def _section_trust_bar(sec: dict) -> str:
    """Premium trust band — mockup layout: peach 500+ + soft white icon cards in one row."""
    items = sec.get("items") or []
    if not items:
        items = [
            {
                "icon": "check",
                "value": "500+",
                "label": "abgeschlossene Projekte",
                "note": "Erfahrung, auf die Sie zählen können.",
            },
            {
                "icon": "calendar",
                "value": "Kostenlose Besichtigung",
                "label": "In Siegen & Umgebung",
            },
            {
                "icon": "shield",
                "value": "Transparenter Festpreis",
                "label": "Ohne Nachkalkulation",
            },
            {
                "icon": "leaf",
                "value": "Umweltgerechte Entsorgung",
                "label": "Recycling mit Verantwortung",
            },
            {
                "icon": "pin",
                "value": "Regional in Siegen",
                "label": "Persönlich vor Ort",
            },
            {
                "icon": "clock",
                "value": "Schnelle Termine",
                "label": "Kurze Reaktionszeiten",
            },
            {
                "icon": "broom",
                "value": "Besenreine Übergabe",
                "label": "Sauber übergeben",
            },
        ]
    heading = (
        sec.get("heading") or "Darauf können Sie sich verlassen"
    ).strip()
    label = (sec.get("label") or "Vertrauen").strip()
    sub = (
        sec.get("subheading")
        or sec.get("sub")
        or (
            "Unsere Leistungen stehen für Qualität, Transparenz und Zuverlässigkeit "
            "– darauf können Sie bauen."
        )
    ).strip()

    parsed: list[tuple[str, str, str, str]] = []
    for it in items:
        if isinstance(it, dict):
            value = str(it.get("value") or "").strip()
            lab = str(it.get("label") or "").strip()
            ico = str(it.get("icon") or "").strip()
            note = str(it.get("note") or it.get("foot") or "").strip()
        else:
            value, lab, ico, note = str(it).strip(), "", "", ""
        if not value and not lab:
            continue
        parsed.append((value, lab, ico, note))

    if not parsed:
        return ""

    hero_idx = next(
        (i for i, (v, _, _, _) in enumerate(parsed) if "500" in v),
        0,
    )
    hero_value, hero_lab, hero_ico, hero_note = parsed[hero_idx]
    if not hero_note:
        hero_note = "Erfahrung, auf die Sie zählen können."
    support = [p for i, p in enumerate(parsed) if i != hero_idx]

    support_cards: list[str] = []
    for idx, (value, lab, ico, _note) in enumerate(support):
        svg = icon(ico, 22) if ico else icon_for_label(lab or value, index=idx, size=22)
        delay = f'style="--wm-trust-d:{60 + idx * 55}ms"'
        support_cards.append(
            f'<li class="wm3-trust-live__card" {delay}>'
            f'<div class="wm3-trust-live__icon" aria-hidden="true">{svg}</div>'
            + (f'<strong class="wm3-trust-live__value">{escape(value)}</strong>' if value else "")
            + (f'<span class="wm3-trust-live__label">{escape(lab)}</span>' if lab else "")
            + "</li>"
        )

    hero_svg = (
        icon(hero_ico, 20)
        if hero_ico
        else icon_for_label(hero_lab or hero_value, index=0, size=20)
    )

    pattern_src = ""
    pattern_cand = project_path("images", "trust-section-texture.png")
    if pattern_cand.is_file():
        pattern_src = publish_local_for_wp(pattern_cand) or ""

    pattern_url = f"{pattern_src}?v=iso2" if pattern_src else ""
    style = (
        f' style="--wm-trust-pattern:url(\'{escape(pattern_url, quote=True)}\')"'
        if pattern_url
        else ""
    )
    html = (
        f'<section class="wm3-section wm3-trust-live wm3-trust-live--anchor"{style} '
        f'aria-labelledby="wm3-trust-live-heading">'
        '<div class="wm3-trust-live__pattern" aria-hidden="true"></div>'
        '<div class="wm3-trust-live__wash" aria-hidden="true"></div>'
        '<div class="wm3-trust-live__inner">'
        '<header class="wm3-trust-live__head">'
        f'<p class="wm3-trust-live__eyebrow">{escape(label)}</p>'
        f'<h2 id="wm3-trust-live-heading" class="wm3-trust-live__title">'
        f"{escape(heading)}</h2>"
        + (f'<p class="wm3-trust-live__sub">{escape(sub)}</p>' if sub else "")
        + "</header>"
        '<div class="wm3-trust-live__row">'
        '<div class="wm3-trust-live__card wm3-trust-live__card--hero" style="--wm-trust-d:0ms">'
        f'<div class="wm3-trust-live__icon wm3-trust-live__stat-icon" aria-hidden="true">{hero_svg}</div>'
        f'<strong class="wm3-trust-live__stat-num">{escape(hero_value)}</strong>'
        + (
            f'<span class="wm3-trust-live__stat-label">{escape(hero_lab)}</span>'
            if hero_lab
            else ""
        )
        + '<span class="wm3-trust-live__divider" aria-hidden="true"></span>'
        + f'<span class="wm3-trust-live__stat-note">{escape(hero_note)}</span>'
        + "</div>"
        f'<ul class="wm3-trust-live__support">{"".join(support_cards)}</ul>'
        "</div></div></section>"
    )
    script = """<script>
(function(){
  var sec=document.querySelector('.wm3-trust-live--anchor');
  if(!sec||sec.dataset.wmTrustAnim==='1') return;
  sec.dataset.wmTrustAnim='1';
  if(window.matchMedia('(prefers-reduced-motion: reduce)').matches){
    sec.classList.add('is-inview');
    return;
  }
  sec.classList.add('wm3-trust-live--js');
  var io=new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      if(!e.isIntersecting) return;
      sec.classList.add('is-inview');
      io.disconnect();
    });
  },{threshold:0.22,rootMargin:'0px 0px -8% 0px'});
  io.observe(sec);
})();
</script>"""
    return f"<!-- wp:html -->\n{html}\n{script}\n<!-- /wp:html -->"


def _section_trust_bar_cards(sec: dict) -> str:
    """HTML card fallback when Vertrauen artwork is unavailable."""
    items = sec.get("items") or []
    if not items:
        return ""
    heading = (sec.get("heading") or "").strip()
    label = (sec.get("label") or "Vertrauen").strip()
    cells: list[str] = []
    for idx, it in enumerate(items):
        if isinstance(it, dict):
            value = str(it.get("value") or "").strip()
            lab = str(it.get("label") or "").strip()
            ico = str(it.get("icon") or "").strip()
        else:
            value, lab, ico = str(it).strip(), "", ""
        if not value and not lab:
            continue
        svg = icon(ico, 22) if ico else icon_for_label(lab or value, index=idx, size=22)
        cells.append(
            '<li class="wm3-trust-strip__item">'
            f'<div class="wm3-trust-strip__icon" aria-hidden="true">{svg}</div>'
            '<div class="wm3-trust-strip__copy">'
            + (f'<strong class="wm3-trust-strip__value">{escape(value)}</strong>' if value else "")
            + (f'<span class="wm3-trust-strip__label">{escape(lab)}</span>' if lab else "")
            + "</div></li>"
        )
    if not cells:
        return ""
    head = ""
    if heading:
        head = (
            '<header class="wm3-trust-strip__head">'
            f'<p class="wm3-trust-strip__eyebrow">{escape(label)}</p>'
            f'<h2 class="wm3-trust-strip__title">{escape(heading)}</h2>'
            "</header>"
        )
    html = (
        '<section class="wm3-section wm3-trust-strip" aria-label="Vertrauenssignale">'
        '<div class="wm3-trust-strip__inner">'
        f"{head}"
        f'<ul class="wm3-trust-strip__grid">{"".join(cells)}</ul>'
        "</div></section>"
    )
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def _section_gallery(sec: dict) -> str:
    heading = sec.get("heading", "")
    images = sec.get("images") or []
    parts: list[str] = []
    if heading:
        parts.append(_h2(heading, "wm3-section-title"))
    row: list[tuple[str, str]] = []
    for src in images[:9]:
        if src:
            row.append((_image_block(src, cls="wm3-gallery-img"), "33.33%"))
        if len(row) == 3:
            parts.append(_columns(row, cls="wm3-gallery-row"))
            row = []
    if row:
        parts.append(_columns(row, cls="wm3-gallery-row"))
    return _group("\n".join(parts), cls="wm3-section wm3-gallery")


def _section_cta(sec: dict) -> str:
    """Homepage CTA band removed — contact lives in header/footer."""
    return ""


def _section_reviews(sec: dict) -> str:
    heading = sec.get("heading", "")
    items = sec.get("items") or []
    parts: list[str] = []
    if heading:
        parts.append(_h2(heading, "wm3-section-title"))
    if not items:
        return _group("\n".join(parts), cls="wm3-section wm3-reviews") if parts else ""
    width = f"{100 // max(1, min(3, len(items)))}%"
    cols: list[tuple[str, str]] = []
    for it in items:
        author = it.get("author", "")
        text = it.get("text", "")
        rating = int(it.get("rating") or 5)
        stars = "★" * min(5, rating) + "☆" * (5 - min(5, rating))
        html = (
            '<div class="wm3-review">'
            f'<div class="wm3-review__stars">{stars}</div>'
            + (f'<p class="wm3-review__text">{escape(text)}</p>' if text else "")
            + (f'<div class="wm3-review__author">— {escape(author)}</div>' if author else "")
            + "</div>"
        )
        cols.append((f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->", width))
    parts.append(_columns(cols, cls="wm3-reviews-row"))
    return _group("\n".join(parts), cls="wm3-section wm3-reviews")


def _section_contact(sec: dict) -> str:
    """Premium Kontakt layout — dark info rail + inquiry form + map."""
    phone = (sec.get("phone") or "").strip()
    email = (sec.get("email") or "").strip()
    address = (sec.get("address") or "").strip()
    hours = (sec.get("hours") or "Montag bis Freitag von 8–20 Uhr").strip()
    sidebar_title = (sec.get("sidebar_title") or "Kostenlose Beratung").strip()
    form_title = (sec.get("heading") or sec.get("form_title") or "Jetzt anfragen!").strip()
    form_intro = (
        sec.get("intro")
        or sec.get("subheading")
        or (
            "Haben Sie Fragen zu unseren Leistungen oder benötigen Sie ein "
            "unverbindliches Angebot? Rufen Sie uns einfach an oder schreiben Sie uns."
        )
    ).strip()
    facebook_url = (sec.get("facebook_url") or "").strip()
    linkedin_url = (sec.get("linkedin_url") or "").strip()

    digits = re.sub(r"[^\d+]", "", phone)
    if digits.startswith("0"):
        wa_digits = "49" + digits[1:]
    elif digits.startswith("+"):
        wa_digits = digits[1:]
    else:
        wa_digits = digits
    tel_href = f"tel:{digits}" if digits else "#"
    wa_href = f"https://wa.me/{wa_digits}" if wa_digits else "#"
    map_q = address.replace("Deutschland", "").strip(" ,")
    map_src = (
        "https://maps.google.com/maps?q="
        + escape(map_q, quote=True).replace("%20", "+")
        + "&z=15&output=embed"
    )

    def _rail_item(ico: str, label: str, value_html: str) -> str:
        return (
            f'<li class="wm3-kontakt__item">'
            f'<span class="wm3-kontakt__ico" aria-hidden="true">{icon(ico, 18)}</span>'
            f'<span class="wm3-kontakt__item-copy">'
            f'<strong class="wm3-kontakt__item-label">{escape(label)}</strong>'
            f'<span class="wm3-kontakt__item-value">{value_html}</span>'
            f"</span></li>"
        )

    items = [
        _rail_item(
            "phone",
            "Telefon",
            f'<a href="{escape(tel_href, quote=True)}">{escape(phone)}</a>',
        ),
        _rail_item(
            "whatsapp",
            "WhatsApp",
            f'<a class="wm3-kontakt__wa" href="{escape(wa_href, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">WhatsApp</a>',
        ),
        _rail_item(
            "mail",
            "E-Mail",
            f'<a href="mailto:{escape(email, quote=True)}">{escape(email)}</a>',
        ),
        _rail_item("pin", "Standort", escape(address)),
    ]
    if hours:
        items.append(_rail_item("clock", "Öffnungszeiten", escape(hours)))

    html = f"""
<section class="wm3-section wm3-kontakt" aria-labelledby="wm3-kontakt-title">
  <div class="wm3-kontakt__inner">
    <div class="wm3-kontakt__grid">
      <aside class="wm3-kontakt__rail" aria-label="Kontaktdaten">
        <h2 class="wm3-kontakt__rail-title">{escape(sidebar_title)}</h2>
        <ul class="wm3-kontakt__list">{"".join(items)}</ul>
        <div class="wm3-kontakt__social">
          <p class="wm3-kontakt__social-title">Social Media</p>
          <div class="wm3-kontakt__social-row">
            <a class="wm3-kontakt__social-link" href="{escape(facebook_url, quote=True)}"
               target="_blank" rel="noopener noreferrer" aria-label="Facebook">
              {icon("facebook", 18)}
            </a>
            <a class="wm3-kontakt__social-link" href="{escape(linkedin_url, quote=True)}"
               target="_blank" rel="noopener noreferrer" aria-label="LinkedIn">
              {icon("linkedin", 18)}
            </a>
          </div>
        </div>
      </aside>
      <div class="wm3-kontakt__main">
        <header class="wm3-kontakt__head">
          <h1 id="wm3-kontakt-title" class="wm3-kontakt__title">{escape(form_title)}</h1>
          <p class="wm3-kontakt__intro">{escape(form_intro)}</p>
        </header>
        <form class="wm3-kontakt__form" action="mailto:{escape(email, quote=True)}"
              method="post" enctype="text/plain">
          <div class="wm3-kontakt__fields">
            <label class="wm3-kontakt__field">
              <span class="wm3-kontakt__sr">Name</span>
              <input type="text" name="Name" placeholder="Name" autocomplete="name" required />
            </label>
            <label class="wm3-kontakt__field">
              <span class="wm3-kontakt__sr">Betreff</span>
              <input type="text" name="Betreff" placeholder="Betreff" required />
            </label>
            <label class="wm3-kontakt__field">
              <span class="wm3-kontakt__sr">Telefon</span>
              <input type="tel" name="Telefon" placeholder="Telefon" autocomplete="tel" />
            </label>
            <label class="wm3-kontakt__field">
              <span class="wm3-kontakt__sr">E-Mail</span>
              <input type="email" name="E-Mail" placeholder="E-Mail" autocomplete="email" required />
            </label>
            <label class="wm3-kontakt__field wm3-kontakt__field--full">
              <span class="wm3-kontakt__sr">Ihre Nachricht</span>
              <textarea name="Nachricht" rows="5" placeholder="Ihre Nachricht" required></textarea>
            </label>
          </div>
          <label class="wm3-kontakt__consent">
            <input type="checkbox" name="Datenschutz" value="akzeptiert" required />
            <span>Ich habe die <a href="/datenschutz/">Datenschutzerklärung</a> gelesen und akzeptiere diese.</span>
          </label>
          <button type="submit" class="wm3-kontakt__submit">Senden</button>
        </form>
      </div>
    </div>
    <div class="wm3-kontakt__map-wrap">
      <iframe class="wm3-kontakt__map"
        title="Standort"
        src="{map_src}"
        loading="lazy" referrerpolicy="no-referrer-when-downgrade"
        allowfullscreen></iframe>
    </div>
  </div>
</section>
""".strip()
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def _section_faq(sec: dict) -> str:
    heading = sec.get("heading", "")
    items = sec.get("items") or []
    parts: list[str] = []
    if heading:
        parts.append(_h2(heading))
    for it in items:
        q = it.get("question", "")
        a = it.get("answer", "")
        if q:
            parts.append(_h3(q, "wm3-faq__q"))
        if a:
            parts.append(_p(a, "wm3-faq__a"))
    return _group("\n".join(parts), cls="wm3-section wm3-faq")


# ── Gutenberg primitives ──────────────────────────────────────────────────────

def _h1(text: str, cls: str = "") -> str:
    meta = json.dumps({"level": 1, **({"className": cls} if cls else {})})
    attr = f' class="{cls}"' if cls else ""
    return f"<!-- wp:heading {meta} -->\n<h1{attr}>{escape(text)}</h1>\n<!-- /wp:heading -->"


def _h2(text: str, cls: str = "") -> str:
    meta = json.dumps({"level": 2, **({"className": cls} if cls else {})})
    attr = f' class="{cls}"' if cls else ""
    return f"<!-- wp:heading {meta} -->\n<h2{attr}>{escape(text)}</h2>\n<!-- /wp:heading -->"


def _h3(text: str, cls: str = "") -> str:
    meta = json.dumps({"level": 3, **({"className": cls} if cls else {})})
    attr = f' class="{cls}"' if cls else ""
    return f"<!-- wp:heading {meta} -->\n<h3{attr}>{escape(text)}</h3>\n<!-- /wp:heading -->"


def _p(text: str, cls: str = "") -> str:
    meta = f' {{"className":"{cls}"}}' if cls else ""
    attr = f' class="{cls}"' if cls else ""
    return f"<!-- wp:paragraph{meta} -->\n<p{attr}>{escape(text)}</p>\n<!-- /wp:paragraph -->"


def _image_block(src: str, cls: str = "", alt: str = "") -> str:
    if not src:
        return ""
    meta = json.dumps({"sizeSlug": "large", **({"className": cls} if cls else {})})
    return (
        f"<!-- wp:image {meta} -->\n"
        f'<figure class="wp-block-image size-large{(" " + cls) if cls else ""}">'
        f'<img src="{escape(src, quote=True)}" alt="{escape(alt, quote=True)}" '
        f'loading="lazy"/></figure>\n'
        "<!-- /wp:image -->"
    )


def _buttons(
    cta_label: str,
    cta_url: str,
    phone: str = "",
    *,
    center: bool = False,
) -> str:
    btns: list[str] = []
    label = (cta_label or "").strip()
    phone = (phone or "").strip()
    phone_digits = re.sub(r"\D+", "", phone)
    # Skip duplicate phone button when the primary CTA already shows that number.
    if phone and phone_digits and phone_digits in re.sub(r"\D+", "", label):
        phone = ""
    if label:
        btns.append(
            '<!-- wp:button {"className":"is-style-fill"} -->\n'
            '<div class="wp-block-button is-style-fill">'
            f'<a class="wp-block-button__link wp-element-button" href="{escape(cta_url or "#", quote=True)}">'
            f"{escape(label)}</a></div>\n<!-- /wp:button -->"
        )
    if phone:
        tel = "tel:" + phone.replace(" ", "").replace("/", "")
        btns.append(
            '<!-- wp:button {"className":"is-style-outline"} -->\n'
            '<div class="wp-block-button is-style-outline">'
            f'<a class="wp-block-button__link wp-element-button" href="{escape(tel, quote=True)}">'
            f"{escape(phone)}</a></div>\n<!-- /wp:button -->"
        )
    if not btns:
        return ""
    justify = "center" if center else "left"
    return (
        f'<!-- wp:buttons {{"layout":{{"type":"flex","justifyContent":"{justify}"}}}} -->\n'
        '<div class="wp-block-buttons">\n'
        + "\n".join(btns)
        + "\n</div>\n<!-- /wp:buttons -->"
    )


def _group(inner: str, *, cls: str = "") -> str:
    meta = f' {{"className":"{cls}"}}' if cls else ""
    outer = f"wp-block-group {cls}".strip()
    return (
        f"<!-- wp:group{meta} -->\n"
        f'<div class="{outer}"><div class="wp-block-group__inner-container">\n'
        f"{inner}\n</div></div>\n<!-- /wp:group -->"
    )


def _columns(cols: list[tuple[str, str]], *, cls: str = "") -> str:
    meta = f' {{"className":"{cls}"}}' if cls else ""
    outer = f"wp-block-columns {cls}".strip()
    chunks: list[str] = []
    for inner, width in cols:
        chunks.append(
            f'<!-- wp:column {{"width":"{width}"}} -->\n'
            f'<div class="wp-block-column" style="flex-basis:{width}">\n'
            f"{inner}\n</div>\n<!-- /wp:column -->"
        )
    return (
        f"<!-- wp:columns{meta} -->\n"
        f'<div class="{outer}">\n'
        + "\n\n".join(chunks)
        + "\n</div>\n<!-- /wp:columns -->"
    )


# ── CSS (variants) ────────────────────────────────────────────────────────────

def _global_css(tokens: DesignTokens) -> str:
    vars_css = css_variables(tokens)
    chrome = chrome_css(tokens)
    guide = services_guide_css()
    polish = premium_polish_css()
    return f"""<!-- wp:html -->
<style>
{vars_css}
{chrome}
{guide}
/* WebMaker Agent 1 — blueprint-driven variants */
.wm3-section{{margin:0;padding:var(--wm-space-section,2.5rem) 1.5rem}}
.wm3-section p{{line-height:1.65}}
.wm3-section-title{{font-size:clamp(1.6rem,2.5vw,2.2rem)!important;font-weight:700!important;margin:0 0 var(--wm-space-head,1.55rem)!important;color:var(--wm-ink);line-height:1.18!important}}
/* Shared section heading: orange eyebrow ABOVE navy H2 */
.wm3-svc-photo__eyebrow,.wm3-process__label,.wm3-ba__label,.wm3-warum__eyebrow,.mba-eyebrow{{
  margin:0 0 .55rem;color:var(--wm-accent);font-family:var(--wm-font-display),system-ui,sans-serif;
  font-size:.78rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;line-height:1.2;
}}
.wm3-svc-photo__eyebrow::after,.wm3-process__label::after,.wm3-ba__label::after,.mba-eyebrow::after{{
  content:"";display:block;width:2.4rem;height:2px;margin:.55rem auto 0;background:var(--wm-accent);border-radius:1px;
}}
.wm3-warum__eyebrow::after{{content:"";display:block;width:2.4rem;height:2px;margin-top:.55rem;background:var(--wm-accent);margin-left:0}}

/* Hero full-bleed — edge-to-edge photo + on-image SEO headline */
.wm3-hero-overlay,.wm3-hero-bleed{{position:relative;min-height:min(90vh,760px);background-size:cover;background-position:38% 46%;background-repeat:no-repeat;background-color:var(--wm-navy);display:flex;align-items:center;padding:5rem 1.5rem;overflow:hidden}}
/* Left-side readability gradient — keeps image bright on the right */
.wm3-hero-bleed__shade,.wm3-hero-overlay__shade{{position:absolute;inset:0;background:linear-gradient(to right,rgba(4,18,36,.48) 0%,rgba(4,18,36,.32) 35%,rgba(4,18,36,.1) 62%,transparent 100%);pointer-events:none}}
/* Bottom cinematic vignette for depth */
.wm3-hero-bleed__vignette{{position:absolute;bottom:0;left:0;right:0;height:200px;background:linear-gradient(to top,rgba(2,8,20,.28) 0%,transparent 100%);pointer-events:none;z-index:0}}
.wm3-hero-bleed__inner,.wm3-hero-overlay__inner{{position:relative;z-index:1;width:100%;max-width:1140px;margin:0 auto;animation:wm3-rise .75s ease-out both}}
.wm3-hero-bleed__title{{margin:0 0 1.15rem!important;font-size:clamp(2.35rem,5.05vw,3.85rem)!important;font-weight:900!important;line-height:1.12!important;color:#fff!important;max-width:16ch;text-shadow:0 2px 12px rgba(0,0,0,.38),0 4px 32px rgba(0,0,0,.18)}}
.wm3-hero-bleed__sub{{margin:0 0 2.35rem;color:rgba(255,255,255,.9);font-size:clamp(1.22rem,1.9vw,1.45rem);line-height:1.9;max-width:40ch;text-shadow:0 1px 8px rgba(0,0,0,.22)}}
.wm3-hero-bleed__actions{{display:flex;flex-direction:column;align-items:flex-start;gap:1rem;margin:0 0 2.1rem}}
.wm3-hero-bleed__softlink{{display:inline-flex;align-items:center;gap:.4rem;color:rgba(255,255,255,.72);font-size:.9rem;font-weight:500;text-decoration:none!important;letter-spacing:.01em;transition:color .18s ease}}
.wm3-hero-bleed__softlink:hover{{color:rgba(255,255,255,.95)}}
.wm3-hero-bleed__softlink-ico{{font-size:.85rem;line-height:1;opacity:.9}}
/* Trust badges — compact 2×2 premium chips */
.wm3-hero-bleed__badges{{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(2,minmax(0,max-content));gap:.6rem .75rem;max-width:34rem}}
.wm3-hero-bleed__badges li{{display:inline-flex;align-items:center;gap:.5rem;padding:.72rem 1.15rem;border-radius:10px;background:rgba(8,20,36,.42);border:1px solid rgba(255,255,255,.14);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);color:rgba(255,255,255,.96);font-size:1.1rem;font-weight:600;letter-spacing:.01em;line-height:1.25;white-space:nowrap;box-shadow:0 4px 14px rgba(0,0,0,.12)}}
.wm3-badge-check{{color:{tokens.accent};font-size:1.15rem;font-weight:800;flex-shrink:0;line-height:1}}
.wm3-btn{{display:inline-block;padding:.85rem 1.6rem;border-radius:var(--wm-radius-sm,12px);font-weight:700;letter-spacing:.01em;text-decoration:none!important;font-size:1rem;transition:transform var(--wm-ease),background var(--wm-ease),box-shadow var(--wm-ease),border-color var(--wm-ease)}}
.wm3-btn--lg{{padding:1rem 1.85rem;font-size:1.08rem;border-radius:var(--wm-radius-sm,12px)}}
.wm3-btn--primary{{background:var(--wm-accent);color:#fff!important;box-shadow:0 8px 22px rgba(232,93,4,.28)}}
.wm3-btn--primary:hover{{background:var(--wm-accent-dark);transform:translateY(-2px);box-shadow:0 12px 28px rgba(232,93,4,.36)}}
.wm3-btn--hero-call{{
  display:inline-flex;align-items:center;gap:.55rem;
  background:linear-gradient(135deg,var(--wm-accent),#c44d00);
  color:#fff!important;font-weight:700;font-size:1.05rem;letter-spacing:.01em;
  padding:.88rem 1.55rem;border-radius:999px;white-space:nowrap;
  box-shadow:0 8px 22px rgba(232,93,4,.28);
  transition:transform var(--wm-ease),box-shadow var(--wm-ease);
}}
.wm3-btn--hero-call .wm3-ico{{margin:0;display:block;flex-shrink:0}}
.wm3-btn--hero-call:hover{{transform:translateY(-2px);box-shadow:0 12px 28px rgba(232,93,4,.36);color:#fff!important}}
.wm3-btn--ghost{{background:transparent;color:var(--wm-ink)!important;border:2px solid #d1d5db}}
.wm3-btn--ghost:hover{{border-color:var(--wm-accent);color:var(--wm-accent)!important;transform:translateY(-1px)}}
@keyframes wm3-rise{{from{{opacity:0;transform:translateY(18px)}}to{{opacity:1;transform:translateY(0)}}}}

/* Trust strip — key buying signals under hero */
.wm3-trust-strip{{background:linear-gradient(180deg,#fff 0%,var(--wm-surface-alt) 100%);padding:1.65rem 0 1.85rem;border-bottom:1px solid rgba(15,23,42,.06)}}
.wm3-trust-strip.wm3-section{{padding-left:0;padding-right:0}}
.wm3-trust-strip__inner{{max-width:min(100%,1400px);margin:0 auto;padding:0 clamp(1rem,3vw,2rem);box-sizing:border-box}}
.wm3-trust-strip__head{{text-align:center;max-width:40rem;margin:0 auto 1.25rem}}
.wm3-trust-strip__eyebrow{{margin:0 0 .45rem;color:var(--wm-accent);font-family:var(--wm-font-display);font-size:.78rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase}}
.wm3-trust-strip__eyebrow::after{{content:"";display:block;width:2.4rem;height:2px;margin:.45rem auto 0;background:var(--wm-accent);border-radius:1px}}
.wm3-trust-strip__title{{margin:0!important;font-family:var(--wm-font-display)!important;font-size:clamp(1.35rem,2.2vw,1.75rem)!important;font-weight:800!important;color:var(--wm-navy)!important;line-height:1.2!important}}
.wm3-trust-strip__grid{{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:.85rem}}
.wm3-trust-strip__item{{
  display:flex;flex-direction:column;align-items:center;text-align:center;gap:.55rem;
  padding:1rem .7rem;border-radius:var(--wm-radius,14px);background:#fff;
  border:1px solid rgba(15,23,42,.06);box-shadow:0 6px 18px rgba(15,23,42,.05);
  transition:transform var(--wm-ease,.22s ease),box-shadow var(--wm-ease,.22s ease),border-color var(--wm-ease,.22s ease);
}}
.wm3-trust-strip__item:hover{{transform:translateY(-3px);box-shadow:0 12px 28px rgba(15,23,42,.1);border-color:rgba(232,93,4,.28)}}
.wm3-trust-strip__icon{{
  width:44px;height:44px;border-radius:12px;display:flex;align-items:center;justify-content:center;
  background:#fff4ec;color:var(--wm-accent);flex-shrink:0;
}}
.wm3-trust-strip__icon .wm3-ico{{display:block;margin:0}}
.wm3-trust-strip__copy{{display:flex;flex-direction:column;gap:.15rem;min-width:0}}
.wm3-trust-strip__value{{font-family:var(--wm-font-display);font-size:.98rem;font-weight:800;color:var(--wm-navy);line-height:1.2}}
.wm3-trust-strip__label{{font-size:.82rem;line-height:1.35;color:var(--wm-muted);font-weight:500}}
@media(max-width:980px){{
  .wm3-trust-strip__grid{{grid-template-columns:repeat(3,minmax(0,1fr));gap:.75rem}}
}}
@media(max-width:560px){{
  .wm3-trust-strip__grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}
}}

/* Trust art — legacy pasted-image styles (kept unused) */
.wm3-trust-art{{background:#fff;padding:.85rem 0 .85rem;overflow:visible}}
.wm3-trust-art.wm3-section{{padding-left:0;padding-right:0;margin:0}}
.wm3-trust-art__inner{{max-width:min(100%,1400px);margin:0 auto;padding:0 clamp(1rem,3vw,2rem);box-sizing:border-box}}
.wm3-trust-art__stage{{position:relative;margin:0;line-height:0}}
.wm3-trust-art__img{{width:100%;height:auto;display:block;margin:0}}

/* Trust live — mockup: soft peach 500+ + white icon cards in one row */
.wm3-trust-live{{
  position:relative;isolation:isolate;overflow:hidden;
  padding:clamp(4rem,6.5vw,5.6rem) 0 clamp(3.6rem,5.8vw,5rem);margin:0;
  background-color:#fff;
}}
.wm3-trust-live.wm3-section{{padding-left:0;padding-right:0}}
.wm3-trust-live__pattern{{
  position:absolute;inset:0;z-index:0;pointer-events:none;
  background-color:#fff;
  background-image:var(--wm-trust-pattern);
  background-size:cover;
  background-position:center center;
  background-repeat:no-repeat;
  opacity:.65;
}}
.wm3-trust-live__wash{{
  position:absolute;inset:0;z-index:0;pointer-events:none;
  background:
    linear-gradient(180deg,rgba(255,255,255,.42) 0%,rgba(255,255,255,.18) 45%,rgba(255,255,255,.48) 100%);
}}
.wm3-trust-live__inner{{
  position:relative;z-index:1;
  max-width:min(100%,1320px);margin:0 auto;
  padding:0 clamp(1rem,2.5vw,1.75rem);box-sizing:border-box;
}}
.wm3-trust-live__head{{
  text-align:center;max-width:40rem;margin:0 auto clamp(2.4rem,4vw,3.2rem);
}}
.wm3-trust-live__eyebrow{{
  margin:0 0 .85rem;color:var(--wm-accent);font-family:var(--wm-font-display);
  font-size:.72rem;font-weight:700;letter-spacing:.18em;text-transform:uppercase;
}}
.wm3-trust-live__eyebrow::after{{display:none}}
.wm3-trust-live__title{{
  margin:0 0 1rem!important;font-family:var(--wm-font-display)!important;
  font-size:clamp(1.75rem,3.1vw,2.55rem)!important;font-weight:700!important;
  color:var(--wm-navy)!important;line-height:1.18!important;letter-spacing:-.028em;
}}
.wm3-trust-live__sub{{
  margin:0 auto;max-width:34rem;color:#64748b;
  font-family:var(--wm-font-body);font-size:clamp(1rem,1.2vw,1.08rem);
  line-height:1.7;font-weight:400;
}}
.wm3-trust-live__row{{
  display:grid;
  grid-template-columns:minmax(11.5rem,1.15fr) minmax(0,5.85fr);
  gap:clamp(.75rem,1.4vw,1.05rem);
  align-items:stretch;
}}
.wm3-trust-live__support{{
  list-style:none;margin:0;padding:0;min-width:0;
  display:grid;grid-template-columns:repeat(6,minmax(0,1fr));
  gap:clamp(.75rem,1.4vw,1.05rem);
}}
.wm3-trust-live__card{{
  display:flex;flex-direction:column;align-items:center;text-align:center;
  gap:0;margin:0;min-width:0;box-sizing:border-box;
  padding:clamp(1.35rem,1.9vw,1.65rem) clamp(.7rem,1.1vw,.95rem) clamp(1.4rem,1.9vw,1.7rem);
  color:var(--wm-navy);
  background:#fff;
  border:none;
  border-radius:18px;
  box-shadow:0 8px 28px rgba(15,23,42,.05);
  transition:transform .35s cubic-bezier(.22,1,.36,1),box-shadow .35s ease;
}}
.wm3-trust-live__card--hero{{
  background:#FFF3EA;
  box-shadow:0 10px 32px rgba(232,93,4,.08),0 4px 14px rgba(15,23,42,.03);
  padding:clamp(1.45rem,2vw,1.85rem) 1rem clamp(1.5rem,2vw,1.9rem);
}}
.wm3-trust-live__card:hover{{
  transform:translateY(-3px);
  box-shadow:0 14px 36px rgba(15,23,42,.07);
}}
.wm3-trust-live__icon{{
  width:2.65rem;height:2.65rem;border-radius:999px;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;
  margin:0 0 .95rem;
  background:rgba(232,93,4,.08);color:var(--wm-accent);
  box-shadow:none;
}}
.wm3-trust-live__icon .wm3-ico{{display:block;margin:0}}
.wm3-trust-live__stat-icon{{
  width:2.55rem;height:2.55rem;margin:0 0 .7rem;
  background:#fff;color:var(--wm-accent);
  box-shadow:0 4px 14px rgba(232,93,4,.12);
}}
.wm3-trust-live__value{{
  display:block;margin:0 0 .45rem;padding:0;
  font-family:var(--wm-font-display);
  font-size:clamp(.84rem,1.05vw,.95rem);
  font-weight:700;color:var(--wm-navy);line-height:1.25;letter-spacing:-.02em;
}}
.wm3-trust-live__label{{
  display:block;margin:0;padding:0;
  font-family:var(--wm-font-body);
  font-size:clamp(.76rem,.95vw,.84rem);line-height:1.4;color:#64748b;font-weight:400;
}}
.wm3-trust-live__stat-num{{
  display:block;margin:0 0 .2rem;
  font-family:var(--wm-font-display);
  font-size:clamp(2.4rem,3.8vw,3.15rem);font-weight:800;line-height:1;
  letter-spacing:-.04em;color:var(--wm-accent);
}}
.wm3-trust-live__stat-label{{
  display:block;margin:0;
  font-family:var(--wm-font-display);
  font-size:clamp(.82rem,1.05vw,.92rem);font-weight:700;letter-spacing:-.01em;
  color:var(--wm-navy);line-height:1.3;
}}
.wm3-trust-live__divider{{
  display:block;width:1.65rem;height:2px;margin:.85rem auto .75rem;
  background:var(--wm-accent);border-radius:1px;opacity:.85;
}}
.wm3-trust-live__stat-note{{
  display:block;margin:0;
  font-family:var(--wm-font-body);
  font-size:.78rem;line-height:1.45;color:#64748b;font-weight:400;
}}
/* Staggered fade-up entrance */
.wm3-trust-live--js:not(.is-inview) .wm3-trust-live__card{{
  opacity:0;transform:translateY(1.1rem);pointer-events:none;
}}
.wm3-trust-live.is-inview .wm3-trust-live__card{{
  animation:wm3-trust-rise .7s cubic-bezier(.22,1,.36,1) var(--wm-trust-d,0ms) both;
}}
@keyframes wm3-trust-rise{{
  from{{opacity:0;transform:translateY(1.1rem)}}
  to{{opacity:1;transform:none}}
}}
@media(prefers-reduced-motion:reduce){{
  .wm3-trust-live--js:not(.is-inview) .wm3-trust-live__card,
  .wm3-trust-live.is-inview .wm3-trust-live__card{{
    opacity:1!important;transform:none!important;animation:none!important;pointer-events:auto!important;
  }}
}}
@media(max-width:1100px){{
  .wm3-trust-live__row{{grid-template-columns:1fr}}
  .wm3-trust-live__support{{grid-template-columns:repeat(3,minmax(0,1fr))}}
  .wm3-trust-live__card--hero{{max-width:18rem;margin:0 auto}}
}}
@media(max-width:720px){{
  .wm3-trust-live{{padding:3.2rem 0 2.8rem}}
  .wm3-trust-live__support{{grid-template-columns:repeat(2,minmax(0,1fr));gap:.85rem}}
  .wm3-trust-live__wash{{
    background:linear-gradient(180deg,rgba(255,255,255,.55) 0%,rgba(255,255,255,.72) 100%);
  }}
}}
@media(max-width:480px){{
  .wm3-trust-live__support{{grid-template-columns:1fr;max-width:18rem;margin:0 auto}}
  .wm3-trust-live__card{{padding:1.35rem 1.15rem}}
}}

/* Legacy equal-grid trust (unused by v2 anchor layout) */
.wm3-trust-live__grid{{display:none}}

/* Hero split — navy copy + bright photo */
.wm3-hero-split{{background:var(--wm-navy);color:#fff;padding:0;margin:0}}
.wm3-hero-split__inner{{display:grid;grid-template-columns:minmax(280px,1fr) minmax(280px,1.05fr);min-height:560px;max-width:100%}}
.wm3-hero-split__copy{{display:flex;flex-direction:column;justify-content:center;padding:3.5rem 3rem;max-width:560px;margin-left:auto}}
.wm3-hero-split__title{{color:#fff!important;font-size:clamp(2.15rem,4.15vw,3.15rem)!important;font-weight:900!important;line-height:1.15!important;margin:0 0 1rem!important}}
.wm3-hero-split__sub{{color:rgba(255,255,255,.88)!important;font-size:1.22rem;line-height:1.85;margin:0 0 1.6rem}}
.wm3-hero-split__actions{{display:flex;flex-wrap:wrap;align-items:center;gap:.85rem 1.1rem;margin:0 0 1rem}}
.wm3-hero-split__phone{{color:rgba(255,255,255,.9);font-weight:700;font-size:1rem}}
.wm3-hero-split__trust{{margin:0;color:rgba(255,255,255,.7);font-size:.95rem}}
.wm3-hero-split__media{{position:relative;min-height:420px;background:var(--wm-navy-mid)}}
.wm3-hero-split__media img{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block;border-radius:0}}
.wm3-hero-split__media--empty{{background:linear-gradient(135deg,var(--wm-navy-mid),var(--wm-accent))}}

/* Services photo cards — light surface, width synced with Warum (1400px) */
.wm3-svc-photo{{background:var(--wm-surface-alt);padding:2.35rem 0}}
.wm3-svc-photo.wm3-section{{padding-left:0;padding-right:0}}
.wm3-svc-photo__inner{{max-width:min(100%,1400px);margin:0 auto;padding:0 clamp(1rem,3vw,2rem);box-sizing:border-box}}
.wm3-svc-photo__head{{text-align:center;max-width:40rem;margin:0 auto 1.85rem}}
.wm3-svc-photo__title{{
  margin:0!important;font-family:var(--wm-font-display)!important;
  font-size:clamp(1.7rem,2.6vw,2.25rem)!important;font-weight:700!important;
  letter-spacing:-0.02em!important;text-transform:none;line-height:1.15!important;
  color:var(--wm-navy)!important;
}}
.wm3-svc-photo__grid{{
  display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
  gap:1.5rem;margin:0;
}}
.wm3-svc-card{{
  position:relative;border-radius:var(--wm-radius,16px);overflow:hidden;min-height:0;
  display:flex;flex-direction:column;text-decoration:none!important;color:inherit;
  border:1px solid rgba(15,23,42,.04);
  box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));background:#fff;
  transition:transform var(--wm-ease),box-shadow var(--wm-ease),border-color var(--wm-ease);
}}
.wm3-svc-card:hover{{
  transform:translateY(-3px);
  box-shadow:var(--wm-shadow-card,0 10px 28px rgba(15,23,42,.08));
  border-color:rgba(232,93,4,.16);
}}
.wm3-svc-card:focus-visible{{outline:2px solid var(--wm-accent);outline-offset:3px}}
.wm3-svc-card__media{{position:relative;overflow:hidden;background:#0b1f33;flex:0 0 auto;line-height:0}}
.wm3-svc-card__img{{width:100%;height:auto;display:block;object-fit:contain;object-position:center top;filter:var(--wm-img-grade);transition:transform .55s cubic-bezier(.22,1,.36,1),filter var(--wm-ease)}}
.wm3-svc-card:hover .wm3-svc-card__img{{transform:scale(1.035)}}
.wm3-svc-card__img--empty{{width:100%;aspect-ratio:16/10;background:linear-gradient(135deg,var(--wm-navy-mid),var(--wm-accent))}}
.wm3-svc-card__body{{
  position:relative;z-index:1;
  display:flex;align-items:flex-start;gap:.8rem;
  padding:1.05rem 1.1rem 1.15rem;background:#fff;
}}
.wm3-svc-card__icon{{
  width:48px;height:48px;border-radius:var(--wm-radius-sm,12px);flex-shrink:0;
  background:var(--wm-surface-alt);color:var(--wm-accent);
  display:flex;align-items:center;justify-content:center;
  box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));
  transition:transform var(--wm-ease),background var(--wm-ease);
}}
.wm3-svc-card:hover .wm3-svc-card__icon{{transform:scale(1.05);background:#fff4ec}}
.wm3-svc-card__icon .wm3-ico{{display:block;margin:0}}
.wm3-svc-card__copy{{min-width:0;flex:1}}
.wm3-svc-card__title{{
  margin:0 0 .35rem!important;color:var(--wm-navy)!important;
  font-family:var(--wm-font-display)!important;font-size:1.08rem!important;
  font-weight:800!important;line-height:1.2!important;letter-spacing:-0.01em;
  transition:color var(--wm-ease);
}}
.wm3-svc-card:hover .wm3-svc-card__title{{color:var(--wm-accent)!important}}
.wm3-svc-card__desc{{
  margin:0;color:var(--wm-muted);font-size:.92rem;line-height:1.55;
}}
.wm3-svc-photo__benefits{{
  display:grid;grid-template-columns:repeat(4,minmax(0,1fr));
  gap:0;align-items:stretch;
  margin:0;padding:1.55rem 0 .15rem;
  border-top:1px solid rgba(15,23,42,.06);
}}
.wm3-svc-photo__benefits > [role="listitem"]{{
  min-width:0;position:relative;
}}
.wm3-svc-photo__benefits > [role="listitem"] + [role="listitem"]::before{{
  content:"";position:absolute;left:0;top:18%;bottom:18%;width:1px;
  background:rgba(15,23,42,.1);
}}
.wm3-svc-benefit{{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:.7rem;text-align:center;padding:1.15rem 1.1rem;min-height:100%;
}}
.wm3-svc-benefit__icon{{
  width:42px;height:42px;border-radius:999px;flex-shrink:0;
  color:var(--wm-accent);background:transparent;
  border:1.5px solid rgba(232,93,4,.45);
  display:flex;align-items:center;justify-content:center;
}}
.wm3-svc-benefit__icon .wm3-ico{{display:block;margin:0}}
.wm3-svc-benefit__title{{
  display:block;margin:0;max-width:11.5rem;
  font-family:var(--wm-font-display);font-size:.98rem;font-weight:700;
  color:var(--wm-navy);line-height:1.25;letter-spacing:-0.01em;
}}

@media(max-width:980px){{
  .wm3-svc-photo__grid{{grid-template-columns:repeat(2,minmax(0,1fr));gap:1.2rem}}
  .wm3-svc-photo__benefits{{grid-template-columns:repeat(2,minmax(0,1fr));gap:0;row-gap:.35rem}}
  .wm3-svc-photo__benefits > [role="listitem"]:nth-child(2n+1)::before{{display:none}}
  .wm3-svc-photo__benefits > [role="listitem"]:nth-child(n+3)::after{{
    content:"";position:absolute;left:12%;right:12%;top:0;height:1px;
    background:rgba(15,23,42,.08);
  }}
}}
@media(max-width:640px){{
  .wm3-svc-photo{{padding:2.75rem 0}}
  .wm3-svc-photo__grid{{grid-template-columns:1fr}}
  .wm3-svc-photo__benefits{{grid-template-columns:1fr 1fr;gap:0}}
  .wm3-svc-benefit{{padding:.95rem .55rem}}
  .wm3-svc-benefit__title{{font-size:.9rem;max-width:none}}
}}

/* Services icon columns */
.wm3-services-icons{{background:var(--wm-surface-alt);padding:3rem 1.5rem}}
.wm3-icon-row{{gap:1.75rem!important}}
.wm3-icon-col{{text-align:center;padding:1rem .5rem}}
.wm3-icon-col__icon{{width:64px;height:64px;margin:0 auto 1rem;border-radius:16px;background:#fff;color:var(--wm-accent);display:flex;align-items:center;justify-content:center;box-shadow:0 8px 24px rgba(15,23,42,.08)}}
.wm3-icon-col__icon .wm3-ico{{display:block;margin:0}}
.wm3-icon-col__title{{font-size:1.15rem;font-weight:700;margin:0 0 .55rem;color:var(--wm-ink)}}
.wm3-icon-col__desc{{color:var(--wm-muted);font-size:.95rem;line-height:1.55;margin:0}}

/* Services cards */
.wm3-services-cards{{background:var(--wm-surface);padding:3rem 1.5rem}}
.wm3-card{{background:#fff;border-radius:var(--wm-radius,16px);padding:1.65rem 1.35rem;border:1px solid rgba(15,23,42,.04);box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));height:100%;text-align:center;transition:transform var(--wm-ease),box-shadow var(--wm-ease)}}
.wm3-card:hover{{transform:translateY(-3px);box-shadow:var(--wm-shadow-card,0 10px 28px rgba(15,23,42,.08))}}
.wm3-card__icon{{width:56px;height:56px;margin:0 auto .85rem;border-radius:var(--wm-radius-sm,12px);background:var(--wm-surface-alt);color:var(--wm-accent);display:flex;align-items:center;justify-content:center;transition:transform var(--wm-ease)}}
.wm3-card:hover .wm3-card__icon{{transform:scale(1.05)}}
.wm3-card__icon .wm3-ico{{display:block;margin:0}}
.wm3-card__title{{font-size:1.15rem;font-weight:700;margin:0 0 .5rem}}
.wm3-card__desc{{color:var(--wm-muted);font-size:.95rem;line-height:1.55;margin:0}}

/* About split rounded */
.wm3-about-split{{background:linear-gradient(180deg,var(--wm-surface-alt) 0%,#fff 100%);padding:3rem 1.5rem}}
.wm3-about-row{{align-items:center!important;gap:2.5rem!important}}
.wm3-rounded-img img{{border-radius:var(--wm-radius,16px)!important;width:100%!important;height:auto!important;display:block;box-shadow:var(--wm-shadow-card,0 10px 28px rgba(15,23,42,.08));filter:var(--wm-img-grade)}}
.wm3-chips{{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:1rem}}
.wm3-chip{{background:#fff;color:var(--wm-ink);border:1px solid #e2e8f0;padding:.4rem .85rem;border-radius:8px;font-size:.85rem;font-weight:600}}

/* Feature stack — Warum-uns synced with section width, equal-height columns */
.wm3-features{{background:#fff;padding:var(--wm-space-section,2.5rem) 0;padding-right:0}}
.wm3-features--warum{{width:100%;max-width:none;margin:0}}
.wm3-features--warum > .wp-block-group__inner-container{{padding:0!important;max-width:none!important;width:100%!important}}
.wm3-warum{{width:100%;max-width:min(100%,1400px);margin:0 auto;padding:0 clamp(1rem,3vw,2rem);box-sizing:border-box}}
.wm3-warum__frame{{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);align-items:stretch;width:100%;min-height:480px;background:var(--wm-surface-alt,#eef2f7);overflow:hidden;border-radius:var(--wm-radius-lg,18px);column-gap:0}}
.wm3-warum__media{{position:relative;margin:0;padding:0;min-height:100%;overflow:hidden;background:#1c2430}}
.wm3-warum__img{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:center center;display:block;border-radius:0;filter:var(--wm-img-grade);transition:transform .7s cubic-bezier(.22,1,.36,1)}}
.wm3-warum__frame:hover .wm3-warum__img{{transform:scale(1.03)}}
.wm3-warum__panel{{display:flex;align-items:center;justify-content:flex-start;height:100%;min-height:100%;background:linear-gradient(135deg,#f8fafc 0%,#eef2f7 100%);padding:clamp(1.5rem,3vw,2.35rem) clamp(1rem,2.2vw,1.65rem) clamp(1.5rem,3vw,2.35rem) clamp(.85rem,1.8vw,1.25rem);box-sizing:border-box}}
.wm3-warum__panel-inner{{width:100%;max-width:none;margin:0}}
.wm3-warum__eyebrow{{margin:0 0 .45rem;color:var(--wm-accent);font-size:.78rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase}}
.wm3-warum__title{{margin:0 0 1.45rem!important;color:var(--wm-navy);font-size:clamp(1.45rem,2.2vw,2.05rem);font-weight:800;line-height:1.18;letter-spacing:-.02em}}
.wm3-warum__grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:0;align-items:start}}
.wm3-warum__col{{padding:0 .75rem;border-left:1px solid rgba(15,23,42,.08);text-align:left}}
.wm3-warum__col:first-child{{padding-left:0;border-left:0}}
.wm3-warum__col:last-child{{padding-right:0}}
.wm3-warum__icon{{width:48px;height:48px;border-radius:50%;background:#fff;color:var(--wm-accent);display:flex;align-items:center;justify-content:center;margin:0 0 .75rem;box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));transition:transform var(--wm-ease)}}
.wm3-warum__col:hover .wm3-warum__icon{{transform:scale(1.06)}}
.wm3-warum__icon .wm3-ico{{display:block;margin:0}}
.wm3-warum__col-title{{margin:0 0 .35rem;color:var(--wm-navy);font-size:.95rem;font-weight:700;line-height:1.3}}
.wm3-warum__col-desc{{margin:0;color:var(--wm-muted);font-size:.84rem;line-height:1.55}}
@media(max-width:960px){{
  .wm3-warum__frame{{grid-template-columns:1fr;min-height:0}}
  .wm3-warum__media{{min-height:0;aspect-ratio:16/10}}
  .wm3-warum__img{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}}
  .wm3-warum__panel{{height:auto;min-height:0}}
  .wm3-warum__grid{{grid-template-columns:repeat(2,minmax(0,1fr));gap:1.35rem 0}}
  .wm3-warum__col{{border-left:0;padding:0 1rem 0 0}}
  .wm3-warum__col:nth-child(even){{padding:0 0 0 1rem}}
}}
@media(max-width:560px){{
  .wm3-warum__grid{{grid-template-columns:1fr;gap:1.25rem}}
  .wm3-warum__col,.wm3-warum__col:nth-child(even){{padding:0}}
  .wm3-warum__media{{aspect-ratio:4/3}}
}}

/* Legacy feature-stack rows (kept for older pages) */
.wm3-feature-photo img{{border-radius:14px;width:100%;height:auto;display:block;object-fit:cover;max-height:520px}}
.wm3-feature-stack{{display:flex;flex-direction:column;gap:.85rem}}
.wm3-feature-row{{display:flex;gap:1rem;align-items:flex-start;background:#fff;border:1px solid #e5e7eb;border-left:4px solid var(--wm-accent);border-radius:10px;padding:1rem 1.1rem;box-shadow:0 2px 10px rgba(0,0,0,.04)}}
.wm3-feature-row__icon{{width:36px;height:36px;border-radius:10px;background:var(--wm-accent);color:#fff;display:flex;align-items:center;justify-content:center;flex-shrink:0}}
.wm3-feature-row__icon .wm3-ico{{display:block;margin:0}}
.wm3-feature-row__body strong{{display:block;margin:0 0 .25rem;color:var(--wm-ink)}}
.wm3-feature-row__body p{{margin:0;color:var(--wm-muted);font-size:.95rem;line-height:1.5}}

/* Process — live step cards over photo (not a baked strip in a white box) */
.wm3-process{{
  position:relative;isolation:isolate;overflow:hidden;
  background-color:#f4f1ec;
  background-image:var(--wm-band-bg,none);
  background-size:cover;background-position:center center;background-repeat:no-repeat;
  padding:clamp(2.5rem,4.2vw,3.35rem) 0;
}}
.wm3-process.wm3-section{{padding-left:0;padding-right:0}}
.wm3-process__wash{{
  position:absolute;inset:0;z-index:0;pointer-events:none;
  background:
    linear-gradient(90deg,
      rgba(255,255,255,.12) 0%,
      rgba(255,255,255,.45) 16%,
      rgba(255,255,255,.78) 36%,
      rgba(255,255,255,.86) 50%,
      rgba(255,255,255,.78) 64%,
      rgba(255,255,255,.45) 84%,
      rgba(255,255,255,.12) 100%),
    linear-gradient(180deg,rgba(255,255,255,.28) 0%,rgba(255,255,255,.1) 48%,rgba(255,255,255,.32) 100%);
}}
.wm3-process__inner{{
  position:relative;z-index:1;
  max-width:min(100%,1400px);margin:0 auto;
  padding:0 clamp(1rem,3vw,2rem);box-sizing:border-box;
}}
.wm3-process__head{{text-align:center;max-width:48rem;margin:0 auto var(--wm-space-head,1.55rem);padding:0}}
.wm3-process__label{{margin:0 0 .45rem;color:var(--wm-accent);font-size:.78rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase}}
.wm3-process__title{{margin:0 0 .55rem!important}}
.wm3-process__sub{{margin:0;color:var(--wm-muted);font-size:1.05rem;line-height:1.6}}
.wm3-process__grid{{
  list-style:none;margin:0;padding:0;counter-reset:none;
  display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1.25rem;
}}
.wm3-process__card{{
  position:relative;display:flex;flex-direction:column;align-items:center;text-align:center;
  margin:0;padding:1.2rem 1.05rem 1.25rem;
  background:rgba(255,255,255,.96);border-radius:var(--wm-radius,16px);
  border:1px solid rgba(15,23,42,.04);
  box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));
  transition:transform var(--wm-ease),box-shadow var(--wm-ease),border-color var(--wm-ease);
}}
.wm3-process__card:hover{{
  transform:translateY(-3px);
  box-shadow:var(--wm-shadow-card,0 10px 28px rgba(15,23,42,.08));
  border-color:rgba(232,93,4,.16);
}}
.wm3-process__num{{
  display:inline-flex;align-items:center;justify-content:center;
  min-width:2.5rem;height:2.5rem;padding:0 .55rem;margin:0 0 .55rem;
  border-radius:999px;background:#fff4ec;color:var(--wm-accent);
  font-family:var(--wm-font-display);font-size:1.02rem;font-weight:800;letter-spacing:.02em;
  box-shadow:inset 0 0 0 1px rgba(232,93,4,.14);
  transition:transform var(--wm-ease);
}}
.wm3-process__card:hover .wm3-process__num{{transform:scale(1.05)}}
.wm3-process__visual{{
  width:100%;max-width:17rem;margin:0 auto .75rem;line-height:0;
  border-radius:var(--wm-radius-sm,12px);overflow:hidden;background:#fff;
}}
.wm3-process__visual-img{{width:100%;height:auto;display:block;object-fit:contain;filter:var(--wm-img-grade);transition:transform .55s cubic-bezier(.22,1,.36,1)}}
.wm3-process__card:hover .wm3-process__visual-img{{transform:scale(1.03)}}
.wm3-process__card-title{{
  margin:0 0 .4rem!important;
  font-family:var(--wm-font-display)!important;
  font-size:clamp(1.2rem,1.5vw,1.38rem)!important;font-weight:800!important;
  color:var(--wm-navy)!important;letter-spacing:-.02em;line-height:1.2!important;
}}
.wm3-process__card-desc{{
  margin:0;color:var(--wm-muted);font-size:.95rem;line-height:1.55;font-weight:500;
  max-width:22rem;
}}
.wm3-process-row{{gap:1.25rem!important;margin:1rem auto 0!important;padding:0 clamp(1rem,3vw,2rem);max-width:1400px;align-items:stretch!important}}
.wm3-step{{text-align:center;padding:.2rem .85rem .4rem}}
.wm3-step__num{{width:52px;height:52px;border-radius:50%;background:var(--wm-accent);color:#fff;font-size:1.35rem;font-weight:800;display:flex;align-items:center;justify-content:center;margin:0 auto 1rem}}
.wm3-step__title{{font-size:clamp(1.28rem,1.6vw,1.42rem);font-weight:800;margin:0 0 .5rem;color:var(--wm-ink);text-align:center;letter-spacing:-0.02em}}
.wm3-step__desc{{color:var(--wm-muted);font-size:.96rem;line-height:1.55;margin:0 auto;max-width:20rem;text-align:center;font-weight:500}}
.screen-reader-text{{border:0;clip:rect(1px,1px,1px,1px);clip-path:inset(50%);height:1px;margin:-1px;overflow:hidden;padding:0;position:absolute;width:1px;word-wrap:normal!important}}
@media(max-width:900px){{
  .wm3-process__grid{{grid-template-columns:1fr;gap:.9rem;max-width:26rem;margin:0 auto}}
  .wm3-process__wash{{
    background:linear-gradient(180deg,rgba(255,255,255,.7) 0%,rgba(255,255,255,.86) 100%);
  }}
}}

/* Before / After — shared white band; picture strip edge-to-edge */
.wm3-ba{{background:#fff;padding:1rem 0 0;overflow:hidden}}
.wm3-ba.wm3-section{{padding-left:0;padding-right:0}}
.wm3-ba__head{{text-align:center;max-width:44rem;margin:0 auto 1.1rem;padding:0 1.25rem}}
.wm3-ba__label{{margin:0 0 .55rem;color:var(--wm-accent);font-size:.78rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase}}
.wm3-ba__title{{margin:0 0 .55rem!important}}
.wm3-ba__sub{{margin:0;color:var(--wm-muted);font-size:1.05rem;line-height:1.55}}
.wm3-ba__visual{{width:100vw;max-width:100vw;margin:0 0 1.25rem;margin-left:calc(50% - 50vw);margin-right:calc(50% - 50vw);padding:0}}
.wm3-ba__img{{width:100%;max-width:none;height:auto;display:block;margin:0;border-radius:0}}
.wm3-ba__actions{{display:flex;flex-wrap:wrap;gap:.85rem;justify-content:center;margin:0 0 1.15rem;padding:0 1.25rem}}
.wm3-ba__btn{{display:inline-flex;align-items:center;justify-content:center;gap:.45rem;padding:.95rem 1.35rem;border-radius:12px;font-weight:700;font-size:.98rem;text-decoration:none!important;line-height:1.2;transition:transform .15s ease,box-shadow .15s ease}}
.wm3-ba__btn--primary{{background:var(--wm-accent);color:#fff!important;box-shadow:0 10px 28px rgba(232,93,4,.28)}}
.wm3-ba__btn--primary:hover{{transform:translateY(-1px);background:var(--wm-accent-dark)}}
.wm3-ba__btn--ghost{{background:#fff;color:var(--wm-accent)!important;border:2px solid var(--wm-accent)}}
.wm3-ba__btn--ghost:hover{{transform:translateY(-1px);background:#fff7f0}}
.wm3-ba__trust{{list-style:none;display:flex;flex-wrap:wrap;gap:.55rem 1.35rem;justify-content:center;margin:0;padding:0 1.25rem;color:var(--wm-muted);font-size:.9rem}}
.wm3-ba__trust li{{display:inline-flex;align-items:center;gap:.4rem}}
.wm3-ba__trust li::before{{content:"✓";color:var(--wm-accent);font-weight:800}}
@media(max-width:640px){{
  .wm3-ba__actions{{flex-direction:column;align-items:stretch}}
  .wm3-ba__btn{{width:100%}}
}}

/* Trust */
.wm3-trust{{background:var(--wm-accent);padding:2.5rem 1.5rem}}
.wm3-trust-item{{text-align:center;color:#fff}}
.wm3-trust-value{{font-size:1.35rem;font-weight:800;margin:0 0 .3rem}}
.wm3-trust-label{{font-size:.85rem;opacity:.9;text-transform:uppercase;letter-spacing:.4px}}

/* CTA — blend with site navy / softer edges */
.wm3-cta{{
  position:relative;overflow:hidden;
  background:linear-gradient(165deg,var(--wm-navy) 0%,var(--wm-navy-mid) 55%,#1a3a56 100%);
  padding:clamp(2.5rem,4vw,3.15rem) 1.5rem;color:#fff;text-align:center;
}}
.wm3-cta::before{{
  content:"";position:absolute;left:0;right:0;top:0;height:2.25rem;pointer-events:none;
  background:linear-gradient(180deg,rgba(255,255,255,.1),transparent);
}}
.wm3-cta__title{{color:#fff!important;max-width:40rem;margin-left:auto!important;margin-right:auto!important;line-height:1.18!important;letter-spacing:-.02em}}
.wm3-cta__sub{{color:rgba(255,255,255,.88)!important;max-width:36rem;margin:.65rem auto 0!important;line-height:1.6!important}}
.wm3-cta .wp-block-buttons{{justify-content:center!important;gap:0!important;margin-top:1.45rem}}
.wm3-cta .wp-block-button{{margin:0!important}}
.wm3-cta .wp-block-button__link{{
  background:var(--wm-accent)!important;border-color:var(--wm-accent)!important;color:#fff!important;
  border-radius:var(--wm-radius-sm,12px)!important;padding:.95rem 1.45rem!important;font-weight:700!important;
  letter-spacing:.01em;box-shadow:0 8px 22px rgba(232,93,4,.28);
  transition:transform var(--wm-ease),box-shadow var(--wm-ease),background var(--wm-ease)!important;
}}
.wm3-cta .wp-block-button__link:hover{{transform:translateY(-2px);box-shadow:0 12px 28px rgba(232,93,4,.36)}}
.wm3-cta .wp-block-button.is-style-outline .wp-block-button__link{{
  background:var(--wm-accent)!important;border-color:var(--wm-accent)!important;color:#fff!important;
}}

/* Gallery / reviews / faq / contact */
.wm3-gallery{{background:var(--wm-surface);padding:2.35rem 1.5rem;height:220px!important;object-fit:cover!important;border-radius:10px!important}}
.wm3-reviews{{background:#fff4ec;padding:2.35rem 1.5rem;border-radius:12px;padding:1.5rem;box-shadow:0 2px 14px rgba(0,0,0,.06);height:100%}}
.wm3-review__stars{{color:#f59e0b;margin:0 0 .6rem}}
.wm3-review__text{{font-style:italic;color:#333;line-height:1.55}}
.wm3-review__author{{color:#666;font-weight:600;font-size:.9rem;margin-top:.75rem}}
.wm3-faq__q{{border-left:3px solid var(--wm-accent);padding-left:1rem;margin:1.4rem 0 .4rem!important}}
.wm3-faq__a{{padding-left:1rem;color:#444}}
.wm3-contact-wrap{{display:none}}
.wm3-kontakt{{
  background:#fff;padding:clamp(3.2rem,5.5vw,4.8rem) 0 clamp(3.6rem,6vw,5.2rem);margin:0;
}}
.wm3-kontakt.wm3-section{{padding-left:0;padding-right:0}}
.wm3-kontakt__inner{{
  max-width:min(100%,1180px);margin:0 auto;padding:0 clamp(1.15rem,3vw,2rem);box-sizing:border-box;
}}
.wm3-kontakt__grid{{
  display:grid;grid-template-columns:minmax(260px,.92fr) minmax(0,1.35fr);
  gap:clamp(1.75rem,3.5vw,3rem);align-items:stretch;margin:0 0 clamp(2rem,3.5vw,2.75rem);
}}
.wm3-kontakt__rail{{
  background:linear-gradient(165deg,#0b1f33 0%,#143049 55%,#0f2740 100%);
  color:#fff;border-radius:24px;padding:clamp(1.75rem,2.8vw,2.35rem) clamp(1.4rem,2.2vw,1.85rem);
  display:flex;flex-direction:column;box-shadow:0 18px 48px rgba(11,31,51,.22);
}}
.wm3-kontakt__rail-title{{
  margin:0 0 1.15rem!important;padding:0 0 1rem;font-family:var(--wm-font-display)!important;
  font-size:clamp(1.2rem,1.8vw,1.4rem)!important;font-weight:700!important;color:#fff!important;
  line-height:1.25!important;border-bottom:1px solid rgba(255,255,255,.14);
}}
.wm3-kontakt__list{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:1.2rem;flex:1}}
.wm3-kontakt__item{{display:flex;align-items:flex-start;gap:.9rem;margin:0}}
.wm3-kontakt__ico{{
  width:2.55rem;height:2.55rem;border-radius:999px;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;
  background:var(--wm-accent);color:#fff;
  box-shadow:0 8px 20px rgba(232,93,4,.28);
}}
.wm3-kontakt__ico .wm3-ico{{display:block;margin:0;color:#fff}}
.wm3-kontakt__item-copy{{display:flex;flex-direction:column;gap:.2rem;min-width:0;padding-top:.15rem}}
.wm3-kontakt__item-label{{
  font-family:var(--wm-font-display);font-size:.92rem;font-weight:700;color:#fff;line-height:1.25;
}}
.wm3-kontakt__item-value{{
  font-size:.92rem;line-height:1.5;color:rgba(255,255,255,.82);font-weight:400;word-break:break-word;
}}
.wm3-kontakt__item-value a{{color:rgba(255,255,255,.92);text-decoration:none}}
.wm3-kontakt__item-value a:hover{{color:#fff;text-decoration:underline}}
.wm3-kontakt__wa{{font-weight:700;text-decoration:underline!important;text-underline-offset:3px}}
.wm3-kontakt__social{{margin-top:auto;padding-top:1.5rem}}
.wm3-kontakt__social-title{{
  margin:0 0 .85rem;padding:0 0 .75rem;font-family:var(--wm-font-display);
  font-size:.92rem;font-weight:700;color:#fff;border-bottom:1px solid rgba(255,255,255,.14);
}}
.wm3-kontakt__social-row{{display:flex;align-items:center;gap:.7rem}}
.wm3-kontakt__social-link{{
  width:2.55rem;height:2.55rem;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;
  background:var(--wm-accent);color:#fff;text-decoration:none;
  box-shadow:0 8px 20px rgba(232,93,4,.28);
  transition:transform .2s ease,background .2s ease;
}}
.wm3-kontakt__social-link:hover{{transform:translateY(-2px);background:var(--wm-accent-dark)}}
.wm3-kontakt__social-link .wm3-ico{{display:block;margin:0;color:#fff}}
.wm3-kontakt__main{{min-width:0;display:flex;flex-direction:column}}
.wm3-kontakt__head{{margin:0 0 1.35rem}}
.wm3-kontakt__title{{
  margin:0 0 .85rem!important;font-family:var(--wm-font-display)!important;
  font-size:clamp(1.85rem,3.2vw,2.55rem)!important;font-weight:800!important;
  color:var(--wm-navy)!important;line-height:1.15!important;letter-spacing:-.03em;
}}
.wm3-kontakt__intro{{
  margin:0;max-width:42rem;color:var(--wm-muted);font-size:1.02rem;line-height:1.65;font-weight:400;
}}
.wm3-kontakt__form{{
  background:#F7F4EF;border-radius:24px;padding:clamp(1.45rem,2.6vw,2rem);
  box-shadow:0 10px 32px rgba(15,23,42,.04);
}}
.wm3-kontakt__fields{{
  display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
  gap:1rem 1.05rem;margin:0 0 1.15rem;
}}
.wm3-kontakt__field{{display:block;margin:0;min-width:0}}
.wm3-kontakt__field--full{{grid-column:1/-1}}
.wm3-kontakt__sr{{
  position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0,0,0,0);white-space:nowrap;border:0;
}}
.wm3-kontakt__field input,
.wm3-kontakt__field textarea{{
  width:100%;box-sizing:border-box;margin:0;padding:.95rem 1.1rem;
  border:1px solid rgba(15,23,42,.06);border-radius:14px;background:#fff;
  color:var(--wm-navy);font:inherit;font-size:.98rem;line-height:1.4;
  box-shadow:0 1px 2px rgba(15,23,42,.03);
  transition:border-color .2s ease,box-shadow .2s ease;
}}
.wm3-kontakt__field textarea{{resize:vertical;min-height:8.5rem}}
.wm3-kontakt__field input::placeholder,
.wm3-kontakt__field textarea::placeholder{{color:#94a3b8}}
.wm3-kontakt__field input:focus,
.wm3-kontakt__field textarea:focus{{
  outline:none;border-color:rgba(232,93,4,.45);
  box-shadow:0 0 0 3px rgba(232,93,4,.12);
}}
.wm3-kontakt__consent{{
  display:flex;align-items:flex-start;gap:.65rem;margin:0 0 1.25rem;
  color:var(--wm-muted);font-size:.88rem;line-height:1.5;cursor:pointer;
}}
.wm3-kontakt__consent input{{margin:.2rem 0 0;accent-color:var(--wm-accent);flex-shrink:0}}
.wm3-kontakt__consent a{{color:var(--wm-accent);font-weight:700;text-decoration:none}}
.wm3-kontakt__consent a:hover{{text-decoration:underline}}
.wm3-kontakt__submit{{
  display:block;width:100%;box-sizing:border-box;margin:0;padding:1rem 1.5rem;
  border:none;border-radius:999px;background:var(--wm-accent);color:#fff;
  font-family:var(--wm-font-display);font-size:1.05rem;font-weight:700;letter-spacing:.01em;
  cursor:pointer;box-shadow:0 12px 28px rgba(232,93,4,.28);
  transition:transform .2s ease,background .2s ease,box-shadow .2s ease;
}}
.wm3-kontakt__submit:hover{{
  background:var(--wm-accent-dark);transform:translateY(-2px);
  box-shadow:0 16px 34px rgba(232,93,4,.34);
}}
.wm3-kontakt__map-wrap{{
  border-radius:22px;overflow:hidden;box-shadow:0 14px 40px rgba(15,23,42,.08);
  border:1px solid rgba(15,23,42,.05);background:var(--wm-surface-alt);min-height:320px;
}}
.wm3-kontakt__map{{
  display:block;width:100%;height:clamp(280px,40vw,420px);border:0;
}}
@media(max-width:900px){{
  .wm3-kontakt__grid{{grid-template-columns:1fr;gap:1.5rem}}
  .wm3-kontakt__rail{{order:2}}
  .wm3-kontakt__main{{order:1}}
}}
@media(max-width:560px){{
  .wm3-kontakt__fields{{grid-template-columns:1fr}}
  .wm3-kontakt__form{{padding:1.2rem 1.05rem}}
  .wm3-kontakt__map{{height:260px}}
}}


@media(max-width:782px){{
  .wm3-hero-overlay,.wm3-hero-bleed{{min-height:min(82vh,620px);padding:3rem 1.25rem;background-size:cover;background-position:42% 48%}}
  .wm3-hero-bleed__title{{max-width:none}}
  .wm3-hero-bleed__badges{{grid-template-columns:1fr;max-width:20rem}}
  .wm3-hero-bleed__badges li{{white-space:normal}}
  .wm3-hero-split__inner{{grid-template-columns:1fr;min-height:0}}
  .wm3-hero-split__copy{{max-width:none;margin:0;padding:2.5rem 1.35rem}}
  .wm3-hero-split__media{{min-height:280px}}
  .wm3-about-row,.wm3-feature-row-cols,.wm3-icon-row,.wm3-process-row,
  .wm3-trust-row,.wm3-gallery-row,.wm3-reviews-row,.wm3-contact-row,
  .wm3-hero-split__row,.wm3-grid-row{{display:block!important}}
  .wm3-about-row .wp-block-column,.wm3-feature-row-cols .wp-block-column,
  .wm3-icon-row .wp-block-column,.wm3-process-row .wp-block-column,
  .wm3-trust-row .wp-block-column,.wm3-gallery-row .wp-block-column,
  .wm3-reviews-row .wp-block-column,.wm3-contact-row .wp-block-column,
  .wm3-hero-split__row .wp-block-column,.wm3-grid-row .wp-block-column{{width:100%!important;flex-basis:100%!important;margin-bottom:1.25rem}}
}}

/* Final polish — unified premium system (no redesign) */
.wm3-svc-photo__head,.wm3-trust-live__head,.wm3-process__head,.mba-head,.wm3-ba__head{{
  margin-bottom:var(--wm-space-head,1.55rem);
}}
.wm3-svc-photo,.wm3-features,.wm3-mba,.wm3-ba{{
  position:relative;
}}
.wm3-svc-photo::before,.wm3-features::before,.wm3-mba::before{{
  content:"";position:absolute;left:0;right:0;top:0;height:1.75rem;pointer-events:none;z-index:0;
  background:linear-gradient(180deg,rgba(15,23,42,.025),transparent);
}}
.wm3-svc-photo__inner,.wm3-warum,.wm3-mba .mba-wrap,.wm3-process__inner,.wm3-trust-live__inner{{
  position:relative;z-index:1;
}}
.wm3-hero-bleed__media img,.wm3-hero-split__media img,.wm3-ba__img{{
  filter:var(--wm-img-grade);
}}
.wm3-icon-col__icon{{
  border-radius:var(--wm-radius,16px);
  box-shadow:var(--wm-shadow-soft,0 6px 18px rgba(15,23,42,.05));
  transition:transform var(--wm-ease),box-shadow var(--wm-ease);
}}
.wm3-icon-col:hover .wm3-icon-col__icon{{transform:scale(1.05);box-shadow:var(--wm-shadow-card,0 10px 28px rgba(15,23,42,.08))}}
.wm3-svc-benefit__icon{{transition:transform var(--wm-ease),border-color var(--wm-ease),background var(--wm-ease)}}
.wm3-svc-benefit:hover .wm3-svc-benefit__icon{{transform:scale(1.06);background:#fff4ec;border-color:rgba(232,93,4,.55)}}
.wm3-brand{{transition:opacity var(--wm-ease)}}
.wm3-brand:hover{{opacity:.92}}
.wm3-hero-bleed__softlink{{transition:color var(--wm-ease),transform var(--wm-ease)}}
.wm3-hero-bleed__softlink:hover{{transform:translateX(2px)}}
.wm3-hero-bleed__badges li{{transition:transform var(--wm-ease),background var(--wm-ease)}}
.wm3-hero-bleed__badges li:hover{{transform:translateY(-1px);background:rgba(8,20,36,.55)}}
.wm3-step{{transition:transform var(--wm-ease)}}
.wm3-step:hover{{transform:translateY(-2px)}}
.wm3-warum__col{{transition:transform var(--wm-ease)}}
.wm3-warum__col:hover{{transform:translateY(-2px)}}
.wm3-svc-benefit{{transition:transform var(--wm-ease)}}
.wm3-svc-benefit:hover{{transform:translateY(-2px)}}
.mba-trust__item{{border-radius:var(--wm-radius,16px)}}
.wm3-ba__btn{{
  border-radius:var(--wm-radius-sm,12px);
  transition:transform var(--wm-ease),box-shadow var(--wm-ease),background var(--wm-ease);
}}
.wm3-ba__btn--primary{{box-shadow:0 8px 22px rgba(232,93,4,.28)}}
.wm3-ba__btn--primary:hover{{transform:translateY(-2px);box-shadow:0 12px 28px rgba(232,93,4,.36)}}
@media(prefers-reduced-motion:reduce){{
  .wm3-svc-card:hover .wm3-svc-card__img,
  .wm3-process__card:hover .wm3-process__visual-img,
  .wm3-warum__frame:hover .wm3-warum__img{{transform:none}}
}}
{polish}
</style>
<!-- /wp:html -->"""
