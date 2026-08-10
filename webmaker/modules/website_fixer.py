"""
webmaker.modules.website_fixer
==============================
Uses Claude (Sonnet) to repair optimised page content based on QA findings,
then rebuilds WordPress pages from the fixed JSON.

This is an optional late-pipeline phase after QA review.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from webmaker.core.exceptions import AIError, GenerationError
from webmaker.core.logging import get_logger
from webmaker.core.prompts import load_prompt_or_default
from webmaker.core.schema import unwrap_json, write_versioned_json
from webmaker.core.types import AIProvider
from webmaker.modules.ai_router import AIRouter

if TYPE_CHECKING:
    from webmaker.config.settings import Settings

log = get_logger("website_fixer")

_FALLBACK_FIX_SYSTEM = (
    "You are a senior website editor for German local business sites. "
    "Fix the provided page JSON using the QA findings. "
    "Preserve all factual business data. Never invent services, prices, "
    "or claims. Use [MISSING INFORMATION] when facts are unknown. "
    "Respond ONLY with a single valid JSON object for the page."
)


class WebsiteFixer:
    """Apply Sonnet-driven content fixes after QA."""

    def __init__(
        self,
        settings: "Settings",
        ai_router: AIRouter | None = None,
    ) -> None:
        self._settings = settings
        self._ai_router = ai_router or AIRouter(settings)

    def fix_from_directory(
        self,
        project_dir: Path,
        *,
        page_slugs: tuple[str, ...] | list[str] | None = None,
        reset: bool = False,
        update_only: bool = True,
    ) -> dict[str, Any]:
        """Fix optimised pages using QA report, then rebuild WordPress.

        Args:
            project_dir: Client project directory.
            page_slugs:  If set, only fix these slugs (others kept as-is).
            reset:       Wipe WP pages/menus before applying (full regenerate).
            update_only: Patch existing pages only — leave menu/media intact.

        Returns:
            Summary dict with ``pages_fixed``, ``errors``, ``rebuilt``.
        """
        project_dir = Path(project_dir)
        json_dir = project_dir / "json"
        summary: dict[str, Any] = {
            "pages_fixed": [],
            "errors": [],
            "rebuilt": False,
            "update_only": update_only,
            "reset": reset,
            "fixed_at": datetime.now(timezone.utc).isoformat(),
        }

        qa = self._load_json(json_dir / "qa_report.json", default={})
        pages = self._load_optimized_pages(project_dir)
        if not pages:
            raise GenerationError(
                "No optimized_*.json pages found to fix",
                path=str(json_dir),
            )

        allowed = {s.strip().lower() for s in (page_slugs or []) if str(s).strip()}
        if allowed:
            pages = {k: v for k, v in pages.items() if k.lower() in allowed}
            if not pages:
                raise GenerationError(
                    f"No matching optimized pages for slugs: {sorted(allowed)}",
                    path=str(json_dir),
                )

        findings = self._summarise_qa(qa)
        for slug, content in pages.items():
            try:
                fixed = self._fix_page(slug, content, findings)
                out = json_dir / f"optimized_{slug}.json"
                write_versioned_json(out, fixed)
                summary["pages_fixed"].append(slug)
                log.info("Fixed page content: {s}", s=slug)
            except Exception as exc:
                summary["errors"].append(f"{slug}: {exc}")
                log.warning("Fix failed for {s}: {e}", s=slug, e=exc)

        # Persist fix report
        write_versioned_json(json_dir / "fix_report.json", summary)

        # Apply to WordPress — update in place unless full regenerate
        try:
            from webmaker.modules.wordpress_generator import WordPressGenerator
            gen = WordPressGenerator(self._settings)
            result = gen.generate_from_directory(
                project_dir,
                reset=reset and not update_only,
                update_only=update_only,
            )
            summary["rebuilt"] = bool(getattr(result, "success", True))
            if getattr(result, "errors", None):
                summary["errors"].extend(str(e) for e in result.errors)
        except Exception as exc:
            summary["errors"].append(f"WordPress rebuild: {exc}")
            log.error("WordPress rebuild after fix failed: {e}", e=exc)

        return summary

    def _fix_page(
        self,
        slug: str,
        content: dict[str, Any],
        findings: str,
    ) -> dict[str, Any]:
        if not self._ai_router.is_available(AIProvider.CLAUDE):
            raise AIError("Claude API key required for website fix step")

        system = load_prompt_or_default("fix", _FALLBACK_FIX_SYSTEM)
        prompt = (
            f"Fix the {slug} page JSON based on QA findings.\n"
            "Goal: make the demo SIGNIFICANTLY BETTER than the cluttered original "
            "client site — clean structure, clear hierarchy, concrete image "
            "placeholders, strong SEO, no inventing facts.\n\n"
            f"=== QA FINDINGS ===\n{findings[:6000]}\n\n"
            f"=== CURRENT PAGE JSON ===\n"
            f"{json.dumps(content, ensure_ascii=False, indent=2)[:12000]}\n\n"
            "Return the full corrected page JSON object only."
        )
        raw = self._ai_router.complete(
            prompt,
            system=system,
            provider=AIProvider.CLAUDE,
            temperature=0.3,
        )
        fixed = self._parse_json(raw)
        if not fixed:
            raise AIError(f"Empty/unparseable fix response for {slug}")
        fixed.setdefault("slug", slug)
        fixed["fixed_at"] = datetime.now(timezone.utc).isoformat()
        fixed["fixed_by"] = "claude"
        return fixed

    @staticmethod
    def _summarise_qa(qa: dict[str, Any]) -> str:
        parts: list[str] = []
        if qa.get("comparison_comment"):
            parts.append(
                "Vs original site: " + str(qa["comparison_comment"])
            )
        if "significantly_better_than_original" in qa:
            parts.append(
                "Significantly better than original: "
                + str(bool(qa.get("significantly_better_than_original")))
            )
        if qa.get("weaknesses"):
            parts.append("Weaknesses: " + "; ".join(str(x) for x in qa["weaknesses"][:12]))
        if qa.get("recommendations"):
            parts.append(
                "Recommendations: " + "; ".join(str(x) for x in qa["recommendations"][:15])
            )
        issues = qa.get("issues") or []
        for issue in issues[:20]:
            if isinstance(issue, dict):
                parts.append(
                    f"- [{issue.get('severity', '?')}] "
                    f"{issue.get('description') or issue.get('message') or issue}"
                )
            else:
                parts.append(f"- {issue}")
        merged = qa.get("ai_review") or {}
        if isinstance(merged, dict) and merged.get("combined_notes"):
            parts.append("Combined AI notes: " + str(merged["combined_notes"])[:2000])
        return "\n".join(parts) if parts else "No explicit QA findings; polish clarity and structure."

    def _load_optimized_pages(self, project_dir: Path) -> dict[str, dict]:
        pages: dict[str, dict] = {}
        for path in sorted((Path(project_dir) / "json").glob("optimized_*.json")):
            data = self._load_json(path, default=None)
            if isinstance(data, dict):
                slug = path.stem.replace("optimized_", "", 1)
                pages[slug] = data
        return pages

    @staticmethod
    def _parse_json(text: str) -> dict:
        raw = (text or "").strip()
        if not raw:
            return {}
        import re
        fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
        if fence:
            raw = fence.group(1)
        else:
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end > start:
                raw = raw[start : end + 1]
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _load_json(path: Path, *, default: Any) -> Any:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return default
        data = unwrap_json(raw)
        if isinstance(default, dict) and not isinstance(data, dict):
            return default
        return data
