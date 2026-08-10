"""
webmaker.agents.website_modernizer.design_blueprint
===================================================
Step 1–2 of Agent 1: study the Design Library and produce a Design Blueprint.

Claude (as Creative Director) picks the best reference screenshot per section
and explains why — then lists which client content slots feed that section.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from webmaker.agents.website_modernizer.library_index import (
    SECTION_KEYS,
    DesignLibraryCatalog,
    DesignReference,
)
from webmaker.core.logging import get_logger
from webmaker.core.types import AIProvider

log = get_logger("modernizer.blueprint")

_SYSTEM = """\
You are a Senior UI/UX Designer and Creative Director at a premium web agency.
The client paid €5,000 for a complete website redesign.

You are NOT a migrator. You do NOT rebuild the old layout.
You combine three inputs:
1. Client Website Package (business facts + content + images)
2. Selected WordPress theme + starter template (design system)
3. WebMaker Design Library screenshots (VISION) + text catalog

VISION TASK (critical):
Attached images are Design Library section screenshots.
Study each screenshot visually for:
- layout composition and whitespace
- visual hierarchy and typography scale
- card / grid arrangement
- CTA placement
- image composition
- trust / social-proof patterns
Never copy branding, colors, logos, or wording from the screenshots.
Only learn UI/UX patterns.

RULES:
- Design Library entries are INSPIRATION only — never copy branding, colors, logos, or copy.
- Never invent services, stats, reviews, certifications, employees, or locations.
- Pick the best reference for EACH section independently — prefer variety across sources.
- Match niche when possible (junk removal / local service / cleaning / handyman / restoration).
- Keep ALL user-facing client content language as-is (usually German).
- In "reason" and "layout_notes", mention what you SAW in the screenshot (spacing, cards, CTA, etc.).
- Respond with ONE valid JSON object only — no markdown fences.

OUTPUT SCHEMA:
{
  "creative_direction": "2-3 sentences: overall design intent for this client",
  "vision_summary": "2-3 sentences: what the Design Library screenshots taught you visually",
  "sections": [
    {
      "section": "hero|services|about|features|process|gallery|testimonials|faq|cta|contact|footer",
      "reference": "section/SourceName",
      "reference_path": "Library/section/SourceName/screenshot.png",
      "reason": "why this reference fits — cite visual patterns you observed",
      "layout_notes": "what visual pattern to learn (cards, split hero, timeline, …)",
      "client_content": {
        "heading": "from client package or empty",
        "subheading": "",
        "body": "",
        "cta": "",
        "images": ["filename or empty"],
        "items": ["service or feature labels from client only"]
      },
      "include": true
    }
  ]
}

