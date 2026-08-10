"""
webmaker.agents.website_reviewer.agent
======================================
Agent 3 — WebsiteReviewer (the intelligence layer).

Responsibility: review the target website against competitors and modern German
web-design / SEO / conversion standards, and emit actionable, human-approvable
recommendations. It does NOT modify the website or generate WordPress.

Model: Claude Sonnet 4.6 (via AIRouter, provider=CLAUDE).

Output: :class:`OpContent` — rendered as tickable cards in the OP-Content tab.
Every recommendation carries: current, issue, recommendation, reason, source,
priority, selected (defaults False).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from webmaker.agents.base import AgentContext, BaseAgent
from webmaker.core.json_util import loads_lenient, salvage_review_payload
from webmaker.core.logging import get_logger
from webmaker.core.types import AIProvider
from webmaker.schemas.competitor import CompetitorProjects
from webmaker.schemas.review import OpContent, Recommendation, SectionReview
from webmaker.schemas.target import TargetProject

log = get_logger("agent.website_reviewer")

_STANDARD_PAGES = ("homepage", "about", "services", "contact", "faq")
_VALID_PRIORITY = {"critical", "high", "medium", "low"}

_SYSTEM_PROMPT = (
    "You are a friendly website coach for German local service business owners "
    "(e.g. Entrümpelung, Handwerk). Write for someone who is NOT a tech expert.\n"
    "LANGUAGE RULES (mandatory):\n"
    "- ALL user-facing fields MUST be German only (Du-Form). Never English.\n"
    "- Use VERY SIMPLE everyday words. Short sentences.\n"
    "- No jargon: never write SEO, UX, CTA, conversion, H1, above-the-fold, SERP.\n"
    "  Say: 'besser bei Google gefunden', 'klarer Knopf', 'mehr Anrufe', "
    "'große Überschrift oben'.\n"
    "CONTENT RULES:\n"
    "- Never invent facts (prices, reviews, certificates).\n"
    "- Competitor ideas are inspiration only — never copy text.\n"
    "- Respond with a SINGLE valid JSON object only — no markdown, no fences.\n"
    "JSON RULES (mandatory — broken JSON is useless):\n"
    "- Escape every double quote inside string values as \\\"\n"
    "- Do NOT use German quotation marks („ “ « ») — paraphrase without quotes.\n"
    "- No raw newlines inside strings; use \\n if needed.\n"
    "- Keep proposed_html short (max ~180 characters).\n"
    "- Max 2 recommendations per page section.\n"
    "FIELD MEANING (critical):\n"
    "- recommendation = short tip in simple German (what to improve).\n"
    "- proposed_html = the READY German website text to put on the page "
    "(headline, paragraph, button label). NO instructions like 'Rewrite…'. "
    "NO English. This is the text that will be published when the user ticks yes."
)

_JSON_SCHEMA_HINT = (
    "{\n"
    '  "summary": "kurze Gesamteinschätzung auf Deutsch",\n'
    '  "sections": [\n'
    "    {\n"
    '      "page_slug": "homepage|about|services|contact|faq",\n'
    '      "section": "z.B. Überschrift, Menü, Leistungen, Vertrauen, Anrufen-Button",\n'
    '      "summary": "kurze Einschätzung auf Deutsch",\n'
    '      "recommendations": [\n'
    "        {\n"
    '          "current": "was jetzt da ist",\n'
    '          "issue": "was unklar oder fehlt — einfach auf Deutsch",\n'
    '          "recommendation": "kurzer Tipp auf Deutsch",\n'
    '          "proposed_html": "fertiger deutscher Website-Text (z.B. neue Überschrift oder Knopf-Text)",\n'
    '          "reason": "warum das hilft — einfach auf Deutsch",\n'
    '          "source": "competitor | seo-best-practice | ux | conversion",\n'
    '          "priority": "critical|high|medium|low"\n'
    "        }\n"
    "      ]\n"
    "    }\n"
    "  ]\n"
    "}"
)


class ReviewInput(BaseModel):
    """Typed input bundling the two upstream artifacts."""

    model_config = ConfigDict(extra="forbid")

    target: TargetProject
    competitors: CompetitorProjects = Field(default_factory=CompetitorProjects)


class WebsiteReviewerAgent(BaseAgent[ReviewInput, OpContent]):
    """Produce the OP-Content review using Claude Sonnet 4.6."""

    name = "website_reviewer"
    input_model = ReviewInput
    output_model = OpContent

    def __init__(self, context: AgentContext, *, router=None) -> None:
        super().__init__(context)
        self._router = router

    def _get_router(self):
        if self._router is None:
            from webmaker.modules.ai_router import AIRouter
            self._router = AIRouter(self._ctx.settings)
        return self._router

    def _run(self, data: ReviewInput) -> OpContent:
        focus = self._focus_pages()
        prompt = self._build_prompt(data, focus_pages=focus)
        router = self._get_router()
        raw_texts: list[str] = []

        try:
            response = router.request(
                prompt,
                provider=AIProvider.CLAUDE,
                system=_SYSTEM_PROMPT,
                task="website_review",
                temperature=0.3,
                max_tokens=8192,
            )
            raw_texts.append(response.text or "")
            parsed = self._parse_json(response.text)
            if not parsed:
                log.warning(
                    "WebsiteReviewer JSON parse failed "
                    "(chars={n}); retrying once with repair hint",
                    n=len(response.text or ""),
                )
                retry = router.request(
                    (
                        "Your previous answer was NOT valid JSON. "
                        "Return ONLY one compact JSON object matching this schema. "
                        "No markdown fences. No commentary.\n"
                        "CRITICAL: escape every \" inside string values as \\\". "
                        "Do not use German quotation marks. "
                        "Max 2 tips per page. proposed_html max 180 chars.\n"
                        f"{_JSON_SCHEMA_HINT}\n\n"
                        "Broken output to repair (may be cut off):\n"
                        f"{(response.text or '')[:5000]}"
                    ),
                    provider=AIProvider.CLAUDE,
                    system=_SYSTEM_PROMPT,
                    task="website_review",
                    temperature=0.0,
                    max_tokens=8192,
                )
                raw_texts.append(retry.text or "")
                parsed = self._parse_json(retry.text)

            if not parsed:
                # Salvage whatever we can from either response
                for blob in raw_texts:
                    salvaged = salvage_review_payload(blob)
                    if salvaged and (
                        salvaged.get("sections") or salvaged.get("summary")
                    ):
                        log.warning(
                            "WebsiteReviewer salvaged partial review "
                            "(sections={n})",
                            n=len(salvaged.get("sections") or []),
                        )
                        parsed = salvaged
                        break

            if not parsed:
                log.error(
                    "WebsiteReviewer parse failed after retry+salvage "
                    "(first_chars={c!r})",
                    c=(raw_texts[0] if raw_texts else "")[:240],
                )
                self._persist_raw_failure(raw_texts)
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            log.warning("WebsiteReviewer AI call failed ({e}); emitting empty review", e=exc)
            parsed = None
            self._persist_raw_failure(raw_texts)

        if not parsed:
            return OpContent(
                sections=[],
                page_slugs=list(focus or _STANDARD_PAGES),
                summary=(
                    "Claude-Antwort konnte nicht gelesen werden "
                    "(ungültiges JSON). Bitte Review erneut starten."
                ),
            )

        op = self._to_op_content(parsed, data, focus_pages=focus)
        if not op.sections and not (op.summary or "").strip():
            self._persist_raw_failure(raw_texts)
            return OpContent(
                sections=[],
                page_slugs=list(focus or _STANDARD_PAGES),
                summary=(
                    "Claude-Antwort enthielt keine verwertbaren Empfehlungen. "
                    "Bitte Review erneut starten."
                ),
            )
        if focus:
            op = self._merge_with_existing(op, focus)
        self._write_markdown(op)
        return op

    def _persist_raw_failure(self, raw_texts: list[str]) -> None:
        """Write raw model output for debugging when parse/salvage fails."""
        if not raw_texts:
            return
        try:
            out = Path(self._ctx.data_dir) / "artifacts" / "website_review_raw.txt"
            out.parent.mkdir(parents=True, exist_ok=True)
            chunks = []
            for i, t in enumerate(raw_texts, 1):
                chunks.append(f"===== RESPONSE {i} ({len(t)} chars) =====\n{t}\n")
            out.write_text("\n".join(chunks), encoding="utf-8")
            log.info("Saved raw review output → {p}", p=out)
        except OSError as exc:
            log.warning("Could not save raw review output: {e}", e=exc)
    def _focus_pages(self) -> list[str] | None:
        """Return page slugs to review, or None for all standard pages."""
        raw = self._ctx.extras.get("page_slug") or self._ctx.extras.get("pages")
        if raw is None or raw == "" or str(raw).strip().lower() in ("all", "*", "alle"):
            return None
        if isinstance(raw, (list, tuple)):
            pages = [str(p).strip().lower() for p in raw if str(p).strip()]
        else:
            pages = [str(raw).strip().lower()]
        pages = [p for p in pages if p in _STANDARD_PAGES]
        return pages or None

    def _merge_with_existing(self, fresh: OpContent, focus: list[str]) -> OpContent:
        """Keep other pages' sections when only some pages were re-reviewed."""
        focus_set = set(focus)
        existing_path = Path(self._ctx.data_dir) / "artifacts" / "op_content.json"
        # Also try project-level artifacts next to data_dir when they coincide
        kept: list[SectionReview] = []
        try:
            if existing_path.is_file():
                old = OpContent.model_validate_json(
                    existing_path.read_text(encoding="utf-8")
                )
                for sec in old.sections:
                    if sec.page_slug not in focus_set:
                        kept.append(sec)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not merge prior OP-Content: {e}", e=exc)

        merged_sections = kept + list(fresh.sections)
        slugs = list(dict.fromkeys(
            [*(s.page_slug for s in merged_sections if s.page_slug)]
            + list(fresh.page_slugs or [])
            + list(_STANDARD_PAGES)
        ))
        summary = fresh.summary
        if kept and focus:
            summary = (
                f"Aktualisiert für: {', '.join(focus)}. "
                f"{fresh.summary}"
            ).strip()
        return OpContent(
            sections=merged_sections,
            page_slugs=slugs,
            summary=summary,
        )

    def _write_markdown(self, op: OpContent) -> None:
        """Write page-by-page OP-Content markdown under the project folder."""
        try:
            root = Path(self._ctx.data_dir)
            # Prefer named project root (parent of artifacts when data_dir == project)
            out = root / "op_content.md"
            lines: list[str] = [
                f"# OP-Content — {self._ctx.project_slug}",
                "",
                op.summary.strip() or "_No summary._",
                "",
            ]
            by_page: dict[str, list] = {}
            for sec in op.sections:
                by_page.setdefault(sec.page_slug or "general", []).append(sec)

            for slug in (op.page_slugs or list(by_page.keys())):
                secs = by_page.get(slug) or []
                lines.append(f"## Page: {slug}")
                lines.append("")
                if not secs:
                    lines.append("_No section reviews._")
                    lines.append("")
                    continue
                for sec in secs:
                    lines.append(f"### {sec.section or 'general'}")
                    if sec.summary:
                        lines.append(sec.summary)
                        lines.append("")
                    for i, rec in enumerate(sec.recommendations, 1):
                        lines.append(f"#### Recommendation {i} [{rec.priority}]")
                        lines.append(f"- **Current:** {rec.current or '—'}")
                        lines.append(f"- **Issue:** {rec.issue or '—'}")
                        lines.append(f"- **Recommendation:** {rec.recommendation or '—'}")
                        lines.append(f"- **Reason:** {rec.reason or '—'}")
                        lines.append(f"- **Source:** {rec.source or '—'}")
                        lines.append(f"- **Selected:** {rec.selected}")
                        lines.append("")
            out.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
            log.info("OP-Content markdown → {p}", p=out)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not write op_content.md: {e}", e=exc)

    # ── Prompt building ──────────────────────────────────────────────────────

    def _build_prompt(
        self,
        data: ReviewInput,
        *,
        focus_pages: list[str] | None = None,
    ) -> str:
        biz = data.target.business
        comp_lines: list[str] = []
        for c in data.competitors.competitors:
            strengths = "; ".join(c.strengths[:6])
            comp_lines.append(f"- {c.name or c.url}: {strengths}")

        pages = "\n".join(
            f"- {p.page_type}: {p.title} ({p.url})" for p in data.target.pages[:20]
        )
        scope = ", ".join(focus_pages) if focus_pages else ", ".join(_STANDARD_PAGES)
        if focus_pages:
            task = (
                f"Review ONLY this page: {scope}. Ignore other pages. "
                f"Give a few clear tips for that page only."
            )
        else:
            task = (
                f"Review these pages: {scope}. For each page, give a few clear tips."
            )

        return (
            f"TARGET BUSINESS\n"
            f"Name: {biz.name}\nIndustry: {biz.industry}\nLocation: {biz.location}\n"
            f"Services: {', '.join(biz.services) or '[unknown]'}\n"
            f"Unique value: {biz.unique_value or '[unknown]'}\n"
            f"Tone: {biz.tone_of_voice or '[unknown]'}\n\n"
            f"TARGET PAGES\n{pages or '[none crawled]'}\n\n"
            f"COMPETITOR STRENGTHS (inspiration only)\n"
            f"{chr(10).join(comp_lines) or '[none]'}\n\n"
            f"TASK\n{task} "
            f"Help the owner get more calls and look trustworthy. "
            f"Write every tip in very easy German — short sentences, no jargon. "
            f"Never invent facts.\n"
            f"Max 2 tips per page. Keep proposed_html under 180 characters. "
            f"Escape all double quotes inside JSON strings as \\\". "
            f"Do not use German quotation marks („ “).\n\n"
            f"Return JSON in exactly this shape:\n{_JSON_SCHEMA_HINT}"
        )

    # ── Parsing ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        data = loads_lenient(text)
        if data is not None:
            return data
        # Last local attempt before AI retry / salvage
        salvaged = salvage_review_payload(text)
        if salvaged and (salvaged.get("sections") or salvaged.get("summary")):
            log.warning(
                "JSON decode failed — salvaged {n} section(s) locally",
                n=len(salvaged.get("sections") or []),
            )
            return salvaged
        if text and text.strip():
            log.warning("JSON decode error: could not parse or salvage review payload")
        return None

    def _to_op_content(
        self,
        parsed: dict,
        data: ReviewInput,
        *,
        focus_pages: list[str] | None = None,
    ) -> OpContent:
        sections: list[SectionReview] = []
        raw_sections = parsed.get("sections") or []
        seen_slugs: list[str] = []
        focus_set = set(focus_pages) if focus_pages else None

        for raw_sec in raw_sections:
            if not isinstance(raw_sec, dict):
                continue
            slug = str(raw_sec.get("page_slug") or "homepage").strip().lower()
            if focus_set is not None and slug not in focus_set:
                continue
            section_name = str(raw_sec.get("section") or "general").strip()
            recs: list[Recommendation] = []
            for raw_rec in raw_sec.get("recommendations") or []:
                if not isinstance(raw_rec, dict):
                    continue
                priority = str(raw_rec.get("priority") or "medium").strip().lower()
                if priority not in _VALID_PRIORITY:
                    priority = "medium"
                recs.append(
                    Recommendation(
                        id=uuid.uuid4().hex[:12],
                        page_slug=slug,
                        section=section_name,
                        current=str(raw_rec.get("current") or ""),
                        issue=str(raw_rec.get("issue") or ""),
                        recommendation=str(raw_rec.get("recommendation") or ""),
                        reason=str(raw_rec.get("reason") or ""),
                        source=str(raw_rec.get("source") or ""),
                        priority=priority,  # type: ignore[arg-type]
                        selected=False,
                        proposed_html=str(
                            raw_rec.get("proposed_html")
                            or raw_rec.get("new_content")
                            or ""
                        ),
                    )
                )
            if slug not in seen_slugs:
                seen_slugs.append(slug)
            sections.append(
                SectionReview(
                    page_slug=slug,
                    section=section_name,
                    summary=str(raw_sec.get("summary") or ""),
                    recommendations=recs,
                )
            )

        return OpContent(
            sections=sections,
            page_slugs=seen_slugs or list(focus_pages or _STANDARD_PAGES),
            summary=str(parsed.get("summary") or ""),
        )
