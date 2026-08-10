"""
webmaker.agents.website_modernizer.content_mapper
=================================================
Claude-powered intelligent content mapping.

Takes the Design Blueprint (from Design Library study) + Website Package and
produces a structured content map for the page builder.
"""

from __future__ import annotations

import json
import re
from typing import Any

from webmaker.agents.website_modernizer.design_blueprint import blueprint_to_prompt
from webmaker.core.logging import get_logger
from webmaker.core.types import AIProvider

log = get_logger("modernizer.content_mapper")

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a Senior UI/UX Designer and Creative Director at a premium web agency.
The client paid €5,000 for a complete website redesign.

You are NOT a website migration AI.
You do NOT rebuild the old HTML layout.
You populate the selected WordPress template using:
1. The Design Blueprint (chosen Design Library references + reasons)
2. The client's existing business information
3. The theme/template design system

MINDSET:
- Old website = business information only
- Design Library = visual pattern inspiration (never copy branding/colors/copy)
- Selected template = foundation to populate
- Goal: premium, trustworthy, modern first impression

ALLOWED:
- Rewrite headings for readability (facts stay exact)
- Merge duplicates / split long paragraphs into cards, features, timelines
- Move content into stronger sections (hero CTA, service cards, process steps)
- Place client images intentionally (hero / about / gallery / CTA)
- Improve hierarchy, whitespace intent, and UX flow

NOT ALLOWED:
- Invent services, history, statistics, reviews, employees, certifications, locations
- Copy Design Library branding, colors, logos, or wording
- SEO keyword stuffing / meta optimization (later agents)
- English translation — keep ALL client-facing text in the original language

Follow the Design Blueprint section order and layout_notes when building pages.
Skip blueprint sections marked include=false unless client content clearly supports them.
Only include testimonials/reviews if the client package already has real review text.

LAYOUT VARIANTS (required on every section):
  Set "layout_variant" from the blueprint layout_notes:
  - hero: "overlay_card" (full-bleed photo + floating white card) OR "split"
  - services_grid: "icon_columns" (no heavy cards) OR "cards"
  - text_block: "split_rounded" OR "feature_stack"
  - process: "numbered"
  - gallery: "grid"
  - cta_banner: "band"
  - reviews: "band_cards"
  - contact_info: "split_info"
  - faq: "accordion_list"
  - trust_bar: "stats"

IMAGES (critical):
  Only use filenames listed under Available Local Images in the package.
  Never invent names like hero-team.jpg or gallery-1.jpg.
  If unsure, omit "image" / "images" and the builder will pick real files.

PAGES TO BUILD (always all five):
  homepage, services, about, contact, faq

OUTPUT FORMAT:
Return ONLY a single valid JSON object. No markdown fences. No commentary.
Schema:
{
  "site_title": "short site title",
  "mapping_summary": "one sentence: design approach + what was mapped",
  "design_notes": "2 sentences on how library patterns informed the layout",
  "pages": {
    "homepage": {
      "title": "nav label (short)",
      "meta_description": "1-2 sentence page description",
      "sections": [ <section objects — see below> ]
    },
    "services": { "title": "...", "meta_description": "...", "sections": [...] },
    "about":    { "title": "...", "meta_description": "...", "sections": [...] },
    "contact":  { "title": "...", "meta_description": "...", "sections": [...] },
    "faq":      { "title": "...", "meta_description": "...", "sections": [...] }
  }
}