Include sections that the client content can support.
Set "include": false (still pick a reference) when the client has no material for that section —
the page builder will skip it.
Always cover at least: hero, services, process (if steps exist), cta, contact, footer.
"""

# Max screenshots per Claude vision call (cost / latency / token limits).
_MAX_VISION_IMAGES = 18
_VISION_PRIORITY_SECTIONS: tuple[str, ...] = (
    "hero", "services", "process", "about", "features",
    "gallery", "testimonials", "cta", "contact", "faq", "footer",
)


def select_vision_screenshots(
    catalog: DesignLibraryCatalog,
    *,
    business_context: str = "",
    max_images: int = _MAX_VISION_IMAGES,
) -> list[str]:
    """Pick Design Library screenshot paths for Claude vision during Migrate.

    Prefers niche-relevant sources (e.g. junk removal) and covers primary
    sections first, with variety across sources when possible.
    """
    ctx_low = business_context.lower()
    junkish = any(
        k in ctx_low
        for k in ("entrümpel", "entruempel", "junk", "haushaltsaufl", "sperrmüll", "clearance")
    )

    chosen: list[str] = []
    seen_files: set[str] = set()

    def _add(ref: DesignReference) -> bool:
        if not ref.screenshot or not Path(ref.screenshot).is_file():
            return False
        key = str(Path(ref.screenshot).resolve())
        if key in seen_files:
            return False
        seen_files.add(key)
        chosen.append(ref.screenshot)
        return True

    # Pass 1: best niche match per priority section
    for sec in _VISION_PRIORITY_SECTIONS:
        if len(chosen) >= max_images:
            break
        refs = catalog.refs_for(sec)
        if not refs:
            continue
        ranked = sorted(
            refs,
            key=lambda r: (
                -(5 if junkish and ("junk" in " ".join(r.niche_tags) or "junk" in r.source.lower()) else 0)
                - (2 if "local-service" in r.niche_tags else 0)
                - (1 if r.layout else 0)
            ),
        )
        _add(ranked[0])

    # Pass 2: add a second option per key section for comparison
    for sec in ("hero", "services", "process", "cta", "contact"):
        if len(chosen) >= max_images:
            break
        refs = catalog.refs_for(sec)
        for r in refs:
            if len(chosen) >= max_images:
                break
            _add(r)

    log.info(
        "Selected {n} Design Library screenshots for vision analysis",
        n=len(chosen),
    )
    return chosen


def create_design_blueprint(
    *,
    business_context: str,
    catalog: DesignLibraryCatalog,
    router: Any,
    theme_id: str = "kadence",
    template_id: str = "",
    use_vision: bool = True,
) -> dict[str, Any] | None:
    """Ask Claude to produce a Design Blueprint from the library + business brief.

    When ``use_vision`` is True (default), attaches Design Library screenshots
    so Sonnet can study layouts visually during the Migrate / modernize run.

    Returns a dict matching the schema above, or ``None`` on failure.
    """
    catalog_text = catalog.to_prompt_catalog(sections=SECTION_KEYS)
    vision_paths: list[str] = []
    if use_vision:
        vision_paths = select_vision_screenshots(
            catalog, business_context=business_context
        )

    prompt = (
        f"Create a Design Blueprint for this client.\n"
        f"Theme: {theme_id or 'kadence'}"
        + (f" | Template: {template_id}" if template_id else "")
        + "\n"
        + (
            f"VISION: {len(vision_paths)} Design Library screenshots are attached. "
            "Study them before choosing references.\n"
            if vision_paths
            else "VISION: no screenshots attached — use text catalog only.\n"
        )
        + "\n"
        + "## Client brief (Website Package summary)\n"
        + business_context[:5500]
        + "\n\n"
        + catalog_text
        + "\n\nReturn ONLY the Design Blueprint JSON."
    )

    try:
        resp = router.request(
            prompt,
            provider=AIProvider.CLAUDE,
            system=_SYSTEM,
            task="design_blueprint",
            temperature=0.4,
            max_tokens=6000,
            use_cache=False,
            images=vision_paths or None,
            allow_fallback=not bool(vision_paths),
        )
        parsed = _parse_blueprint(resp.text or "", catalog)
        if parsed is not None:
            parsed["vision_images"] = len(vision_paths)
            parsed["vision_used"] = bool(vision_paths)
            if vision_paths and not parsed.get("vision_summary"):
                parsed["vision_summary"] = (
                    f"Studied {len(vision_paths)} Design Library screenshots via Claude vision."
                )
    except Exception as exc:  # noqa: BLE001
        log.error("Design Blueprint Claude call failed: {e}", e=exc)
        parsed = None
        # Retry once text-only if vision request failed
        if vision_paths:
            log.warning("Retrying Design Blueprint without vision images…")
            try:
                resp = router.request(
                    prompt.replace(
                        f"VISION: {len(vision_paths)} Design Library screenshots are attached. "
                        "Study them before choosing references.\n",
                        "VISION: unavailable — use text catalog only.\n",
                    ),
                    provider=AIProvider.CLAUDE,
                    system=_SYSTEM,
                    task="design_blueprint",
                    temperature=0.4,
                    max_tokens=6000,
                    use_cache=False,
                    images=None,
                )
                parsed = _parse_blueprint(resp.text or "", catalog)
                if parsed is not None:
                    parsed["vision_images"] = 0
                    parsed["vision_used"] = False
                    parsed["vision_summary"] = "Vision failed; used text catalog only."
            except Exception as exc2:  # noqa: BLE001
                log.error("Text-only blueprint retry failed: {e}", e=exc2)
                parsed = None

    if parsed:
        log.info(
            "Design Blueprint ready — {n} sections, vision={v}, direction={d!r}",
            n=len(parsed.get("sections") or []),
            v=parsed.get("vision_images", 0),
            d=(parsed.get("creative_direction") or "")[:80],
        )
        return parsed

    log.warning("Blueprint parse failed — using heuristic blueprint from library")
    return heuristic_blueprint(catalog, business_context=business_context)


def heuristic_blueprint(
    catalog: DesignLibraryCatalog,
    *,
    business_context: str = "",
) -> dict[str, Any]:
    """Deterministic fallback when Claude is unavailable."""
    ctx_low = business_context.lower()
    junkish = any(
        k in ctx_low
        for k in ("entrümpel", "entruempel", "junk", "haushaltsaufl", "sperrmüll", "clearance")
    )

    sections: list[dict[str, Any]] = []
    used_sources: list[str] = []

    for sec in SECTION_KEYS:
        refs = catalog.refs_for(sec)
        if not refs:
            continue
        pick = _pick_ref(refs, junkish=junkish, used=used_sources)
        if pick is None:
            continue
        used_sources.append(pick.source)
        # Soft rotate sources
        if len(used_sources) > 4:
            used_sources = used_sources[-2:]

        include = sec in ("hero", "services", "cta", "contact", "footer", "process", "about", "faq")
        sections.append({
            "section": sec,
            "reference": pick.ref_id,
            "reference_path": f"Library/{pick.path}/screenshot.png",
            "reason": (
                f"Strong {sec} pattern from {pick.source}"
                + (" — junk/removal niche fit" if junkish and "junk" in " ".join(pick.niche_tags) else "")
                + (f" ({pick.layout})" if pick.layout else "")
            ),
            "layout_notes": pick.layout or f"Learn composition from {pick.ref_id}",
            "client_content": {
                "heading": "",
                "subheading": "",
                "body": "",
                "cta": "",
                "images": [],
                "items": [],
            },
            "include": include,
        })

    return {
        "creative_direction": (
            "Premium local-service redesign inspired by Design Library patterns: "
            "trust-first hero, clear service cards, simple process, strong CTA."
        ),
        "sections": sections,
        "heuristic": True,
        "vision_used": False,
        "vision_images": 0,
        "vision_summary": "Heuristic blueprint (no Claude vision).",
    }


def blueprint_to_prompt(blueprint: dict[str, Any]) -> str:
    """Serialize blueprint for the content-mapping prompt."""
    parts = [
        "## Design Blueprint (follow this — do not invent new section styles)",
        f"Creative direction: {blueprint.get('creative_direction', '')}",
        "",
    ]
    for sec in blueprint.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        if sec.get("include") is False:
            parts.append(
                f"### {sec.get('section')} — SKIP (no client material)  "
                f"[ref studied: {sec.get('reference')}]"
            )
            continue
        parts.append(f"### {sec.get('section')}")
        parts.append(f"Reference: {sec.get('reference')} ({sec.get('reference_path', '')})")
        parts.append(f"Reason: {sec.get('reason', '')}")
        parts.append(f"Layout notes: {sec.get('layout_notes', '')}")
        cc = sec.get("client_content") or {}
        if isinstance(cc, dict):
            for k in ("heading", "subheading", "body", "cta"):
                if cc.get(k):
                    parts.append(f"Client {k}: {cc[k]}")
            if cc.get("items"):
                parts.append("Client items: " + " | ".join(str(x) for x in cc["items"][:8]))
            if cc.get("images"):
                parts.append("Client images: " + ", ".join(str(x) for x in cc["images"][:6]))
        parts.append("")
    return "\n".join(parts)


def save_blueprint(blueprint: dict[str, Any], data_dir: Path) -> Path:
    """Persist ``json/design_blueprint.json`` (+ artifacts copy)."""
    data_dir = Path(data_dir)
    json_dir = data_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    out = json_dir / "design_blueprint.json"
    text = json.dumps(blueprint, ensure_ascii=False, indent=2)
    out.write_text(text, encoding="utf-8")
    art = data_dir / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    (art / "design_blueprint.json").write_text(text, encoding="utf-8")
    return out


def _pick_ref(
    refs: list[DesignReference],
    *,
    junkish: bool,
    used: list[str],
) -> DesignReference | None:
    if not refs:
        return None
    scored: list[tuple[int, DesignReference]] = []
    for r in refs:
        score = 0
        tags = " ".join(r.niche_tags).lower()
        if junkish and ("junk" in tags or "junk" in r.source.lower()):
            score += 5
        if "local-service" in tags:
            score += 2
        if r.source not in used:
            score += 3
        if r.layout:
            score += 1
        if r.headings:
            score += 1
        scored.append((score, r))
    scored.sort(key=lambda t: (-t[0], t[1].source))
    return scored[0][1]


def _parse_blueprint(raw: str, catalog: DesignLibraryCatalog) -> dict[str, Any] | None:
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        return None

    # Normalize / validate reference ids against catalog
    fixed: list[dict[str, Any]] = []
    for sec in data["sections"]:
        if not isinstance(sec, dict):
            continue
        name = str(sec.get("section") or "").lower().strip()
        if not name:
            continue
        ref_id = str(sec.get("reference") or "").strip().replace("\\", "/")
        # Accept "Library/hero/Neat" style
        if ref_id.lower().startswith("library/"):
            ref_id = ref_id[8:]
        if ref_id.endswith("/screenshot.png"):
            ref_id = ref_id[: -len("/screenshot.png")]
        ref = catalog.get(ref_id)
        if ref is None and "/" not in ref_id:
            # Try section/ref_id
            ref = catalog.get(f"{name}/{ref_id}")
            if ref:
                ref_id = ref.ref_id
        if ref is None:
            # Fall back to best available for section
            candidates = catalog.refs_for(name)
            ref = candidates[0] if candidates else None
            if ref:
                ref_id = ref.ref_id
                sec["reason"] = (sec.get("reason") or "") + " [ref auto-corrected]"
        if ref:
            sec["reference"] = ref.ref_id
            sec["reference_path"] = f"Library/{ref.path}/screenshot.png"
        sec["section"] = name
        if "include" not in sec:
            sec["include"] = True
        fixed.append(sec)
    data["sections"] = fixed
    return data
