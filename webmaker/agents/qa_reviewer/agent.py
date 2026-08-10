"""
webmaker.agents.qa_reviewer.agent
=================================
Agent 6 — QAReviewer.

Responsibility: review the final demo ONLY and report. Never fixes.

V2 dual-model path (aligned with the agent plan):
- Claude Sonnet 4.6 → content, SEO, German writing (via QAReviewer content_ai="claude")
- GPT → layout, UX, visual quality (this agent)
- Merge into one :class:`QAArtifact`

DeepSeek is NOT used on the V2 agent path (legacy Optimize→Fix still may).
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field

from webmaker.agents.base import AgentContext, BaseAgent
from webmaker.core.logging import get_logger
from webmaker.core.types import AIProvider, QAReport
from webmaker.schemas.qa import QAArtifact, QACheckResult
from webmaker.schemas.render import RenderResult

log = get_logger("agent.qa_reviewer")

_VISUAL_SYSTEM = (
    "You are a senior UX and visual design reviewer for German local business "
    "websites. Assess visual hierarchy, trust signals, navigation clarity, "
    "whitespace, and whether the demo looks significantly better than a typical "
    "outdated local-service site. Do NOT rewrite content. Do NOT re-audit SEO "
    "copy (another reviewer owns content). "
    "Respond with a SINGLE valid JSON object — no markdown, no fences."
)


class QAAgentInput(BaseModel):
    """Typed input for the QAReviewer agent."""

    model_config = ConfigDict(extra="forbid")

    render: RenderResult = Field(default_factory=RenderResult)


class QAReviewerAgent(BaseAgent[QAAgentInput, QAArtifact]):
    """Review the rendered demo and produce a dual-model :class:`QAArtifact`."""

    name = "qa_reviewer"
    input_model = QAAgentInput
    output_model = QAArtifact

    def __init__(self, context: AgentContext, *, reviewer=None, router=None) -> None:
        super().__init__(context)
        self._reviewer = reviewer
        self._router = router

    def _get_reviewer(self):
        if self._reviewer is None:
            from webmaker.modules.qa_reviewer import QAReviewer
            self._reviewer = QAReviewer(self._ctx.settings)
        return self._reviewer

    def _get_router(self):
        if self._router is None:
            from webmaker.modules.ai_router import AIRouter
            self._router = AIRouter(self._ctx.settings)
        return self._router

    def _run(self, data: QAAgentInput) -> QAArtifact:
        reviewer = self._get_reviewer()
        wp_url = data.render.wp_url or getattr(self._ctx.settings, "wordpress_url", "")

        # Claude-only content AI — no DeepSeek on the V2 path.
        report: QAReport = reviewer.review_from_directory(
            self._ctx.data_dir,
            wp_url=wp_url,
            skip_live_checks=False,
            skip_ai=False,
            content_ai="claude",
        )

        checks = [
            QACheckResult(
                name=getattr(c, "name", "") or "",
                passed=bool(getattr(c, "passed", False)),
                score=float(getattr(c, "score", 0.0) or 0.0),
                detail=getattr(c, "detail", "") or "",
            )
            for c in getattr(report, "checks", []) or []
        ]

        recommendations = list(getattr(report, "recommendations", []) or [])
        content_review = self._summarise_content_review(report)

        visual_review, visual_recs = self._gpt_visual_review(data.render, report)
        for rec in visual_recs:
            if rec and rec not in recommendations:
                recommendations.append(rec)

        return QAArtifact(
            wp_url=getattr(report, "wp_url", "") or wp_url,
            overall_score=float(getattr(report, "overall_score", 0.0) or 0.0),
            passed=bool(getattr(report, "passed", False)),
            checks=checks,
            recommendations=recommendations,
            content_review=content_review,
            visual_review=visual_review,
        )

    @staticmethod
    def _summarise_content_review(report: QAReport) -> str:
        """Build a short Claude content summary from the shared report fields."""
        parts: list[str] = []
        score = getattr(report, "overall_score", None)
        if score is not None:
            parts.append(f"content_score={float(score):.2f}; passed={bool(getattr(report, 'passed', False))}")
        claude_recs = [
            r for r in (getattr(report, "recommendations", []) or [])
            if str(r).startswith("[Claude content]")
        ]
        if claude_recs:
            parts.append("Claude findings: " + "; ".join(str(r)[17:].strip() for r in claude_recs[:5]))
        elif getattr(report, "recommendations", None):
            # Comparison comment / untagged recs still count as content-side.
            parts.append(
                "Content notes: "
                + "; ".join(str(r) for r in report.recommendations[:3] if not str(r).startswith("[GPT"))
            )
        return "\n".join(parts).strip()

    # ── GPT visual second opinion ────────────────────────────────────────────

    def _gpt_visual_review(
        self,
        render: RenderResult,
        report: QAReport,
    ) -> tuple[str, list[str]]:
        try:
            router = self._get_router()
            if not router.is_available(AIProvider.OPENAI):
                log.info("GPT unavailable — skipping visual QA second opinion")
                return "", []

            prompt = (
                f"DEMO URL: {render.wp_url or getattr(report, 'wp_url', '')}\n"
                f"THEME: {render.theme_applied or '(unknown)'}\n"
                f"TEMPLATE: {render.template_applied or '(none)'}\n"
                f"PAGES RENDERED: {', '.join(render.pages_rendered) or '(none)'}\n"
                f"CONTENT QA SCORE: {getattr(report, 'overall_score', 0.0)}\n"
                f"CONTENT QA PASSED: {getattr(report, 'passed', False)}\n"
                f"CONTENT RECOMMENDATIONS (context only):\n"
                + "\n".join(
                    f"- {r}" for r in (getattr(report, "recommendations", []) or [])[:8]
                )
                + "\n\n"
                "Assess LAYOUT, UX, and VISUAL QUALITY only. Return JSON:\n"
                "{\n"
                '  "summary": "2-4 sentence visual/UX verdict",\n'
                '  "looks_significantly_better": true,\n'
                '  "score": 0.0,\n'
                '  "strengths": ["..."],\n'
                '  "weaknesses": ["..."],\n'
                '  "recommendations": ["actionable visual/UX fixes"]\n'
                "}"
            )
            response = router.request(
                prompt,
                provider=AIProvider.OPENAI,
                system=_VISUAL_SYSTEM,
                task="qa_visual_review",
                temperature=0.2,
                max_tokens=1536,
                allow_fallback=False,
            )
            parsed = self._parse_json(response.text)
        except Exception as exc:  # noqa: BLE001
            log.warning("GPT visual QA failed ({e})", e=exc)
            return "", []

        if not parsed:
            return "", []

        summary = str(parsed.get("summary") or "").strip()
        better = bool(parsed.get("looks_significantly_better"))
        try:
            score = float(parsed.get("score") if parsed.get("score") is not None else 0.0)
        except (TypeError, ValueError):
            score = 0.0
        strengths = [str(s) for s in (parsed.get("strengths") or []) if str(s).strip()]
        weaknesses = [str(w) for w in (parsed.get("weaknesses") or []) if str(w).strip()]
        recs = [str(r) for r in (parsed.get("recommendations") or []) if str(r).strip()]

        visual = (
            f"{summary}\n"
            f"looks_significantly_better={better}; score={score:.2f}\n"
            f"strengths: {'; '.join(strengths) or '—'}\n"
            f"weaknesses: {'; '.join(weaknesses) or '—'}"
        ).strip()
        tagged = [f"[GPT visual] {r}" for r in recs]
        return visual, tagged

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