SECTION TYPES (use only these — always include layout_variant + design_ref):
  hero:          { "type":"hero", "layout_variant":"overlay_card",
                   "heading":"...", "subheading":"...",
                   "cta_label":"...", "cta_url":"#kontakt",
                   "phone":"...", "trust_line":"...",
                   "image":"REAL_FILENAME_FROM_PACKAGE",
                   "design_ref":"hero/Source" }

  services_grid: { "type":"services_grid", "layout_variant":"icon_columns",
                   "heading":"...",
                   "items": [{"title":"...","description":"..."}],  # icons chosen by page builder
                   "design_ref":"services/Source" }

  process:       { "type":"process", "layout_variant":"numbered", "heading":"...",
                   "steps": [{"step":1,"title":"...","description":"..."}],
                   "design_ref":"process/Source" }

  text_block:    { "type":"text_block", "layout_variant":"split_rounded",
                   "heading":"...", "text":"...",
                   "image":"REAL_FILENAME", "image_side":"left|right",
                   "items": [{"title":"..."}],
                   "design_ref":"about/Source" }

  trust_bar:     { "type":"trust_bar", "layout_variant":"stats",
                   "items": [{"label":"...","value":"..."}] }

  gallery:       { "type":"gallery", "layout_variant":"grid", "heading":"...",
                   "images": ["REAL_FILENAME1","REAL_FILENAME2"],
                   "design_ref":"gallery/Source" }

  cta_banner:    { "type":"cta_banner", "layout_variant":"band",
                   "heading":"...", "subheading":"...",
                   "cta_label":"...", "cta_url":"#kontakt", "phone":"...",
                   "design_ref":"cta/Source" }

  reviews:       { "type":"reviews", "layout_variant":"band_cards", "heading":"...",
                   "items": [{"author":"...","text":"...","rating":5}],
                   "design_ref":"testimonials/Source" }

  contact_info:  { "type":"contact_info", "layout_variant":"split_info",
                   "heading":"...",
                   "phone":"...", "email":"...", "address":"...",
                   "hours":"...", "cta_label":"...", "cta_url":"tel:...",
                   "design_ref":"contact/Source" }

  faq:           { "type":"faq", "layout_variant":"accordion_list", "heading":"...",
                   "items": [{"question":"...","answer":"..."}],
                   "design_ref":"faq/Source" }

HOMEPAGE (premium agency flow):
  hero(overlay_card) → services_grid(icon_columns) → process → [trust_bar]
  → text_block(feature_stack or split_rounded) → [gallery] → cta_banner

SERVICES:
  hero (overlay_card or split) → services_grid → process → cta_banner

ABOUT:
  hero (split) → text_block(split_rounded) → [trust_bar] → cta_banner

CONTACT:
  hero (split) → contact_info → cta_banner

FAQ:
  hero (split) → faq → cta_banner
"""

_SCHEMA_HINT = (
    '{"site_title":"...","mapping_summary":"...","design_notes":"...",'
    '"pages":{'
    '"homepage":{"title":"...","meta_description":"...","sections":[...]},'
    '"services":{"title":"...","meta_description":"...","sections":[...]},'
    '"about":{"title":"...","meta_description":"...","sections":[...]},'
    '"contact":{"title":"...","meta_description":"...","sections":[...]},'
    '"faq":{"title":"...","meta_description":"...","sections":[...]}'
    "}}"
)


def map_content(
    context: str,
    *,
    router: Any,
    theme_id: str = "kadence",
    template_id: str = "",
    blueprint: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Call Claude with package context + Design Blueprint; return content map."""
    blueprint_block = blueprint_to_prompt(blueprint) if blueprint else ""
    prompt = (
        f"Populate a premium WordPress demo for theme '{theme_id or 'kadence'}'"
        + (f" / template '{template_id}'" if template_id else "")
        + ".\n\n"
        + (blueprint_block + "\n\n" if blueprint_block else "")
        + "## Website Package\n"
        + context
        + "\n\nReturn ONLY the JSON content map. No markdown."
    )

    try:
        resp = router.request(
            prompt,
            provider=AIProvider.CLAUDE,
            system=_SYSTEM,
            task="website_modernize",
            temperature=0.35,
            max_tokens=8192,
        )
        raw = resp.text or ""
    except Exception as exc:  # noqa: BLE001
        log.error("Claude call failed: {e}", e=exc)
        return None

    parsed = _parse(raw)
    if parsed:
        log.info(
            "Content map parsed — pages={n} summary={s!r}",
            n=len((parsed.get("pages") or {}).keys()),
            s=parsed.get("mapping_summary", "")[:80],
        )
        return parsed

    log.warning("First parse failed ({n} chars) — retrying with repair hint", n=len(raw))
    try:
        retry_resp = router.request(
            (
                "Your previous response was not valid JSON. "
                "Return ONLY a single compact JSON object following this schema exactly, "
                "with no markdown fences, no commentary:\n"
                f"{_SCHEMA_HINT}\n\n"
                "Your prior (invalid) output was:\n"
                f"{raw[:4000]}"
            ),
            provider=AIProvider.CLAUDE,
            system=_SYSTEM,
            task="website_modernize",
            temperature=0.1,
            max_tokens=8192,
        )
        parsed = _parse(retry_resp.text or "")
    except Exception as exc:  # noqa: BLE001
        log.error("Repair retry failed: {e}", e=exc)
        return None

    if not parsed:
        log.error("Content mapping failed after repair retry; will use fallback")
    return parsed


def _parse(raw: str) -> dict[str, Any] | None:
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if "pages" not in data or not isinstance(data["pages"], dict):
        return None
    return data
