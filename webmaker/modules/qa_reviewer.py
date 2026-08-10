"""
webmaker.modules.qa_reviewer
==============================
Performs a comprehensive quality review of the generated local WordPress
demo website.  Analyses structured project outputs and (optionally) the
live site; never modifies WordPress or regenerates content.

Pipeline
--------
1. Load generation_report.json, business_profile.json, optimized_*.json,
   meta_data.json, comparison_report.json, content_review.json
2. Run deterministic checks:
   - Business consistency (name, services, contact, areas)
   - Content quality (placeholders, empty sections, duplication)
   - SEO (meta titles/descriptions, H1 presence, alt text coverage)
   - WordPress structure (pages, menu, homepage, images)
   - Conversion readiness (CTA, contact, FAQ, trust signals)
   - Accessibility observations (alt text, heading hierarchy hints)
3. Optional live HTTP checks against the WordPress URL
4. DeepSeek AI auditor review (via AIRouter) — review only, never rewrite
5. Optional Claude second opinion when configured
6. Aggregate scores and write structured JSON reports

Primary class: QAReviewer
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from pydantic import BaseModel, Field

from webmaker.core.exceptions import AIError, QAError
from webmaker.core.logging import get_logger
from webmaker.core.prompts import load_prompt_or_default
from webmaker.core.schema import unwrap_json, write_versioned_json
from webmaker.core.types import AIProvider, GenerationResult, QACheck, QAReport
from webmaker.modules.ai_router import AIRouter

if TYPE_CHECKING:
    from webmaker.config.settings import Settings

log = get_logger("qa_reviewer")


# ── Constants ─────────────────────────────────────────────────────────────────

_FALLBACK_DEEPSEEK_SYSTEM = (
    "You are an experienced website auditor specialising in German local "
    "business websites. Review the provided website data carefully. "
    "Identify strengths, weaknesses, inconsistencies, missing information, "
    "and improvement opportunities. "
    "Do NOT rewrite any content. Do NOT suggest copying competitor text. "
    "Respond ONLY with a single valid JSON object — no markdown, no fences."
)

_FALLBACK_CLAUDE_SYSTEM = (
    "You are a senior content and UX reviewer. Provide a brief second-opinion "
    "audit of the website data. Do NOT rewrite content. "
    "Respond ONLY with a single valid JSON object."
)

_FALLBACK_CLAUDE_CONTENT_SYSTEM = (
    "You are a senior content, SEO, and German-language writing auditor for "
    "local business websites. Focus on factual consistency, SEO structure "
    "(titles, H1, meta), natural German copy, trust, and conversion clarity. "
    "Do NOT rewrite pages. Do NOT judge visual layout (another reviewer owns that). "
    "Respond ONLY with a single valid JSON object — no markdown, no fences."
)

_PLACEHOLDER_PATTERNS = (
    re.compile(r"\[MISSING INFORMATION\]", re.I),
    re.compile(r"\bTODO\b"),
    re.compile(r"\bTBD\b"),
    re.compile(r"lorem ipsum", re.I),
    re.compile(r"\[Company Name\]", re.I),
    re.compile(r"placeholder", re.I),
)

_STANDARD_PAGES = ("homepage", "about", "services", "contact", "faq")

# Severity weights for scoring (higher = more impact when failed)
_SEVERITY_WEIGHT = {"critical": 1.0, "high": 0.75, "medium": 0.5, "low": 0.25}

# Pass threshold for overall QAReport.passed
_PASS_THRESHOLD = 0.65


# ── Module-local models ───────────────────────────────────────────────────────

class QAIssue(BaseModel):
    """A single quality issue found during review."""

    category:              str = ""   # business | content | seo | accessibility | structure | conversion
    severity:              str = "medium"  # critical | high | medium | low
    affected_page:         str = ""
    description:           str = ""
    suggested_improvement: str = ""


class CategoryScore(BaseModel):
    """Score for one quality category."""

    category:    str   = ""
    score:       float = 0.0   # 0–100
    explanation: str   = ""
    issue_count: int   = 0


class WebsiteScore(BaseModel):
    """Aggregated website quality scores."""

    overall_website_quality: float = 0.0
    content_quality:         float = 0.0
    seo_quality:             float = 0.0
    accessibility:           float = 0.0
    conversion_readiness:    float = 0.0
    business_consistency:    float = 0.0
    categories:              list[CategoryScore] = Field(default_factory=list)
    explanations:            dict[str, str] = Field(default_factory=dict)
    scored_at:               str = ""


class DetailedQAReport(BaseModel):
    """Full structured QA output persisted to disk."""

    wp_url:           str = ""
    project_dir:      str = ""
    reviewed_at:      str = ""
    passed:           bool = False
    overall_score:    float = 0.0   # 0–1
    scores:           WebsiteScore = Field(default_factory=WebsiteScore)
    issues:           list[QAIssue] = Field(default_factory=list)
    strengths:        list[str] = Field(default_factory=list)
    weaknesses:       list[str] = Field(default_factory=list)
    recommendations:  list[str] = Field(default_factory=list)
    checks:           list[dict[str, Any]] = Field(default_factory=list)
    ai_review:        dict[str, Any] = Field(default_factory=dict)
    second_opinion:   dict[str, Any] = Field(default_factory=dict)
    errors:           list[str] = Field(default_factory=list)
    warnings:         list[str] = Field(default_factory=list)
    # Comparison vs the client's original live site
    significantly_better_than_original: bool = False
    comparison_comment: str = ""
    original_site_url:  str = ""


# ── Main class ────────────────────────────────────────────────────────────────

class QAReviewer:
    """Performs automated quality assurance on a generated WordPress site.

    Responsibilities:
    - Load prior module outputs and the generation report.
    - Run deterministic structural / content / SEO / accessibility checks.
    - Optionally probe the live WordPress URL (availability, SEO, alt, TTFB).
    - Ask DeepSeek (via AIRouter) for an auditor-style review.
    - Optionally ask Claude for a second opinion.
    - Score categories and write structured JSON reports.
    - NEVER modify the WordPress site or regenerate content.

    Args:
        settings:  Application settings instance.
        ai_router: Optional pre-constructed AIRouter.
    """

    def __init__(
        self,
        settings:  "Settings",
        ai_router: AIRouter | None = None,
    ) -> None:
        self._settings  = settings
        self._ai_router = ai_router or AIRouter(settings)
        self._http_timeout = 10.0
        log.debug("QAReviewer initialised")

    # ── Primary entry points ───────────────────────────────────────────────────

    def review_from_directory(
        self,
        project_dir: Path,
        *,
        wp_url:           str | None = None,
        skip_live_checks: bool = False,
        skip_ai:          bool = False,
        second_opinion:   bool = False,
        content_ai:       str = "legacy",
    ) -> QAReport:
        """Run a full QA review from a project directory's JSON outputs.

        Args:
            project_dir:      Client project directory (contains ``json/``).
            wp_url:           Override WordPress base URL.
            skip_live_checks: If True, skip HTTP probes against WordPress.
            skip_ai:          If True, skip DeepSeek / Claude AI review.
            second_opinion:   If True, also ask Claude when available (legacy).
            content_ai:       ``"legacy"`` = Claude second opinion + DeepSeek
                              primary (Optimize→Fix). ``"claude"`` = Claude only
                              for content/SEO/German (V2 agent path; GPT visual
                              is layered by QAReviewerAgent).

        Returns:
            Shared QAReport for downstream consumers.
        """
        project_dir = Path(project_dir)
        url = wp_url or self._settings.wordpress_url
        mode = (content_ai or "legacy").strip().lower()
        if mode not in ("legacy", "claude"):
            mode = "legacy"

        log.info("=== QA review started: {d} (content_ai={m}) ===", d=project_dir.name, m=mode)

        report = DetailedQAReport(
            wp_url      = url,
            project_dir = str(project_dir),
            reviewed_at = datetime.now(timezone.utc).isoformat(),
        )

        # ── 1. Load inputs ────────────────────────────────────────────────────
        biz      = self._load_json(project_dir / "json" / "business_profile.json", default={})
        gen_rep  = self._load_json(project_dir / "json" / "generation_report.json", default={})
        meta     = self._load_json(project_dir / "json" / "meta_data.json", default={})
        comp     = self._load_json(project_dir / "json" / "comparison_report.json", default={})
        pages    = self._load_optimized_pages(project_dir)
        images   = self._load_json(project_dir / "json" / "images.json", default=[])

        if not isinstance(biz, dict):
            biz = {}
        if not isinstance(gen_rep, dict):
            gen_rep = {}
        if not isinstance(meta, dict):
            meta = {}
        if not isinstance(comp, dict):
            comp = {}
        if not isinstance(images, list):
            images = []

        if not pages and not gen_rep:
            report.warnings.append(
                "No optimized content or generation report found — "
                "QA coverage will be limited"
            )
            log.warning("Limited QA inputs in {d}", d=project_dir)

        log.info(
            "Files loaded: {n} optimized pages, business={b}, generation={g}",
            n=len(pages), b=bool(biz), g=bool(gen_rep),
        )

        # ── 2. Deterministic checks ───────────────────────────────────────────
        issues: list[QAIssue] = []
        checks: list[QACheck] = []

        issues += self._check_business_consistency(biz, pages, gen_rep)
        issues += self._check_content_quality(pages)
        issues += self._check_seo_from_json(pages, meta)
        issues += self._check_accessibility_from_json(pages, images)
        issues += self._check_wordpress_structure(gen_rep, pages)
        issues += self._check_conversion(pages, biz)

        # Map deterministic findings into QACheck list
        checks += self._issues_to_checks(issues)

        # ── 3. Live WordPress checks (optional) ───────────────────────────────
        page_slugs = list(pages.keys()) or list(
            p.get("slug", "") for p in gen_rep.get("pages_created", [])
            if isinstance(p, dict)
        )
        if not page_slugs and isinstance(gen_rep.get("pages_created"), list):
            page_slugs = [
                s for s in gen_rep["pages_created"] if isinstance(s, str)
            ]

        if not skip_live_checks and url:
            try:
                live = self._run_live_checks(url, page_slugs or list(_STANDARD_PAGES))
                checks.extend(live)
            except Exception as exc:
                report.warnings.append(f"Live checks skipped: {exc}")
                log.warning("Live checks failed: {e}", e=exc)

        # ── 4. AI content review ──────────────────────────────────────────────
        # V2 (content_ai="claude"): Claude Sonnet only for content/SEO/German.
        # Legacy: Claude second opinion + DeepSeek primary (Optimize→Fix).
        if not skip_ai:
            sonnet_result: dict[str, Any] = {}
            deepseek_result: dict[str, Any] = {}

            if mode == "claude":
                if self._ai_router.is_available(AIProvider.CLAUDE):
                    try:
                        sonnet_result = self._run_ai_review(
                            biz, pages, meta, gen_rep, issues,
                            provider=AIProvider.CLAUDE,
                        )
                        log.info("Claude content QA review complete")
                    except Exception as exc:
                        report.warnings.append(f"Claude QA skipped: {exc}")
                        log.warning("Claude QA failed: {e}", e=exc)
                else:
                    report.warnings.append(
                        "AI review skipped: Claude not available "
                        "(set CLAUDE_API_KEY for V2 content QA)"
                    )
            else:
                if self._ai_router.is_available(AIProvider.CLAUDE):
                    try:
                        sonnet_result = self._run_second_opinion(biz, pages, issues)
                        log.info("Claude Sonnet QA review complete")
                    except Exception as exc:
                        report.warnings.append(f"Claude QA skipped: {exc}")
                        log.warning("Claude QA failed: {e}", e=exc)

                if self._ai_router.is_available(AIProvider.DEEPSEEK):
                    try:
                        deepseek_result = self._run_ai_review(
                            biz, pages, meta, gen_rep, issues,
                            provider=AIProvider.DEEPSEEK,
                        )
                        log.info("DeepSeek QA review complete")
                    except AIError as exc:
                        report.warnings.append(f"DeepSeek QA skipped: {exc}")
                        log.warning("DeepSeek QA failed: {e}", e=exc)
                    except Exception as exc:
                        report.warnings.append(f"DeepSeek QA error: {exc}")
                        log.error("Unexpected DeepSeek QA error: {e}", e=exc)

            ai_result = self._merge_ai_reviews(sonnet_result, deepseek_result)
            report.ai_review = ai_result
            report.second_opinion = sonnet_result or {}

            if not sonnet_result and not deepseek_result:
                report.warnings.append(
                    "AI review skipped: no content AI provider available"
                )

            for raw_issue in ai_result.get("issues", []):
                if isinstance(raw_issue, dict):
                    issues.append(self._dict_to_issue(raw_issue))
            for s in ai_result.get("strengths", []):
                if s and s not in report.strengths:
                    report.strengths.append(str(s))
            for w in ai_result.get("weaknesses", []):
                if w and w not in report.weaknesses:
                    report.weaknesses.append(str(w))
            for r in ai_result.get("recommendations", []):
                if r and r not in report.recommendations:
                    # Tag Claude-only V2 recs so the merged report is attributable.
                    text = str(r)
                    if mode == "claude" and not text.startswith("[Claude"):
                        text = f"[Claude content] {text}"
                    if text not in report.recommendations:
                        report.recommendations.append(text)

            # Legacy flag: always attempt dual review when keys exist
            if second_opinion and mode == "legacy" and not sonnet_result and self._ai_router.is_available(AIProvider.CLAUDE):
                try:
                    report.second_opinion = self._run_second_opinion(
                        biz, pages, issues
                    )
                except Exception as exc:
                    report.warnings.append(f"Second opinion skipped: {exc}")

        # ── 4b. Compare demo vs original client site ───────────────────────────
        original_url = str(biz.get("source_url") or "").strip()
        report.original_site_url = original_url
        try:
            cmp = self._compare_to_original(
                original_url=original_url,
                demo_url=url,
                pages=pages,
                gen_rep=gen_rep,
                issues=issues,
            )
            report.significantly_better_than_original = bool(
                cmp.get("significantly_better_than_original")
            )
            report.comparison_comment = str(cmp.get("comparison_comment") or "")
            if report.comparison_comment:
                report.recommendations.insert(0, report.comparison_comment)
        except Exception as exc:
            report.warnings.append(f"Original-site comparison skipped: {exc}")
            log.warning("Original comparison failed: {e}", e=exc)

        # ── 5. Scoring ────────────────────────────────────────────────────────
        scores = self._compute_scores(issues, checks, pages, gen_rep, biz)
        report.scores = scores
        report.issues = issues
        report.checks = [c.model_dump() for c in checks]
        report.overall_score = round(scores.overall_website_quality / 100.0, 3)
        report.passed = report.overall_score >= _PASS_THRESHOLD
        # Improve-loop gate uses significantly_better_than_original separately

        # Fill strengths/weaknesses from deterministic findings if AI skipped
        if not report.strengths:
            report.strengths = self._infer_strengths(pages, gen_rep, issues)
        if not report.weaknesses:
            report.weaknesses = [
                i.description for i in issues
                if i.severity in ("critical", "high")
            ][:10]
        if not report.recommendations:
            report.recommendations = [
                i.suggested_improvement for i in issues
                if i.suggested_improvement
            ][:15]

        # ── 6. Persist reports ────────────────────────────────────────────────
        self._save_reports(project_dir, report)

        log.info(
            "QA complete — score={s:.0%} passed={p} issues={n}",
            s=report.overall_score, p=report.passed, n=len(issues),
        )

        return QAReport(
            wp_url          = url,
            checks          = checks,
            overall_score   = report.overall_score,
            recommendations = report.recommendations,
            passed          = report.passed,
        )

    def review(self, generation: GenerationResult) -> QAReport:
        """Run all QA checks against *generation* and return a report.

        Resolves the project directory from ``generation.wp_path``'s sibling
        ``projects/`` tree when possible; otherwise runs live-only checks
        against ``generation.wp_url``.

        Args:
            generation: Output from WordPressGenerator.generate().

        Returns:
            QAReport with individual check results and overall score.

        Raises:
            QAError: If the WordPress site cannot be reached and no project
                     JSON is available.
        """
        # Prefer project dir inferred from generation metadata
        project_dir = self._infer_project_dir(generation)
        if project_dir and project_dir.exists():
            return self.review_from_directory(
                project_dir,
                wp_url=generation.wp_url,
            )

        # Fallback: live-only review
        if not generation.wp_url:
            raise QAError(
                "Cannot run QA: no project directory and no WordPress URL"
            )

        log.info("Running live-only QA against {u}", u=generation.wp_url)
        pages = generation.pages_created or list(_STANDARD_PAGES)
        checks: list[QACheck] = []
        try:
            checks.extend(self.check_page_availability(generation.wp_url, pages))
            checks.append(self.check_broken_links(generation.wp_url))
            checks.extend(self.check_seo_completeness(generation.wp_url, pages))
            checks.append(self.check_image_alt_text(generation.wp_url))
            checks.append(self.check_performance(generation.wp_url))
        except Exception as exc:
            raise QAError(
                f"WordPress site unreachable: {exc}",
                url=generation.wp_url,
            ) from exc

        score = self.calculate_score(checks)
        return QAReport(
            wp_url        = generation.wp_url,
            checks        = checks,
            overall_score = score,
            recommendations = [
                c.detail for c in checks if not c.passed and c.detail
            ][:10],
            passed = score >= _PASS_THRESHOLD,
        )

    # ── Live HTTP check methods (Phase 2 stubs) ────────────────────────────────

    def check_page_availability(
        self,
        wp_url: str,
        pages:  list[str],
    ) -> list[QACheck]:
        """Verify each page slug returns a 200 response.

        Args:
            wp_url: WordPress base URL.
            pages:  List of page slugs to check.

        Returns:
            One QACheck per page.
        """
        results: list[QACheck] = []
        for slug in pages:
            path = "/" if slug in ("home", "homepage", "") else f"/{slug.strip('/')}/"
            url  = urljoin(wp_url.rstrip("/") + "/", path.lstrip("/"))
            try:
                status, _body, _elapsed = self._http_get(url)
                passed = 200 <= status < 400
                results.append(QACheck(
                    name   = f"page_available:{slug}",
                    passed = passed,
                    score  = 1.0 if passed else 0.0,
                    detail = f"HTTP {status} for {url}",
                ))
            except Exception as exc:
                results.append(QACheck(
                    name   = f"page_available:{slug}",
                    passed = False,
                    score  = 0.0,
                    detail = f"Request failed for {url}: {exc}",
                ))
        return results

    def check_broken_links(self, wp_url: str) -> QACheck:
        """Scan the homepage HTML for broken internal links.

        Args:
            wp_url: WordPress base URL.

        Returns:
            QACheck with broken-link count in detail.
        """
        try:
            status, html, _ = self._http_get(wp_url)
            if status >= 400 or not html:
                return QACheck(
                    name   = "broken_links",
                    passed = False,
                    score  = 0.0,
                    detail = f"Could not fetch homepage (HTTP {status})",
                )

            hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, re.I)
            base  = wp_url.rstrip("/")
            broken: list[str] = []
            checked = 0

            for href in hrefs:
                if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                if href.startswith("//"):
                    continue
                # Only check same-origin / relative links
                if href.startswith("http") and not href.startswith(base):
                    continue
                full = href if href.startswith("http") else urljoin(base + "/", href.lstrip("/"))
                checked += 1
                if checked > 30:
                    break
                try:
                    st, _, _ = self._http_get(full)
                    if st >= 400:
                        broken.append(f"{full} ({st})")
                except Exception:
                    broken.append(full)

            passed = len(broken) == 0
            return QACheck(
                name   = "broken_links",
                passed = passed,
                score  = 1.0 if passed else max(0.0, 1.0 - len(broken) * 0.1),
                detail = (
                    f"Checked {checked} links, {len(broken)} broken"
                    + (f": {', '.join(broken[:5])}" if broken else "")
                ),
            )
        except Exception as exc:
            return QACheck(
                name   = "broken_links",
                passed = False,
                score  = 0.0,
                detail = f"Link scan failed: {exc}",
            )

    def check_seo_completeness(
        self,
        wp_url: str,
        pages:  list[str],
    ) -> list[QACheck]:
        """Confirm ``<title>`` and meta description exist on each page.

        Args:
            wp_url: WordPress base URL.
            pages:  Page slugs to inspect.

        Returns:
            One QACheck per page.
        """
        results: list[QACheck] = []
        for slug in pages:
            path = "/" if slug in ("home", "homepage", "") else f"/{slug.strip('/')}/"
            url  = urljoin(wp_url.rstrip("/") + "/", path.lstrip("/"))
            try:
                status, html, _ = self._http_get(url)
                if status >= 400 or not html:
                    results.append(QACheck(
                        name   = f"seo:{slug}",
                        passed = False,
                        score  = 0.0,
                        detail = f"HTTP {status}",
                    ))
                    continue

                has_title = bool(re.search(r"<title>[^<]+</title>", html, re.I))
                has_desc  = bool(re.search(
                    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'][^"\']+["\']',
                    html, re.I,
                )) or bool(re.search(
                    r'<meta[^>]+content=["\'][^"\']+["\'][^>]+name=["\']description["\']',
                    html, re.I,
                ))
                has_h1 = bool(re.search(r"<h1[\s>]", html, re.I))

                score = (has_title + has_desc + has_h1) / 3.0
                missing = []
                if not has_title:
                    missing.append("title")
                if not has_desc:
                    missing.append("meta description")
                if not has_h1:
                    missing.append("H1")

                results.append(QACheck(
                    name   = f"seo:{slug}",
                    passed = score >= 0.67,
                    score  = round(score, 2),
                    detail = (
                        "OK" if not missing
                        else f"Missing: {', '.join(missing)}"
                    ),
                ))
            except Exception as exc:
                results.append(QACheck(
                    name   = f"seo:{slug}",
                    passed = False,
                    score  = 0.0,
                    detail = str(exc),
                ))
        return results

    def check_image_alt_text(self, wp_url: str) -> QACheck:
        """Report images on the homepage that are missing alt attributes.

        Args:
            wp_url: WordPress base URL.

        Returns:
            QACheck with number of violations in detail.
        """
        try:
            status, html, _ = self._http_get(wp_url)
            if status >= 400 or not html:
                return QACheck(
                    name   = "image_alt_text",
                    passed = False,
                    score  = 0.0,
                    detail = f"Could not fetch homepage (HTTP {status})",
                )

            imgs = re.findall(r"<img\b[^>]*>", html, re.I)
            missing = 0
            for tag in imgs:
                if not re.search(r'\balt\s*=', tag, re.I):
                    missing += 1
                elif re.search(r'\balt\s*=\s*["\']\s*["\']', tag, re.I):
                    missing += 1

            total = len(imgs) or 1
            score = max(0.0, 1.0 - missing / total)
            return QACheck(
                name   = "image_alt_text",
                passed = missing == 0,
                score  = round(score, 2),
                detail = f"{missing}/{len(imgs)} images missing or empty alt",
            )
        except Exception as exc:
            return QACheck(
                name   = "image_alt_text",
                passed = False,
                score  = 0.0,
                detail = str(exc),
            )

    def check_performance(self, wp_url: str) -> QACheck:
        """Measure time-to-first-byte and page weight for the homepage.

        Args:
            wp_url: WordPress base URL.

        Returns:
            QACheck with performance summary in detail.
        """
        try:
            status, body, elapsed = self._http_get(wp_url)
            weight_kb = len(body.encode("utf-8", errors="replace")) / 1024.0
            ttfb_ok   = elapsed < 3.0
            weight_ok = weight_kb < 2048  # 2 MB soft limit for local demo
            score = (0.6 if ttfb_ok else 0.2) + (0.4 if weight_ok else 0.1)
            return QACheck(
                name   = "performance",
                passed = ttfb_ok and weight_ok and status < 400,
                score  = round(min(score, 1.0), 2),
                detail = (
                    f"HTTP {status}, TTFB={elapsed:.2f}s, "
                    f"weight={weight_kb:.0f} KB"
                ),
            )
        except Exception as exc:
            return QACheck(
                name   = "performance",
                passed = False,
                score  = 0.0,
                detail = str(exc),
            )

    def capture_screenshots(
        self,
        wp_url:     str,
        pages:      list[str],
        output_dir: Path,
    ) -> list[Path]:
        """Save screenshots of each page for human review via Playwright.

        Args:
            wp_url:     WordPress base URL.
            pages:      Page slugs to screenshot.
            output_dir: Directory to save PNG files.

        Returns:
            List of paths to saved screenshots.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("Playwright not available — screenshots skipped")
            return saved

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                for slug in pages:
                    path = "/" if slug in ("home", "homepage", "") else f"/{slug.strip('/')}/"
                    url  = urljoin(wp_url.rstrip("/") + "/", path.lstrip("/"))
                    dest = output_dir / f"qa_{self._slugify(slug) or 'home'}.png"
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        page.screenshot(path=str(dest), full_page=True)
                        saved.append(dest)
                        log.info("Screenshot saved: {p}", p=dest.name)
                    except Exception as exc:
                        log.warning("Screenshot failed for {u}: {e}", u=url, e=exc)
                browser.close()
        except Exception as exc:
            log.warning("Screenshot session failed: {e}", e=exc)

        return saved

    def calculate_score(self, checks: list[QACheck]) -> float:
        """Compute a 0.0 – 1.0 weighted overall quality score.

        Args:
            checks: All QACheck results from individual check methods.

        Returns:
            Overall score.
        """
        if not checks:
            return 0.0
        total = sum(max(0.0, min(1.0, c.score)) for c in checks)
        return round(total / len(checks), 3)

    # ── Private: deterministic checks ──────────────────────────────────────────

    def _check_business_consistency(
        self,
        biz:     dict,
        pages:   dict[str, dict],
        gen_rep: dict,
    ) -> list[QAIssue]:
        issues: list[QAIssue] = []
        name = str(biz.get("company_name") or "").strip()
        services = biz.get("main_services") or []
        areas = biz.get("service_areas") or []
        phone = str(biz.get("contact_phone") or "").strip()
        email = str(biz.get("contact_email") or "").strip()

        if not name:
            issues.append(QAIssue(
                category="business", severity="critical", affected_page="all",
                description="Company name missing from business profile",
                suggested_improvement="Ensure business_profile.json includes company_name",
            ))

        # Company name should appear in homepage content when present
        home = pages.get("homepage") or pages.get("home") or {}
        home_text = json.dumps(home, ensure_ascii=False)
        if name and home and name.lower() not in home_text.lower():
            issues.append(QAIssue(
                category="business", severity="high", affected_page="homepage",
                description=f"Company name '{name}' not found in homepage content",
                suggested_improvement="Include the company name in the hero or intro",
            ))

        # Services mentioned
        if services:
            svc_page = pages.get("services") or {}
            svc_text = json.dumps(svc_page, ensure_ascii=False).lower()
            missing_svc = [
                s for s in services
                if isinstance(s, str) and s.lower() not in svc_text
            ]
            if missing_svc and svc_page:
                issues.append(QAIssue(
                    category="business", severity="medium", affected_page="services",
                    description=f"Services not reflected in content: {', '.join(missing_svc[:5])}",
                    suggested_improvement="Ensure all main services appear on the services page",
                ))
        else:
            issues.append(QAIssue(
                category="business", severity="high", affected_page="services",
                description="No main services listed in business profile",
                suggested_improvement="Populate main_services in business_profile.json",
            ))

        # Contact info
        contact = pages.get("contact") or {}
        contact_text = json.dumps(contact, ensure_ascii=False)
        if phone and contact and phone not in contact_text:
            # Phone may only be in profile — soft warning if contact page lacks it
            issues.append(QAIssue(
                category="business", severity="low", affected_page="contact",
                description="Phone number from profile not present in contact page JSON",
                suggested_improvement="Display the business phone number on the contact page",
            ))
        if not phone and not email:
            issues.append(QAIssue(
                category="business", severity="high", affected_page="contact",
                description="No contact phone or email in business profile",
                suggested_improvement="Add contact_phone and/or contact_email",
            ))

        if not areas:
            issues.append(QAIssue(
                category="business", severity="medium", affected_page="all",
                description="No service areas defined in business profile",
                suggested_improvement="Add geographic service areas for local SEO",
            ))

        # Site title consistency with generation report
        site_title = str(gen_rep.get("site_title") or "")
        if name and site_title and name.lower() not in site_title.lower():
            issues.append(QAIssue(
                category="business", severity="medium", affected_page="settings",
                description=f"Site title '{site_title}' does not include company name '{name}'",
                suggested_improvement="Align WordPress site title with company name",
            ))

        return issues

    def _check_content_quality(self, pages: dict[str, dict]) -> list[QAIssue]:
        issues: list[QAIssue] = []
        seen_snippets: dict[str, str] = {}

        for slug, content in pages.items():
            text = json.dumps(content, ensure_ascii=False)

            if not content or text in ("{}", "null"):
                issues.append(QAIssue(
                    category="content", severity="critical", affected_page=slug,
                    description=f"Page '{slug}' content is empty",
                    suggested_improvement="Regenerate content for this page via ContentOptimizer",
                ))
                continue

            # Placeholders
            for pat in _PLACEHOLDER_PATTERNS:
                matches = pat.findall(text)
                if matches:
                    issues.append(QAIssue(
                        category="content", severity="high", affected_page=slug,
                        description=f"Placeholder text found on '{slug}': {matches[0]}",
                        suggested_improvement="Replace placeholders with verified business facts or remove the section",
                    ))
                    break

            # Missing hero / heading
            has_heading = bool(
                (isinstance(content.get("hero"), dict) and content["hero"].get("heading"))
                or content.get("hero_heading")
                or content.get("title")
                or content.get("body_html")
            )
            if not has_heading:
                issues.append(QAIssue(
                    category="content", severity="medium", affected_page=slug,
                    description=f"No clear heading/hero found on '{slug}'",
                    suggested_improvement="Add an H1 / hero heading for this page",
                ))

            # Very short content
            plain = re.sub(r"<[^>]+>", " ", text)
            words = plain.split()
            if len(words) < 30 and slug in _STANDARD_PAGES:
                issues.append(QAIssue(
                    category="content", severity="medium", affected_page=slug,
                    description=f"Content on '{slug}' appears very short ({len(words)} tokens)",
                    suggested_improvement="Expand with factual service / about information",
                ))

            # Near-duplicate intros across pages
            intro = str(
                content.get("intro")
                or (content.get("hero", {}) or {}).get("subheading")
                or ""
            ).strip().lower()[:80]
            if intro and len(intro) > 20:
                if intro in seen_snippets:
                    issues.append(QAIssue(
                        category="content", severity="low", affected_page=slug,
                        description=f"Duplicated text snippet also used on '{seen_snippets[intro]}'",
                        suggested_improvement="Differentiate page intros to avoid repetition",
                    ))
                else:
                    seen_snippets[intro] = slug

        # Required pages
        if "homepage" not in pages and "home" not in pages:
            issues.append(QAIssue(
                category="content", severity="critical", affected_page="homepage",
                description="Required page 'homepage' is missing from optimized content",
                suggested_improvement="Generate optimized_homepage.json before publishing",
            ))
        if "contact" not in pages:
            issues.append(QAIssue(
                category="content", severity="critical", affected_page="contact",
                description="Required page 'contact' is missing from optimized content",
                suggested_improvement="Generate optimized_contact.json before publishing",
            ))

        return issues

    def _check_seo_from_json(
        self,
        pages: dict[str, dict],
        meta:  dict,
    ) -> list[QAIssue]:
        issues: list[QAIssue] = []

        for slug, content in pages.items():
            m = meta.get(slug) if isinstance(meta.get(slug), dict) else {}
            title = (
                (m or {}).get("title")
                or content.get("meta_title")
                or ""
            )
            desc = (
                (m or {}).get("description")
                or content.get("meta_description")
                or ""
            )

            if not title:
                issues.append(QAIssue(
                    category="seo", severity="high", affected_page=slug,
                    description=f"Missing meta title for '{slug}'",
                    suggested_improvement="Add meta_title in optimized content or meta_data.json",
                ))
            elif len(str(title)) > 60:
                issues.append(QAIssue(
                    category="seo", severity="low", affected_page=slug,
                    description=f"Meta title for '{slug}' exceeds 60 characters ({len(str(title))})",
                    suggested_improvement="Shorten meta title to ≤60 characters",
                ))

            if not desc:
                issues.append(QAIssue(
                    category="seo", severity="high", affected_page=slug,
                    description=f"Missing meta description for '{slug}'",
                    suggested_improvement="Add meta_description in optimized content or meta_data.json",
                ))
            elif len(str(desc)) > 160:
                issues.append(QAIssue(
                    category="seo", severity="low", affected_page=slug,
                    description=f"Meta description for '{slug}' exceeds 160 characters",
                    suggested_improvement="Shorten meta description to ≤160 characters",
                ))

            # H1 structure hint from content
            has_h1 = bool(
                (isinstance(content.get("hero"), dict) and content["hero"].get("heading"))
                or content.get("hero_heading")
                or (content.get("body_html") and "<h1" in str(content["body_html"]).lower())
            )
            if not has_h1:
                issues.append(QAIssue(
                    category="seo", severity="medium", affected_page=slug,
                    description=f"No H1 / hero heading detected for '{slug}'",
                    suggested_improvement="Ensure each page has exactly one clear H1",
                ))

        return issues

    def _check_accessibility_from_json(
        self,
        pages:  dict[str, dict],
        images: list,
    ) -> list[QAIssue]:
        issues: list[QAIssue] = []

        # Image alt coverage from crawler metadata
        if images:
            total = 0
            missing_alt = 0
            for img in images:
                if not isinstance(img, dict):
                    continue
                total += 1
                alt = str(img.get("alt_text") or "").strip()
                if not alt:
                    missing_alt += 1
            if total and missing_alt / total > 0.3:
                issues.append(QAIssue(
                    category="accessibility", severity="medium", affected_page="media",
                    description=f"{missing_alt}/{total} crawled images lack alt text",
                    suggested_improvement="Add descriptive alt text for key images before publishing",
                ))

        # Link description hints in contact CTAs
        for slug, content in pages.items():
            text = json.dumps(content, ensure_ascii=False).lower()
            if "click here" in text or "hier klicken" in text:
                issues.append(QAIssue(
                    category="accessibility", severity="low", affected_page=slug,
                    description=f"Generic link text ('click here') found on '{slug}'",
                    suggested_improvement="Use descriptive link labels (e.g. 'Contact us for a quote')",
                ))

        return issues

    def _check_wordpress_structure(
        self,
        gen_rep: dict,
        pages:   dict[str, dict],
    ) -> list[QAIssue]:
        issues: list[QAIssue] = []

        if not gen_rep:
            issues.append(QAIssue(
                category="structure", severity="high", affected_page="all",
                description="generation_report.json missing — WordPress structure cannot be verified",
                suggested_improvement="Run WordPressGenerator before QA",
            ))
            return issues

        created = gen_rep.get("pages_created") or []
        if not created:
            issues.append(QAIssue(
                category="structure", severity="critical", affected_page="all",
                description="No pages listed in generation report",
                suggested_improvement="Re-run WordPressGenerator and confirm pages were created",
            ))
        else:
            slugs = set()
            for p in created:
                if isinstance(p, dict):
                    slugs.add(str(p.get("slug", "")))
                elif isinstance(p, str):
                    slugs.add(p)
            for required in ("home", "contact"):
                # homepage may be stored as "home"
                if required == "home" and ("home" in slugs or "homepage" in slugs):
                    continue
                if required not in slugs and required != "home":
                    issues.append(QAIssue(
                        category="structure", severity="high", affected_page=required,
                        description=f"Expected page '{required}' not in generation report",
                        suggested_improvement=f"Ensure '{required}' page is created in WordPress",
                    ))

        if not gen_rep.get("menu_created"):
            issues.append(QAIssue(
                category="structure", severity="high", affected_page="navigation",
                description="Primary navigation menu was not created",
                suggested_improvement="Create and assign the Primary menu in WordPress",
            ))

        if gen_rep.get("homepage_id") in (None, 0, ""):
            issues.append(QAIssue(
                category="structure", severity="medium", affected_page="homepage",
                description="Static homepage was not set in generation report",
                suggested_improvement="Set a static front page in WordPress settings",
            ))

        imported = gen_rep.get("images_imported") or []
        if not imported:
            issues.append(QAIssue(
                category="structure", severity="low", affected_page="media",
                description="No images were imported into the Media Library",
                suggested_improvement="Import key brand images for a more complete demo",
            ))

        if gen_rep.get("errors"):
            for err in gen_rep["errors"][:5]:
                issues.append(QAIssue(
                    category="structure", severity="high", affected_page="generation",
                    description=f"Generation error: {err}",
                    suggested_improvement="Resolve WordPressGenerator errors and regenerate",
                ))

        return issues

    def _check_conversion(
        self,
        pages: dict[str, dict],
        biz:   dict,
    ) -> list[QAIssue]:
        issues: list[QAIssue] = []

        home = pages.get("homepage") or pages.get("home") or {}
        home_text = json.dumps(home, ensure_ascii=False).lower()

        has_cta = any(
            k in home_text
            for k in ("cta", "kontakt", "contact", "anfragen", "quote", "call")
        ) or bool(
            isinstance(home.get("hero"), dict) and home["hero"].get("cta_primary")
        ) or bool(home.get("cta_section"))

        if home and not has_cta:
            issues.append(QAIssue(
                category="conversion", severity="high", affected_page="homepage",
                description="No clear call-to-action detected on homepage",
                suggested_improvement="Add a primary CTA button (e.g. Get a free quote)",
            ))

        if "contact" not in pages:
            issues.append(QAIssue(
                category="conversion", severity="critical", affected_page="contact",
                description="Contact page content is missing",
                suggested_improvement="Generate and publish a contact page",
            ))

        if "faq" not in pages:
            issues.append(QAIssue(
                category="conversion", severity="medium", affected_page="faq",
                description="FAQ page content is missing",
                suggested_improvement="Add an FAQ page to address common customer questions",
            ))

        trust = biz.get("trust_signals") or []
        why = (home.get("why_choose_us") or {}) if isinstance(home.get("why_choose_us"), dict) else {}
        if not trust and not why.get("points"):
            issues.append(QAIssue(
                category="conversion", severity="medium", affected_page="homepage",
                description="No trust signals or 'Why choose us' section found",
                suggested_improvement="Add certifications, experience, or guarantees that are factually verified",
            ))

        if "services" not in pages:
            issues.append(QAIssue(
                category="conversion", severity="high", affected_page="services",
                description="Services page content is missing",
                suggested_improvement="Publish a clear services overview page",
            ))

        return issues

    # ── Private: scoring ───────────────────────────────────────────────────────

    def _compute_scores(
        self,
        issues:  list[QAIssue],
        checks:  list[QACheck],
        pages:   dict[str, dict],
        gen_rep: dict,
        biz:     dict,
    ) -> WebsiteScore:
        categories = (
            "business_consistency",
            "content_quality",
            "seo_quality",
            "accessibility",
            "conversion_readiness",
            "structure",
        )
        cat_map = {
            "business":     "business_consistency",
            "content":      "content_quality",
            "seo":          "seo_quality",
            "accessibility": "accessibility",
            "conversion":   "conversion_readiness",
            "structure":    "structure",
        }

        # Start each category at 100, deduct by severity
        scores: dict[str, float] = {c: 100.0 for c in categories}
        counts: dict[str, int] = {c: 0 for c in categories}

        for issue in issues:
            key = cat_map.get(issue.category, "content_quality")
            if key not in scores:
                continue
            deduct = _SEVERITY_WEIGHT.get(issue.severity, 0.5) * 12
            scores[key] = max(0.0, scores[key] - deduct)
            counts[key] += 1

        # Boost from live checks average (if any)
        live_scores = [c.score for c in checks if c.name.startswith(
            ("page_available", "seo:", "broken_links", "image_alt", "performance")
        )]
        if live_scores:
            live_avg = sum(live_scores) / len(live_scores) * 100
            # Blend lightly into seo / structure
            scores["seo_quality"] = round((scores["seo_quality"] * 0.7 + live_avg * 0.3), 1)
            scores["structure"] = round((scores["structure"] * 0.7 + live_avg * 0.3), 1)

        # Presence bonuses
        if pages.get("homepage") or pages.get("home"):
            scores["content_quality"] = min(100.0, scores["content_quality"] + 2)
        if gen_rep.get("menu_created"):
            scores["structure"] = min(100.0, scores["structure"] + 3)
        if biz.get("company_name"):
            scores["business_consistency"] = min(100.0, scores["business_consistency"] + 2)

        explanations = {
            "business_consistency": (
                f"{counts['business_consistency']} consistency issue(s); "
                "checks company name, services, areas, and contact data"
            ),
            "content_quality": (
                f"{counts['content_quality']} content issue(s); "
                "checks placeholders, length, headings, and duplication"
            ),
            "seo_quality": (
                f"{counts['seo_quality']} SEO issue(s); "
                "checks meta titles, descriptions, and H1 presence"
            ),
            "accessibility": (
                f"{counts['accessibility']} accessibility observation(s); "
                "checks alt text coverage and link labels"
            ),
            "conversion_readiness": (
                f"{counts['conversion_readiness']} conversion issue(s); "
                "checks CTAs, contact, FAQ, trust, and services"
            ),
            "structure": (
                f"{counts['structure']} structure issue(s); "
                "checks pages, menu, homepage, and media from generation report"
            ),
        }

        category_models = [
            CategoryScore(
                category=c,
                score=round(scores[c], 1),
                explanation=explanations[c],
                issue_count=counts[c],
            )
            for c in categories
        ]

        # Overall = weighted average (structure + content + business weighted higher)
        weights = {
            "business_consistency": 0.20,
            "content_quality":      0.25,
            "seo_quality":          0.15,
            "accessibility":        0.10,
            "conversion_readiness": 0.15,
            "structure":            0.15,
        }
        overall = sum(scores[c] * weights[c] for c in categories)

        return WebsiteScore(
            overall_website_quality = round(overall, 1),
            content_quality         = round(scores["content_quality"], 1),
            seo_quality             = round(scores["seo_quality"], 1),
            accessibility           = round(scores["accessibility"], 1),
            conversion_readiness    = round(scores["conversion_readiness"], 1),
            business_consistency    = round(scores["business_consistency"], 1),
            categories              = category_models,
            explanations            = {
                "overall": (
                    f"Weighted average of six categories "
                    f"(content 25%, business 20%, SEO/conversion/structure 15% each, "
                    f"accessibility 10%). Score={overall:.1f}/100."
                ),
                **explanations,
            },
            scored_at = datetime.now(timezone.utc).isoformat(),
        )

    # ── Private: AI review ─────────────────────────────────────────────────────

    def _compare_to_original(
        self,
        *,
        original_url: str,
        demo_url: str,
        pages: dict[str, dict],
        gen_rep: dict[str, Any],
        issues: list,
    ) -> dict[str, Any]:
        """Ask Claude whether the demo is significantly better than the original."""
        if not self._ai_router.is_available(AIProvider.CLAUDE):
            # Heuristic fallback without Claude
            critical = sum(
                1 for i in issues
                if getattr(i, "severity", "") in ("critical", "high")
            )
            better = critical <= 2 and len(pages) >= 3
            return {
                "significantly_better_than_original": better,
                "comparison_comment": (
                    "Heuristic: demo has structured pages and limited high-severity issues."
                    if better else
                    "Heuristic: demo is not yet clearly better — fix navigation clutter, "
                    "missing images, and high-severity QA issues."
                ),
            }

        page_summary = []
        for slug, content in list(pages.items())[:6]:
            hero = ""
            if isinstance(content.get("hero"), dict):
                hero = str(content["hero"].get("heading") or "")
            page_summary.append(f"- {slug}: {hero or content.get('meta_title', '')}")

        prompt = (
            "Compare the NEW WordPress demo against the ORIGINAL client website.\n"
            "Decide if the demo is SIGNIFICANTLY BETTER in UX, navigation clarity, "
            "visual presence (images/hero), SEO structure, and trust/conversion.\n\n"
            f"Original site URL: {original_url or '(unknown)'}\n"
            f"Demo site URL: {demo_url}\n"
            f"Pages generated: {len(pages)}\n"
            f"Menu/pages created: {gen_rep.get('pages_created', [])}\n"
            f"Images imported: {len(gen_rep.get('images_imported') or [])}\n"
            "Demo page headlines:\n" + "\n".join(page_summary) + "\n\n"
            f"Open QA issues (sample): "
            f"{[getattr(i, 'description', str(i)) for i in issues[:8]]}\n\n"
            "Respond ONLY with JSON:\n"
            "{"
            '"significantly_better_than_original": true/false, '
            '"comparison_comment": "2-4 sentences explaining the verdict and what still blocks a yes"'
            "}"
        )
        raw = self._ai_router.complete(
            prompt,
            system=(
                "You are a strict UX/SEO auditor. Prefer false unless the demo is "
                "clearly cleaner, more trustworthy, and more conversion-ready than "
                "a typical cluttered local-business original site. "
                "Crowded top navigation, missing images, and weak hierarchy "
                "must keep the verdict false."
            ),
            provider=AIProvider.CLAUDE,
            temperature=0.2,
        )
        data = self._parse_ai_json(raw or "")
        return {
            "significantly_better_than_original": bool(
                data.get("significantly_better_than_original")
            ),
            "comparison_comment": str(data.get("comparison_comment") or ""),
        }

    def _merge_ai_reviews(
        self,
        sonnet: dict[str, Any],
        deepseek: dict[str, Any],
    ) -> dict[str, Any]:
        """Combine Claude + DeepSeek QA when aligned; prefer Sonnet on conflict."""
        if sonnet and not deepseek:
            out = dict(sonnet)
            out["merge_policy"] = "sonnet_only"
            return out
        if deepseek and not sonnet:
            out = dict(deepseek)
            out["merge_policy"] = "deepseek_only"
            return out
        if not sonnet and not deepseek:
            return {"merge_policy": "none"}

        def _norm_list(data: dict, key: str) -> list[str]:
            vals = data.get(key) or []
            out: list[str] = []
            for v in vals:
                s = str(v).strip()
                if s and s not in out:
                    out.append(s)
            return out

        def _merge_lists(a: list[str], b: list[str]) -> tuple[list[str], list[str]]:
            """Return (aligned_union, sonnet_preferred_conflicts_dropped_from_b)."""
            a_l = {x.lower(): x for x in a}
            b_l = {x.lower(): x for x in b}
            aligned = []
            for k, v in a_l.items():
                aligned.append(v)
            for k, v in b_l.items():
                if k in a_l:
                    continue
                # Keep DeepSeek-only items that don't contradict Sonnet phrasing
                aligned.append(v)
            return aligned, []

        strengths, _ = _merge_lists(
            _norm_list(sonnet, "strengths"), _norm_list(deepseek, "strengths")
        )
        # Weaknesses / recommendations: Sonnet wins on overlap; add unique DeepSeek
        weaknesses = _norm_list(sonnet, "weaknesses")
        for w in _norm_list(deepseek, "weaknesses"):
            if w.lower() not in {x.lower() for x in weaknesses}:
                weaknesses.append(w)
        recommendations = _norm_list(sonnet, "recommendations")
        for r in _norm_list(deepseek, "recommendations"):
            if r.lower() not in {x.lower() for x in recommendations}:
                recommendations.append(r)

        issues: list[Any] = []
        seen_desc: set[str] = set()
        for src_name, src in (("sonnet", sonnet), ("deepseek", deepseek)):
            for issue in src.get("issues") or []:
                if not isinstance(issue, dict):
                    continue
                desc = str(issue.get("description") or issue.get("message") or "").strip()
                key = desc.lower()
                if not key or key in seen_desc:
                    continue
                # If DeepSeek issue conflicts with an existing Sonnet issue topic, skip
                if src_name == "deepseek" and any(
                    key[:40] in s or s[:40] in key for s in seen_desc
                ):
                    continue
                seen_desc.add(key)
                item = dict(issue)
                item["source"] = src_name
                issues.append(item)

        notes = []
        if sonnet.get("summary"):
            notes.append("Sonnet: " + str(sonnet.get("summary")))
        if deepseek.get("summary"):
            notes.append("DeepSeek: " + str(deepseek.get("summary")))

        return {
            "strengths": strengths,
            "weaknesses": weaknesses,
            "recommendations": recommendations,
            "issues": issues,
            "sonnet": sonnet,
            "deepseek": deepseek,
            "combined_notes": " | ".join(notes),
            "merge_policy": "union_aligned_prefer_sonnet",
        }

    def _run_ai_review(
        self,
        biz:     dict,
        pages:   dict[str, dict],
        meta:    dict,
        gen_rep: dict,
        issues:  list[QAIssue],
        *,
        provider: AIProvider = AIProvider.DEEPSEEK,
    ) -> dict:
        """Ask *provider* to audit website content (SEO / copy / consistency).

        Returns:
            Parsed AI JSON dict.

        Raises:
            AIError: If the provider is unavailable or the call fails.
        """
        if not self._ai_router.is_available(provider):
            label = {
                AIProvider.DEEPSEEK: "DeepSeek",
                AIProvider.CLAUDE: "Claude",
                AIProvider.OPENAI: "OpenAI",
                AIProvider.GEMINI: "Gemini",
            }.get(provider, provider.value)
            raise AIError(
                f"{label} API key not configured. "
                f"Set the matching *_API_KEY in .env to enable AI QA review."
            )

        prompt = self._build_ai_review_prompt(biz, pages, meta, gen_rep, issues)
        if provider == AIProvider.CLAUDE:
            system = load_prompt_or_default("qa_content", _FALLBACK_CLAUDE_CONTENT_SYSTEM)
            # Bias the prompt toward content/SEO/German — not layout.
            prompt = (
                "Focus on CONTENT QUALITY, SEO, and German writing. "
                "Ignore pure visual/layout critique.\n\n" + prompt
            )
        else:
            system = load_prompt_or_default("qa", _FALLBACK_DEEPSEEK_SYSTEM)

        log.info("{p} QA request: {n:,} chars", p=provider.value, n=len(prompt))

        try:
            response = self._ai_router.complete(
                prompt,
                system=system,
                provider=provider,
            )
        except NotImplementedError:
            raise AIError(
                "AIRouter.complete() is not yet implemented. "
                "Implement ai_router.py to enable AI QA review."
            )
        except Exception as exc:
            raise AIError(f"{provider.value} QA call failed: {exc}") from exc

        if not response or not response.strip():
            raise AIError(f"{provider.value} returned an empty QA response")

        parsed = self._parse_ai_json(response)
        parsed["reviewer"] = provider.value
        return parsed

    def _run_second_opinion(
        self,
        biz:    dict,
        pages:  dict[str, dict],
        issues: list[QAIssue],
    ) -> dict:
        """Ask Claude for a brief second-opinion review."""
        prompt = self._build_second_opinion_prompt(biz, pages, issues)
        log.info("Claude second-opinion request: {n:,} chars", n=len(prompt))
        try:
            response = self._ai_router.complete(
                prompt,
                system=load_prompt_or_default("qa_second_opinion", _FALLBACK_CLAUDE_SYSTEM),
                provider=AIProvider.CLAUDE,
            )
        except NotImplementedError:
            raise AIError("AIRouter.complete() is not yet implemented")
        except Exception as exc:
            raise AIError(f"Claude second opinion failed: {exc}") from exc

        parsed = self._parse_ai_json(response or "")
        parsed["reviewer"] = "claude"
        return parsed

    def _build_ai_review_prompt(
        self,
        biz:     dict,
        pages:   dict[str, dict],
        meta:    dict,
        gen_rep: dict,
        issues:  list[QAIssue],
    ) -> str:
        """Assemble the DeepSeek auditor prompt."""
        # Bound page payloads
        page_summaries: dict[str, Any] = {}
        for slug, content in list(pages.items())[:6]:
            page_summaries[slug] = self._summarise_page(content)

        lines = [
            "Audit this generated local-business website. Do NOT rewrite content.",
            "",
            "=== BUSINESS PROFILE ===",
            json.dumps({
                "company_name":    biz.get("company_name"),
                "industry":        biz.get("industry"),
                "main_services":   biz.get("main_services"),
                "service_areas":   biz.get("service_areas"),
                "contact_phone":   biz.get("contact_phone"),
                "contact_email":   biz.get("contact_email"),
                "trust_signals":   biz.get("trust_signals"),
                "brand_tone":      biz.get("brand_tone"),
            }, indent=2, ensure_ascii=False),
            "",
            "=== GENERATION SUMMARY ===",
            json.dumps({
                "pages_created":   gen_rep.get("pages_created"),
                "menu_created":    gen_rep.get("menu_created"),
                "images_imported": len(gen_rep.get("images_imported") or []),
                "seo_applied":     len(gen_rep.get("seo_applied") or []),
                "errors":          gen_rep.get("errors"),
                "warnings":        gen_rep.get("warnings"),
            }, indent=2, ensure_ascii=False),
            "",
            "=== PAGE CONTENT SUMMARIES ===",
            json.dumps(page_summaries, indent=2, ensure_ascii=False)[:8000],
            "",
            "=== META DATA ===",
            json.dumps(meta, indent=2, ensure_ascii=False)[:2000],
            "",
            "=== DETERMINISTIC ISSUES ALREADY FOUND ===",
            json.dumps([i.model_dump() for i in issues[:20]], indent=2, ensure_ascii=False),
            "",
            "=== REQUIRED JSON RESPONSE ===",
            json.dumps({
                "strengths": ["what the website does well"],
                "weaknesses": ["key weaknesses"],
                "inconsistencies": ["branding or factual inconsistencies"],
                "missing_information": ["important missing facts/sections"],
                "issues": [{
                    "category": "content|seo|business|accessibility|conversion|structure",
                    "severity": "critical|high|medium|low",
                    "affected_page": "page slug or all",
                    "description": "clear description",
                    "suggested_improvement": "actionable suggestion — do not rewrite copy",
                }],
                "recommendations": ["prioritised improvement opportunities"],
                "overall_assessment": "2-4 sentence auditor summary",
            }, indent=2),
        ]
        return "\n".join(lines)

    def _build_second_opinion_prompt(
        self,
        biz:    dict,
        pages:  dict[str, dict],
        issues: list[QAIssue],
    ) -> str:
        return "\n".join([
            "Provide a brief second-opinion audit. Do NOT rewrite content.",
            f"Company: {biz.get('company_name', '?')} | Industry: {biz.get('industry', '?')}",
            f"Pages present: {', '.join(pages.keys()) or 'none'}",
            f"Known issues ({len(issues)}):",
            json.dumps([i.model_dump() for i in issues[:12]], indent=2, ensure_ascii=False),
            "",
            "Respond with JSON:",
            json.dumps({
                "agree_with_findings": True,
                "additional_issues": [],
                "priority_fixes": ["top fixes"],
                "summary": "short second opinion",
            }, indent=2),
        ])

    @staticmethod
    def _summarise_page(content: dict) -> dict:
        """Shrink a page content dict for the AI prompt."""
        out: dict[str, Any] = {}
        for key in (
            "meta_title", "meta_description", "hero", "hero_heading",
            "intro", "company_story", "cta_section", "footer_tagline",
        ):
            if key in content:
                out[key] = content[key]
        if "services_overview" in content:
            out["services_overview"] = content["services_overview"]
        if "services" in content and isinstance(content["services"], list):
            out["services"] = content["services"][:5]
        if "faqs" in content and isinstance(content["faqs"], list):
            out["faqs"] = content["faqs"][:6]
        if "why_choose_us" in content:
            out["why_choose_us"] = content["why_choose_us"]
        if "body_html" in content:
            out["body_html_excerpt"] = str(content["body_html"])[:500]
        return out

    # ── Private: live helpers ──────────────────────────────────────────────────

    def _run_live_checks(self, wp_url: str, pages: list[str]) -> list[QACheck]:
        """Run all live HTTP checks and return combined results."""
        log.info("Running live checks against {u}", u=wp_url)
        results: list[QACheck] = []
        results.extend(self.check_page_availability(wp_url, pages))
        results.append(self.check_broken_links(wp_url))
        results.extend(self.check_seo_completeness(wp_url, pages[:5]))
        results.append(self.check_image_alt_text(wp_url))
        results.append(self.check_performance(wp_url))
        return results

    def _http_get(self, url: str) -> tuple[int, str, float]:
        """GET *url* and return (status, body, elapsed_seconds)."""
        import urllib.error
        import urllib.request

        start = time.perf_counter()
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "WebMaker-QAReviewer/1.0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._http_timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                elapsed = time.perf_counter() - start
                return int(resp.status), body, elapsed
        except urllib.error.HTTPError as exc:
            elapsed = time.perf_counter() - start
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            return int(exc.code), body, elapsed

    # ── Private: report I/O ────────────────────────────────────────────────────

    def _save_reports(self, project_dir: Path, report: DetailedQAReport) -> None:
        """Write qa_report.json, seo_review.json, content_review.json, website_score.json."""
        json_dir = project_dir / "json"
        json_dir.mkdir(parents=True, exist_ok=True)

        # Full QA report
        self._write_json(json_dir / "qa_report.json", report.model_dump())

        # SEO-focused extract
        seo_issues = [i.model_dump() for i in report.issues if i.category == "seo"]
        self._write_json(json_dir / "seo_review.json", {
            "reviewed_at": report.reviewed_at,
            "score":       report.scores.seo_quality,
            "explanation": report.scores.explanations.get("seo_quality", ""),
            "issues":      seo_issues,
            "checks": [
                c for c in report.checks
                if str(c.get("name", "")).startswith("seo")
            ],
        })

        # Content-focused extract (merge with any prior content_optimizer review)
        content_issues = [
            i.model_dump() for i in report.issues if i.category == "content"
        ]
        self._write_json(json_dir / "qa_content_review.json", {
            "reviewed_at": report.reviewed_at,
            "score":       report.scores.content_quality,
            "explanation": report.scores.explanations.get("content_quality", ""),
            "issues":      content_issues,
            "ai_review":   {
                "strengths":  report.ai_review.get("strengths", []),
                "weaknesses": report.ai_review.get("weaknesses", []),
                "assessment": report.ai_review.get("overall_assessment", ""),
            },
        })
        # Spec: content_review.json — QA content findings
        self._write_json(json_dir / "content_review.json", {
            "reviewed_at": report.reviewed_at,
            "source":      "qa_reviewer",
            "score":       report.scores.content_quality,
            "explanation": report.scores.explanations.get("content_quality", ""),
            "issues":      content_issues,
            "strengths":   report.strengths,
            "weaknesses":  report.weaknesses,
            "recommendations": report.recommendations,
        })

        # Scores
        self._write_json(
            json_dir / "website_score.json",
            report.scores.model_dump(),
        )

        log.info(
            "Saved: qa_report.json, seo_review.json, content_review.json, "
            "website_score.json → {d}",
            d=json_dir,
        )

    # ── Private: helpers ───────────────────────────────────────────────────────

    def _load_optimized_pages(self, project_dir: Path) -> dict[str, dict]:
        json_dir = project_dir / "json"
        pages: dict[str, dict] = {}
        if not json_dir.exists():
            return pages
        for path in sorted(json_dir.glob("optimized_*.json")):
            slug = path.stem.removeprefix("optimized_")
            data = self._load_json(path, default=None)
            if isinstance(data, dict):
                pages[slug] = data
        return pages

    def _issues_to_checks(self, issues: list[QAIssue]) -> list[QACheck]:
        """Convert issues into coarse per-category QACheck summaries."""
        by_cat: dict[str, list[QAIssue]] = {}
        for issue in issues:
            by_cat.setdefault(issue.category, []).append(issue)

        checks: list[QACheck] = []
        for cat, cat_issues in by_cat.items():
            critical = sum(1 for i in cat_issues if i.severity == "critical")
            high = sum(1 for i in cat_issues if i.severity == "high")
            penalty = critical * 0.35 + high * 0.2 + (len(cat_issues) - critical - high) * 0.05
            score = max(0.0, 1.0 - penalty)
            checks.append(QACheck(
                name   = f"deterministic:{cat}",
                passed = critical == 0 and high <= 1,
                score  = round(score, 2),
                detail = f"{len(cat_issues)} issue(s) ({critical} critical, {high} high)",
            ))
        return checks

    def _infer_strengths(
        self,
        pages:   dict[str, dict],
        gen_rep: dict,
        issues:  list[QAIssue],
    ) -> list[str]:
        strengths: list[str] = []
        if len(pages) >= 4:
            strengths.append(f"Content generated for {len(pages)} pages")
        if gen_rep.get("menu_created"):
            strengths.append("Primary navigation menu created")
        if gen_rep.get("images_imported"):
            strengths.append(
                f"{len(gen_rep['images_imported'])} images imported to Media Library"
            )
        if gen_rep.get("seo_applied"):
            strengths.append("SEO metadata applied to generated pages")
        if not any(i.severity == "critical" for i in issues):
            strengths.append("No critical issues found in deterministic checks")
        return strengths

    @staticmethod
    def _dict_to_issue(data: dict) -> QAIssue:
        return QAIssue(
            category=str(data.get("category", "content")),
            severity=str(data.get("severity", "medium")),
            affected_page=str(data.get("affected_page", "")),
            description=str(data.get("description", "")),
            suggested_improvement=str(data.get("suggested_improvement", "")),
        )

    def _infer_project_dir(self, generation: GenerationResult) -> Path | None:
        """Best-effort resolve of the client project directory."""
        # generation.errors may contain paths; prefer projects_dir scan is out of scope
        # Callers should use review_from_directory when possible.
        return None

    def _parse_ai_json(self, response: str) -> dict:
        text = response.strip()
        if not text:
            return {}
        fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.DOTALL)
        if fence:
            text = fence.group(1)
        else:
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end > start:
                text = text[start : end + 1]
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            log.warning("AI JSON parse error: {e}", e=exc)
            return {}
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _slugify(text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"[^\w\s-]", "", text)
        text = re.sub(r"[\s_]+", "-", text)
        return re.sub(r"-+", "-", text).strip("-")[:60]

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        write_versioned_json(path, data)

    @staticmethod
    def _load_json(path: Path, *, default: Any) -> Any:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.debug("Not found: {p}", p=Path(path).name)
            return default
        except json.JSONDecodeError as exc:
            log.warning("Invalid JSON in {p}: {e}", p=Path(path).name, e=exc)
            return default
        except OSError as exc:
            log.warning("Cannot read {p}: {e}", p=Path(path).name, e=exc)
            return default
        data = unwrap_json(raw)
        if default is not None and type(default) is list and not isinstance(data, list):
            return default
        if default is not None and type(default) is dict and not isinstance(data, dict):
            return default
        return data
