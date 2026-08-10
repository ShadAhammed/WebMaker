"""
webmaker.agents.design_recommendation.agent
===========================================
Agent 4 — Design Pattern Selector.

Responsibility: select proven design components from the curated library.
It does NOT invent layouts or page structures from scratch.

Inputs:
  - Business Profile
  - Approved OP-Content recommendations (selected=True)
  - Business category / industry
  - Design Pattern Library + theme catalog

Outputs (DesignRecommendation):
  - WordPress theme + starter template (with justification)
  - One pattern per slot: hero, services, about, process, testimonial,
    faq, cta, footer — each with a short justification

Primary path: GPT selects only catalog IDs. Offline fallback: deterministic
tag / category scoring against the same libraries.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field

from webmaker.agents.base import AgentContext, BaseAgent
from webmaker.core.logging import get_logger
from webmaker.core.types import AIProvider
from webmaker.data.design_patterns import (
    PATTERN_SLOTS,
    catalog_summary as patterns_catalog_summary,
    get_pattern,
    patterns_by_slot,
)
from webmaker.data.theme_catalog import THEMES
from webmaker.schemas.business import BusinessProfile
from webmaker.schemas.design import DesignRecommendation, PatternSelection, ThemeOption
from webmaker.schemas.review import OpContent

log = get_logger("agent.design_recommendation")

_MAX_THEME_OPTIONS = 5

_SYSTEM_PROMPT = (
    "You are a Design Pattern Selector for German local business websites. "
    "You NEVER invent layouts, themes, templates, or pattern IDs. "
    "You ONLY choose from the provided Design Pattern Library and theme catalog. "
    "Prefer proven, consistent patterns used by professional builders "
    "(Framer/Webflow-style systems) over creative one-offs. "
    "For every choice, give a short justification tied to this business. "
    "Respond with a SINGLE valid JSON object — no markdown, no fences."
)


class DesignInput(BaseModel):
    """Typed input for the Design Pattern Selector."""

    model_config = ConfigDict(extra="forbid")

    business: BusinessProfile = Field(default_factory=BusinessProfile)
    # Approved recommendations only are meaningful; agent filters selected=True.
    op_content: OpContent = Field(default_factory=OpContent)


class DesignRecommendationAgent(BaseAgent[DesignInput, DesignRecommendation]):
    """Select theme/template + design patterns from curated libraries."""

    name = "design_recommendation"
    input_model = DesignInput
    output_model = DesignRecommendation

    def __init__(self, context: AgentContext, *, router=None) -> None:
        super().__init__(context)
        self._router = router

    def _get_router(self):
        if self._router is None:
            from webmaker.modules.ai_router import AIRouter
            self._router = AIRouter(self._ctx.settings)
        return self._router

    def _run(self, data: DesignInput) -> DesignRecommendation:
        fallback = self._select_deterministic(data)
        gpt = self._select_with_gpt(data, fallback)
        return gpt if gpt is not None else fallback

    # ── GPT selection ────────────────────────────────────────────────────────

    def _select_with_gpt(
        self,
        data: DesignInput,
        fallback: DesignRecommendation,
    ) -> DesignRecommendation | None:
        approved = data.op_content.selected_recommendations()
        approved_lines = [
            f"- [{r.priority}] {r.page_slug}/{r.section}: {r.recommendation}"
            for r in approved[:20]
        ] or ["(none approved yet — use business profile only)"]

        prompt = (
            f"BUSINESS PROFILE\n"
            f"Name: {data.business.name}\n"
            f"Industry / category: {data.business.industry}\n"
            f"Location: {data.business.location}\n"
            f"Services: {', '.join(data.business.services) or '[unknown]'}\n"
            f"Unique value: {data.business.unique_value or '[unknown]'}\n"
            f"Tone: {data.business.tone_of_voice or '[unknown]'}\n"
            f"Primary color: {data.business.primary_color or '[unknown]'}\n\n"
            f"APPROVED OP-CONTENT RECOMMENDATIONS\n"
            f"{chr(10).join(approved_lines)}\n\n"
            f"THEME CATALOG (choose theme_id + template_id only from here)\n"
            f"{self._theme_catalog_summary()}\n\n"
            f"DESIGN PATTERN LIBRARY (choose exactly one pattern_id per slot)\n"
            f"{patterns_catalog_summary()}\n\n"
            "Return JSON:\n"
            "{\n"
            '  "selected_theme": "theme_id",\n'
            '  "selected_template": "template_id",\n'
            '  "theme_justification": "why this theme",\n'
            '  "template_justification": "why this starter template",\n'
            '  "typography": "short guidance",\n'
            '  "color_palette": ["#hex"],\n'
            '  "visual_style": "short",\n'
            '  "business_style": "short",\n'
            '  "patterns": [\n'
            '    {"slot": "hero", "pattern_id": "...", "justification": "..."},\n'
            '    {"slot": "services", "pattern_id": "...", "justification": "..."},\n'
            '    {"slot": "about", "pattern_id": "...", "justification": "..."},\n'
            '    {"slot": "process", "pattern_id": "...", "justification": "..."},\n'
            '    {"slot": "testimonial", "pattern_id": "...", "justification": "..."},\n'
            '    {"slot": "faq", "pattern_id": "...", "justification": "..."},\n'
            '    {"slot": "cta", "pattern_id": "...", "justification": "..."},\n'
            '    {"slot": "footer", "pattern_id": "...", "justification": "..."}\n'
            "  ]\n"
            "}\n"
            "Rules: every pattern_id MUST exist in the library; every slot exactly once."
        )

        try:
            router = self._get_router()
            if not router.is_available(AIProvider.OPENAI):
                log.info("GPT unavailable — using deterministic pattern selection")
                return None
            response = router.request(
                prompt,
                provider=AIProvider.OPENAI,
                system=_SYSTEM_PROMPT,
                task="design_recommendation",
                temperature=0.2,
                max_tokens=2500,
                allow_fallback=False,
            )
            parsed = self._parse_json(response.text)
        except Exception as exc:  # noqa: BLE001
            log.warning("GPT pattern selection failed ({e}); using fallback", e=exc)
            return None

        if not parsed:
            return None
        return self._merge_gpt_result(parsed, fallback, data.business)

    def _merge_gpt_result(
        self,
        parsed: dict,
        fallback: DesignRecommendation,
        business: BusinessProfile,
    ) -> DesignRecommendation | None:
        theme_id = str(parsed.get("selected_theme") or fallback.selected_theme).strip()
        template_id = str(parsed.get("selected_template") or fallback.selected_template).strip()

        # Validate theme/template against catalog; fall back if invalid.
        theme_opt = self._find_theme_option(theme_id, template_id) or (
            fallback.options[0] if fallback.options else None
        )
        if theme_opt is None:
            return None

        patterns = self._parse_pattern_selections(parsed.get("patterns") or [], fallback)
        if len(patterns) < len(PATTERN_SLOTS):
            # Fill any missing slots from deterministic fallback.
            have = {str(p.slot) for p in patterns}
            for fb in fallback.patterns:
                if str(fb.slot) not in have:
                    patterns.append(fb)

        palette = [
            str(c) for c in (parsed.get("color_palette") or []) if str(c).strip()
        ]
        if not palette and business.primary_color:
            palette = [business.primary_color]

        # Rebuild ranked options with selected pair first.
        options = [theme_opt] + [
            o for o in fallback.options
            if not (o.theme_id == theme_opt.theme_id and o.template_id == theme_opt.template_id)
        ]

        return DesignRecommendation(
            options=options[:_MAX_THEME_OPTIONS],
            selected_theme=theme_opt.theme_id,
            selected_template=theme_opt.template_id,
            theme_justification=str(
                parsed.get("theme_justification") or theme_opt.rationale
            ),
            template_justification=str(
                parsed.get("template_justification") or theme_opt.rationale
            ),
            patterns=patterns,
            typography=str(parsed.get("typography") or ""),
            color_palette=palette,
            visual_style=str(parsed.get("visual_style") or "clean, modern, trustworthy"),
            business_style=str(parsed.get("business_style") or business.industry or ""),
        )

    def _parse_pattern_selections(
        self,
        raw_list: object,
        fallback: DesignRecommendation,
    ) -> list[PatternSelection]:
        by_slot: dict[str, PatternSelection] = {}
        if isinstance(raw_list, list):
            for raw in raw_list:
                if not isinstance(raw, dict):
                    continue
                slot = str(raw.get("slot") or "").strip().lower()
                if slot not in PATTERN_SLOTS:
                    continue
                pid = str(raw.get("pattern_id") or "").strip()
                entry = get_pattern(pid)
                if entry is None or entry["category"] != slot:
                    continue
                by_slot[slot] = PatternSelection(
                    slot=slot,
                    pattern_id=entry["id"],
                    pattern_name=entry["name"],
                    justification=str(raw.get("justification") or entry["description"]),
                )
        # Fill gaps from fallback
        for fb in fallback.patterns:
            if str(fb.slot) not in by_slot:
                by_slot[str(fb.slot)] = fb
        return [by_slot[s] for s in PATTERN_SLOTS if s in by_slot]

    # ── Deterministic fallback ───────────────────────────────────────────────

    def _select_deterministic(self, data: DesignInput) -> DesignRecommendation:
        keywords = self._keywords(data)
        theme_options = self._rank_themes(keywords)
        top = theme_options[0] if theme_options else ThemeOption()

        patterns: list[PatternSelection] = []
        for slot in PATTERN_SLOTS:
            best = self._best_pattern(slot, keywords)
            if best is None:
                continue
            patterns.append(
                PatternSelection(
                    slot=slot,
                    pattern_id=best["id"],
                    pattern_name=best["name"],
                    justification=self._pattern_justification(best, data.business),
                )
            )

        palette = [data.business.primary_color] if data.business.primary_color else []
        return DesignRecommendation(
            options=theme_options[:_MAX_THEME_OPTIONS],
            selected_theme=top.theme_id,
            selected_template=top.template_id,
            theme_justification=top.rationale,
            template_justification=top.rationale,
            patterns=patterns,
            typography="",
            color_palette=palette,
            visual_style="clean, modern, trustworthy",
            business_style=data.business.industry or "",
        )

    def _rank_themes(self, keywords: set[str]) -> list[ThemeOption]:
        scored: list[tuple[float, ThemeOption]] = []
        for theme in THEMES:
            for tmpl in theme["templates"]:
                score = self._score(keywords, tmpl.get("tags", []))
                scored.append(
                    (
                        score,
                        ThemeOption(
                            theme_id=theme["id"],
                            theme_name=theme["name"],
                            template_id=tmpl["id"],
                            template_name=tmpl["name"],
                            preview_url=tmpl.get("preview_url", ""),
                            rationale=(
                                f"Matches business focus ({', '.join(tmpl.get('tags', [])[:3])}). "
                                f"SEO: {theme.get('seo', '')}"
                                if score > 0
                                else f"General-purpose fit. SEO: {theme.get('seo', '')}"
                            ),
                            score=round(score, 3),
                        ),
                    )
                )
        scored.sort(key=lambda x: x[0], reverse=True)
        return [opt for _, opt in scored]

    def _best_pattern(self, slot: str, keywords: set[str]):
        candidates = patterns_by_slot(slot)  # type: ignore[arg-type]
        if not candidates:
            return None
        best = max(
            candidates,
            key=lambda p: self._score(keywords, p.get("tags", []) + p.get("best_for", [])),
        )
        return best

    @staticmethod
    def _pattern_justification(pattern: dict, business: BusinessProfile) -> str:
        industry = business.industry or "this business"
        return (
            f"{pattern['name']} fits {industry}: {pattern['description']} "
            f"({pattern.get('structure_notes', '')})"
        ).strip()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _keywords(self, data: DesignInput) -> set[str]:
        words: set[str] = set()
        texts = [
            data.business.industry,
            data.business.unique_value,
            data.business.tone_of_voice,
            *data.business.services,
        ]
        for rec in data.op_content.selected_recommendations():
            texts.extend([rec.section, rec.recommendation, rec.source])
        for text in texts:
            for token in str(text).lower().replace("/", " ").replace("-", " ").split():
                token = token.strip(".,;:()[]")
                if len(token) >= 3:
                    words.add(token)
        return words

    @staticmethod
    def _score(keywords: set[str], tags: list[str]) -> float:
        if not tags:
            return 0.0
        tag_words = {t.lower() for t in tags}
        overlap = keywords & tag_words
        partial = sum(
            0.5
            for k in keywords
            for t in tag_words
            if k != t and (k in t or t in k)
        )
        return float(len(overlap)) + partial

    @staticmethod
    def _theme_catalog_summary() -> str:
        lines: list[str] = []
        for theme in THEMES:
            for tmpl in theme["templates"]:
                tags = ", ".join(tmpl.get("tags", []))
                lines.append(
                    f"- theme_id={theme['id']} ({theme['name']}, SEO={theme.get('seo', '')}) "
                    f"template_id={tmpl['id']} ({tmpl['name']}) tags=[{tags}]"
                )
        return "\n".join(lines)

    @staticmethod
    def _find_theme_option(theme_id: str, template_id: str) -> ThemeOption | None:
        for theme in THEMES:
            if theme["id"] != theme_id:
                continue
            for tmpl in theme["templates"]:
                if tmpl["id"] != template_id:
                    continue
                return ThemeOption(
                    theme_id=theme["id"],
                    theme_name=theme["name"],
                    template_id=tmpl["id"],
                    template_name=tmpl["name"],
                    preview_url=tmpl.get("preview_url", ""),
                    rationale=f"Selected from catalog ({theme['name']} / {tmpl['name']})",
                    score=1.0,
                )
        return None

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        raw = (text or "").strip()
        if not raw:
            return None
        fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
        if fence:
            raw = fence.group(1)
        else:
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end > start:
                raw = raw[start : end + 1]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None
