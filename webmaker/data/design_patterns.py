"""
webmaker.data.design_patterns
=============================
Curated Design Pattern Library for Agent 4 (Design Pattern Selector).

Patterns are proven section/layout recipes — not free-form AI layouts.
Agent 4 may only select IDs from this library. Agent 5 (LiveDemoRenderer)
assembles the demo from the chosen theme/template + these patterns.

Slots (``category``):
  hero | services | about | process | testimonial | faq | cta | footer
"""

from __future__ import annotations

from typing import Literal, TypedDict

PatternSlot = Literal[
    "hero",
    "services",
    "about",
    "process",
    "testimonial",
    "faq",
    "cta",
    "footer",
]

PATTERN_SLOTS: tuple[PatternSlot, ...] = (
    "hero",
    "services",
    "about",
    "process",
    "testimonial",
    "faq",
    "cta",
    "footer",
)


class PatternEntry(TypedDict):
    id: str
    name: str
    category: PatternSlot
    description: str
    tags: list[str]
    best_for: list[str]
    structure_notes: str


DESIGN_PATTERNS: list[PatternEntry] = [
    # ── Hero ────────────────────────────────────────────────────────────────
    {
        "id": "hero-trust-local",
        "name": "Trust Local Hero",
        "category": "hero",
        "description": "H1 + location badge + primary phone CTA + secondary quote CTA; photo of real work.",
        "tags": ["local", "trust", "phone", "services"],
        "best_for": ["entrümpelung", "cleaning", "handwerk", "home services", "clearance"],
        "structure_notes": "Full-width hero, left copy / right image, sticky mobile call button.",
    },
    {
        "id": "hero-urgency-clear",
        "name": "Clear Urgency Hero",
        "category": "hero",
        "description": "Benefit headline, 3 trust chips (insured, fast, fixed price), dual CTAs.",
        "tags": ["urgency", "conversion", "trust", "cta"],
        "best_for": ["entrümpelung", "emergency", "removal", "restoration"],
        "structure_notes": "Centered stack on mobile; chips row under subheading.",
    },
    {
        "id": "hero-calm-professional",
        "name": "Calm Professional Hero",
        "category": "hero",
        "description": "Quiet typography, soft image, single primary CTA — premium local service feel.",
        "tags": ["professional", "calm", "b2b", "premium"],
        "best_for": ["consulting", "legal", "medical", "office", "professional"],
        "structure_notes": "Generous whitespace; no promo stickers on the image.",
    },
    # ── Services ────────────────────────────────────────────────────────────
    {
        "id": "services-card-grid-3",
        "name": "Three-Column Service Cards",
        "category": "services",
        "description": "3–6 cards: icon/photo, service name, 2-line benefit, link to contact.",
        "tags": ["services", "cards", "grid", "overview"],
        "best_for": ["home services", "cleaning", "handwerk", "local"],
        "structure_notes": "Equal-height cards; no nested cards; one CTA style.",
    },
    {
        "id": "services-list-split",
        "name": "Split Service List",
        "category": "services",
        "description": "Left sticky intro, right numbered service list with short bullets.",
        "tags": ["services", "list", "detail", "seo"],
        "best_for": ["entrümpelung", "construction", "trades", "b2b"],
        "structure_notes": "Good for long-tail keywords without extra orphan pages.",
    },
    {
        "id": "services-process-linked",
        "name": "Services Linked to Process",
        "category": "services",
        "description": "Service tiles that map 1:1 into the process steps below.",
        "tags": ["services", "process", "clarity"],
        "best_for": ["clearance", "renovation", "restoration"],
        "structure_notes": "Use same service names in process pattern for consistency.",
    },
    # ── About ───────────────────────────────────────────────────────────────
    {
        "id": "about-story-photo",
        "name": "Owner Story + Photo",
        "category": "about",
        "description": "Short founder/company story beside a real team or job-site photo.",
        "tags": ["about", "trust", "local", "human"],
        "best_for": ["local", "family", "handwerk", "home services"],
        "structure_notes": "No stock-handshake clichés; prefer authentic imagery.",
    },
    {
        "id": "about-credentials-strip",
        "name": "Credentials Strip",
        "category": "about",
        "description": "Compact about blurb + insurance/certs/years row — facts only.",
        "tags": ["about", "trust", "credentials", "seo"],
        "best_for": ["entrümpelung", "construction", "professional"],
        "structure_notes": "Never invent certifications; use [MISSING INFORMATION] if unknown.",
    },
    {
        "id": "about-mission-values",
        "name": "Mission & Values",
        "category": "about",
        "description": "Mission paragraph + 3 value points (reliable, fair, clean).",
        "tags": ["about", "values", "brand"],
        "best_for": ["professional", "b2b", "premium"],
        "structure_notes": "Keep values grounded in provided business facts.",
    },
    # ── Process ─────────────────────────────────────────────────────────────
    {
        "id": "process-steps-4",
        "name": "Four-Step Process",
        "category": "process",
        "description": "Contact → Quote → Schedule → Done — numbered horizontal/vertical steps.",
        "tags": ["process", "steps", "conversion", "clarity"],
        "best_for": ["entrümpelung", "cleaning", "home services", "clearance"],
        "structure_notes": "4 steps max; each step one verb + one sentence.",
    },
    {
        "id": "process-timeline",
        "name": "Vertical Timeline",
        "category": "process",
        "description": "Vertical timeline for multi-day or multi-phase jobs.",
        "tags": ["process", "timeline", "detail"],
        "best_for": ["construction", "renovation", "restoration"],
        "structure_notes": "Alternate text/media if images exist; else text-only.",
    },
    {
        "id": "process-checklist",
        "name": "Prep Checklist Process",
        "category": "process",
        "description": "What the customer should prepare + what the team does on site.",
        "tags": ["process", "checklist", "trust"],
        "best_for": ["entrümpelung", "moving", "clearance", "removal"],
        "structure_notes": "Two columns: Customer / Team.",
    },
    # ── Testimonial ─────────────────────────────────────────────────────────
    {
        "id": "testimonial-quote-cards",
        "name": "Quote Cards",
        "category": "testimonial",
        "description": "2–3 short quote cards with name + city when available.",
        "tags": ["testimonial", "trust", "social-proof"],
        "best_for": ["local", "home services", "cleaning"],
        "structure_notes": "Never invent reviews; hide section if none provided.",
    },
    {
        "id": "testimonial-featured-single",
        "name": "Featured Single Quote",
        "category": "testimonial",
        "description": "One strong quote in large type with attribution.",
        "tags": ["testimonial", "minimal", "trust"],
        "best_for": ["premium", "professional", "b2b"],
        "structure_notes": "Use only if a real review exists in OP-Content/business data.",
    },
    {
        "id": "testimonial-logo-bar",
        "name": "Client / Partner Bar",
        "category": "testimonial",
        "description": "Logo or name bar for B2B clients (no fake logos).",
        "tags": ["testimonial", "b2b", "partners"],
        "best_for": ["b2b", "office", "commercial"],
        "structure_notes": "Skip entirely when no real partners are known.",
    },
    # ── FAQ ─────────────────────────────────────────────────────────────────
    {
        "id": "faq-accordion-local",
        "name": "Local FAQ Accordion",
        "category": "faq",
        "description": "6–10 accordion items: price, area, timing, disposal, insurance.",
        "tags": ["faq", "seo", "local", "conversion"],
        "best_for": ["entrümpelung", "cleaning", "home services", "local"],
        "structure_notes": "One H2; questions as H3; schema-friendly markup later.",
    },
    {
        "id": "faq-two-column",
        "name": "Two-Column FAQ",
        "category": "faq",
        "description": "Split practical vs. pricing/legal style questions.",
        "tags": ["faq", "clarity", "seo"],
        "best_for": ["professional", "b2b", "construction"],
        "structure_notes": "Keep answers short; link to contact CTA.",
    },
    {
        "id": "faq-short-essential",
        "name": "Essential Five FAQ",
        "category": "faq",
        "description": "Only the five most conversion-critical questions.",
        "tags": ["faq", "minimal", "conversion"],
        "best_for": ["small", "startup", "local"],
        "structure_notes": "Prefer when OP-Content marks FAQ as overcrowded.",
    },
    # ── CTA ─────────────────────────────────────────────────────────────────
    {
        "id": "cta-phone-band",
        "name": "Phone CTA Band",
        "category": "cta",
        "description": "Full-width band: call now + callback form link.",
        "tags": ["cta", "phone", "conversion"],
        "best_for": ["local", "entrümpelung", "emergency", "home services"],
        "structure_notes": "Repeat phone exactly as in business profile.",
    },
    {
        "id": "cta-quote-form",
        "name": "Quote Request CTA",
        "category": "cta",
        "description": "Short quote request block with 3 fields + submit.",
        "tags": ["cta", "form", "lead"],
        "best_for": ["b2b", "construction", "professional"],
        "structure_notes": "Do not invent form backends; mailto or WP form placeholder.",
    },
    {
        "id": "cta-dual-soft",
        "name": "Dual Soft CTA",
        "category": "cta",
        "description": "Primary call + secondary WhatsApp/email — calm dual actions.",
        "tags": ["cta", "dual", "calm"],
        "best_for": ["premium", "professional", "calm"],
        "structure_notes": "Equal visual weight; no flashing urgency.",
    },
    # ── Footer ──────────────────────────────────────────────────────────────
    {
        "id": "footer-local-compact",
        "name": "Compact Local Footer",
        "category": "footer",
        "description": "Company, address, phone, hours, short nav, Impressum/Datenschutz links.",
        "tags": ["footer", "local", "legal", "contact"],
        "best_for": ["local", "home services", "entrümpelung", "cleaning"],
        "structure_notes": "Required DE legal links; keep nav labels short.",
    },
    {
        "id": "footer-three-column",
        "name": "Three-Column Footer",
        "category": "footer",
        "description": "About blurb | Services list | Contact + social.",
        "tags": ["footer", "services", "contact"],
        "best_for": ["professional", "b2b", "multi-service"],
        "structure_notes": "Services list mirrors main services only — no invented items.",
    },
    {
        "id": "footer-minimal-legal",
        "name": "Minimal Legal Footer",
        "category": "footer",
        "description": "Single row: © company · Impressum · Datenschutz · contact email.",
        "tags": ["footer", "minimal", "legal"],
        "best_for": ["minimal", "premium", "landing"],
        "structure_notes": "Use when main pages already carry rich contact blocks.",
    },
]


def patterns_by_slot(slot: PatternSlot) -> list[PatternEntry]:
    """Return all patterns for a given slot."""
    return [p for p in DESIGN_PATTERNS if p["category"] == slot]


def get_pattern(pattern_id: str) -> PatternEntry | None:
    """Look up a pattern by id."""
    for p in DESIGN_PATTERNS:
        if p["id"] == pattern_id:
            return p
    return None


def catalog_summary() -> str:
    """Compact text catalog for LLM prompts (IDs only — never invent)."""
    lines: list[str] = []
    for slot in PATTERN_SLOTS:
        lines.append(f"[{slot}]")
        for p in patterns_by_slot(slot):
            tags = ", ".join(p["tags"][:4])
            best = ", ".join(p["best_for"][:4])
            lines.append(
                f"  - id={p['id']} | {p['name']} | tags=[{tags}] | best_for=[{best}]"
            )
    return "\n".join(lines)
