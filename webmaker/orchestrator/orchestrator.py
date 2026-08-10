"""
webmaker.orchestrator.orchestrator
==================================
The single execution layer that runs agents in order.

Responsibilities (per V2 spec):
- Run agents sequentially.
- Validate every artifact (agents self-validate; the store re-validates on load).
- Stop if a required upstream artifact is missing or invalid.
- Persist every artifact to ``projects/<slug>/artifacts/``.
- Allow rerunning a single agent using stored upstream artifacts, without
  rerunning previous agents.

This replaces the legacy ``ProjectManager.run_pipeline`` phase loop as the
primary path. ``ProjectManager`` is retained for project/state/dir CRUD.

V2 agent order (definitive)
---------------------------
    0. website_acquisition — Agent 0: crawl + package + validation (no AI)
    1. migration_agent     — Agent 1: faithful as-is migration (optional, UI-driven)
    2. target_crawler      — business profile (reuses crawl)
    3. competitor_crawler  — competitor crawl + comparison
    4. website_reviewer    — AI review → OP-Content recommendations
    5. design_recommendation — GPT theme/pattern selector
    6. live_demo_renderer  — render approved OP-Content into WordPress
    7. qa_reviewer         — content + visual QA

``migration_agent`` is optional: ``run_all`` skips it unless the project state
has a ``theme_id`` configured (set by the Migrate UI tab).
``website_acquisition`` is skipped in ``run_all`` when a passing validation
report already exists (unless ``force_crawl``).
"""

from __future__ import annotations

from typing import Callable

from webmaker.agents.base import AgentContext, BaseAgent
from webmaker.agents.competitor_crawler import (
    CompetitorCrawlerAgent,
    CrawlCompetitorsInput,
)
from webmaker.agents.design_recommendation import (
    DesignInput,
    DesignRecommendationAgent,
)
from webmaker.agents.live_demo_renderer import LiveDemoRendererAgent, RenderAgentInput
from webmaker.agents.migration_agent import MigrationAgent, MigrateInput
from webmaker.agents.website_modernizer import (
    ModernizeInput,
    WebsiteModernizerAgent,
)
from webmaker.agents.qa_reviewer import QAAgentInput, QAReviewerAgent
from webmaker.agents.target_crawler import CrawlTargetInput, TargetCrawlerAgent
from webmaker.agents.website_acquisition import (
    AcquireInput,
    WebsiteAcquisitionAgent,
)
from webmaker.agents.website_reviewer import ReviewInput, WebsiteReviewerAgent
from webmaker.core.exceptions import WebMakerError
from webmaker.core.logging import get_logger
from webmaker.schemas import (
    CompetitorProjects,
    DesignRecommendation,
    MigrationResult,
    ModernizeResult,
    OpContent,
    RenderResult,
    TargetProject,
    WebsitePackageResult,
)
from webmaker.orchestrator.store import ArtifactStore

log = get_logger("orchestrator")

# Canonical execution order.
AGENT_ORDER: tuple[str, ...] = (
    "website_acquisition",
    "website_modernizer",   # Agent 1 — intelligent modernizer (AI, primary Migrate)
    "migration_agent",      # faithful as-is clone (alternative Migrate mode)
    "target_crawler",
    "competitor_crawler",
    "website_reviewer",
    "design_recommendation",
    "live_demo_renderer",
    "qa_reviewer",
)


class OrchestratorError(WebMakerError):
    """Raised when orchestration cannot proceed (e.g. missing upstream artifact)."""


class Orchestrator:
    """Runs single-responsibility agents and persists their artifacts."""

    def __init__(
        self,
        settings: object,
        project_slug: str,
        *,
        manager=None,
    ) -> None:
        self._settings = settings
        self._slug = project_slug

        if manager is None:
            from webmaker.modules.project_manager import ProjectManager
            manager = ProjectManager(settings)
            manager.load_project(project_slug)
        self._pm = manager

        project_dir = self._pm.get_project_dir()
        self._store = ArtifactStore(project_dir / "artifacts")

    # ── Accessors ────────────────────────────────────────────────────────────

    @property
    def store(self) -> ArtifactStore:
        """The artifact store for this project."""
        return self._store

    @property
    def manager(self):
        """The underlying ProjectManager (state/dir CRUD only)."""
        return self._pm

    def _context(self, extras: dict[str, object] | None = None) -> AgentContext:
        return AgentContext(
            project_slug=self._slug,
            data_dir=self._pm.get_data_dir(),
            settings=self._settings,
            extras=dict(extras or {}),
        )

    # ── Acquisition (Agent 0) ────────────────────────────────────────────────

    def run_acquisition(
        self,
        *,
        force_crawl: bool = False,
        threshold: float = 0.95,
    ) -> WebsitePackageResult:
        """Run Agent 0 (WebsiteAcquisitionAgent) for the selected project.

        Crawls (or reuses crawl data), builds ``website_package/``, and writes
        ``validation_report.json`` + ``artifacts/acquisition.json``.
        """
        state = self._pm.active_state
        url = state.target_url if state else ""
        extras = {
            "force_crawl": force_crawl,
            "threshold": threshold,
        }
        agent = WebsiteAcquisitionAgent(self._context(extras))
        agent_input = AcquireInput(
            target_url=url,
            force_crawl=force_crawl,
            threshold=threshold,
        )
        artifact = agent.run(agent_input)
        self._store.save(artifact)
        try:
            self._pm.sync_crawl_data_dir()
        except WebMakerError as exc:
            log.warning("Could not sync crawl data dir after acquisition: {e}", e=exc)
        return artifact

    # ── Modernize (Agent 1 — intelligent, primary Migrate) ───────────────────

    def run_modernize(
        self,
        *,
        theme_id: str,
        template_id: str,
        force_crawl: bool = False,
        open_browser: bool = True,
    ) -> ModernizeResult:
        """Run WebsiteModernizerAgent (Agent 1) for the selected project.

        Uses Claude to intelligently map the website package into a beautiful,
        professional WordPress site using the chosen theme + template design
        language.  Falls back to the deterministic layout pipeline when Claude
        is unavailable.

        Args:
            theme_id:     WordPress theme slug to install (e.g. ``"kadence"``).
            template_id:  Starter template slug to import (may be empty).
            force_crawl:  Re-crawl even if crawl data already exists.
            open_browser: Open the demo URL in the system browser on success.

        Returns:
            The :class:`~webmaker.schemas.modernizer.ModernizeResult` artifact.
        """
        state = self._pm.active_state
        url   = state.target_url if state else ""

        # Warn when acquisition validation is missing or failed.
        acq = self._store.load(WebsitePackageResult)
        if acq is None:
            log.warning(
                "No acquisition artifact — modernizer will use raw crawl data. "
                "Run the Crawl tab first for best results."
            )
        elif not acq.passed:
            log.warning(
                "Acquisition completeness {s:.1%} below threshold {t:.0%} — "
                "gaps: {g}",
                s=acq.overall_score, t=acq.threshold,
                g="; ".join(acq.gaps[:5]) or "(see validation_report.json)",
            )

        if state is not None:
            state.metadata["theme_id"]    = theme_id
            state.metadata["template_id"] = template_id
            self._pm.save_project()

        extras = {
            "theme_id":     theme_id,
            "template_id":  template_id,
            "force_crawl":  force_crawl,
            "open_browser": open_browser,
        }
        agent = WebsiteModernizerAgent(self._context(extras))
        agent_input = ModernizeInput(
            target_url=url,
            theme_id=theme_id,
            template_id=template_id,
        )
        artifact = agent.run(agent_input)
        self._store.save(artifact)

        try:
            self._pm.sync_crawl_data_dir()
        except WebMakerError as exc:
            log.warning("Could not sync crawl data dir after modernize: {e}", e=exc)

        return artifact

    # ── Migration (Agent 1 — faithful, legacy) ───────────────────────────────

    def run_migration(
        self,
        *,
        theme_id: str,
        template_id: str,
        force_crawl: bool = False,
        open_browser: bool = True,
    ) -> MigrationResult:
        """Run MigrationAgent for the selected project.

        Crawls the target site (or reuses existing crawl data), writes verbatim
        ``optimized_*.json`` files, installs the chosen theme + template, and
        generates the WordPress demo from the raw source content.

        Args:
            theme_id:     WordPress theme slug to install (e.g. ``"kadence"``).
            template_id:  Starter template slug to import (may be empty).
            force_crawl:  Re-crawl even if ``pages.json`` already exists.
            open_browser: Open the demo URL in the system browser on success.

        Returns:
            The :class:`~webmaker.schemas.migration.MigrationResult` artifact.
        """
        state = self._pm.active_state
        url   = state.target_url if state else ""

        # Warn when acquisition validation is missing or failed.
        acq = self._store.load(WebsitePackageResult)
        if acq is None:
            log.warning(
                "No acquisition artifact — migrate will use crawl data if present. "
                "Run the Crawl tab first for a completeness report."
            )
        elif not acq.passed:
            log.warning(
                "Acquisition completeness {s:.1%} below threshold {t:.0%} — "
                "gaps: {g}",
                s=acq.overall_score, t=acq.threshold,
                g="; ".join(acq.gaps[:5]) or "(see validation_report.json)",
            )

        # Persist theme choice in project state for future reference.
        if state is not None:
            state.metadata["theme_id"]    = theme_id
            state.metadata["template_id"] = template_id
            self._pm.save_project()

        extras = {
            "theme_id":     theme_id,
            "template_id":  template_id,
            "force_crawl":  force_crawl,
            "open_browser": open_browser,
        }
        agent       = MigrationAgent(self._context(extras))
        agent_input = MigrateInput(
            target_url=url,
            theme_id=theme_id,
            template_id=template_id,
        )
        artifact = agent.run(agent_input)
        self._store.save(artifact)

        # Keep the crawl data dir in sync right after migration crawl.
        try:
            self._pm.sync_crawl_data_dir()
        except WebMakerError as exc:
            log.warning("Could not sync crawl data dir after migration: {e}", e=exc)

        return artifact

    # ── Single-agent execution ───────────────────────────────────────────────

    def run_agent(self, name: str, *, extras: dict[str, object] | None = None):
        """Run one agent by name, persist and return its artifact.

        Upstream artifacts are loaded from the store, so a single agent can be
        rerun without rerunning its predecessors.

        Raises:
            OrchestratorError: If the agent name is unknown or a required
                               upstream artifact is missing.
        """
        if name not in AGENT_ORDER:
            raise OrchestratorError(f"Unknown agent: {name!r}")

        builder = self._builders()[name]
        agent, agent_input = builder(extras or {})
        artifact = agent.run(agent_input)
        self._store.save(artifact)

        # Keep the crawl data dir in sync right after the target crawl.
        if name in ("target_crawler", "migration_agent", "website_acquisition", "website_modernizer"):
            try:
                self._pm.sync_crawl_data_dir()
            except WebMakerError as exc:
                log.warning("Could not sync crawl data dir: {e}", e=exc)

        return artifact

    def ensure_crawl_artifacts(self, *, extras: dict[str, object] | None = None) -> None:
        """Materialize ``target`` / ``competitors`` from crawl data if missing.

        Crawl & Analyze still writes legacy ``json/`` files; OP-Content and later
        agents need V2 artifacts. This hydrates them without re-burning tokens
        when ``pages.json`` / competitor ``.md`` already exist.
        """
        if self._store.load(TargetProject) is None:
            log.info("No target artifact — running target_crawler to hydrate")
            self.run_agent("target_crawler", extras=extras)
        if self._store.load(CompetitorProjects) is None:
            log.info("No competitors artifact — running competitor_crawler to hydrate")
            self.run_agent("competitor_crawler", extras=extras)

    def run_all(
        self,
        *,
        stop_on_error: bool = True,
        on_step: Callable[[str, object], None] | None = None,
        extras: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Run every agent in order; stop on the first failure by default.

        ``website_acquisition`` is skipped when a passing acquisition artifact
        already exists (unless ``force_crawl``). ``migration_agent`` is skipped
        unless the project state has ``theme_id`` configured.
        """
        state = self._pm.active_state
        results: dict[str, object] = {}
        force_crawl = bool((extras or {}).get("force_crawl"))
        for name in AGENT_ORDER:
            if name == "website_acquisition" and not force_crawl:
                existing = self._store.load(WebsitePackageResult)
                if existing is not None and existing.passed:
                    log.info("Skipping website_acquisition (passing package exists)")
                    results[name] = existing
                    continue

            if name == "website_modernizer":
                theme_configured = bool(
                    (state and state.metadata.get("theme_id"))
                    or (extras or {}).get("theme_id")
                )
                if not theme_configured:
                    log.info("Skipping website_modernizer (no theme configured)")
                    continue

            if name == "migration_agent":
                theme_configured = bool(
                    (state and state.metadata.get("theme_id"))
                    or (extras or {}).get("theme_id")
                )
                if not theme_configured:
                    log.info("Skipping migration_agent (no theme configured)")
                    continue

            try:
                artifact = self.run_agent(name, extras=extras)
                results[name] = artifact
                if on_step is not None:
                    on_step(name, artifact)
            except WebMakerError as exc:
                log.error("Agent {n} failed: {e}", n=name, e=exc)
                if stop_on_error:
                    raise
        return results

    # ── Input builders (map prior artifacts -> agent input) ──────────────────

    def _builders(self) -> dict[str, Callable[[dict], tuple[BaseAgent, object]]]:
        return {
            "website_acquisition": self._build_acquisition,
            "website_modernizer":  self._build_modernize,
            "migration_agent":    self._build_migration,
            "target_crawler":     self._build_target_crawler,
            "competitor_crawler": self._build_competitor_crawler,
            "website_reviewer":   self._build_website_reviewer,
            "design_recommendation": self._build_design,
            "live_demo_renderer": self._build_renderer,
            "qa_reviewer":        self._build_qa,
        }

    def _require(self, model, label: str):
        artifact = self._store.load(model)
        if artifact is None:
            raise OrchestratorError(
                f"Missing upstream artifact {label!r}. Run its agent first."
            )
        return artifact

    def _build_acquisition(self, extras: dict):
        state = self._pm.active_state
        url = state.target_url if state else ""
        force = bool(extras.get("force_crawl"))
        threshold = float(extras.get("threshold") or 0.95)
        return (
            WebsiteAcquisitionAgent(self._context(extras)),
            AcquireInput(target_url=url, force_crawl=force, threshold=threshold),
        )

    def _build_modernize(self, extras: dict):
        state       = self._pm.active_state
        url         = state.target_url if state else ""
        meta        = state.metadata if state else {}
        theme_id    = str(extras.get("theme_id")   or meta.get("theme_id")   or "")
        template_id = str(extras.get("template_id") or meta.get("template_id") or "")
        return (
            WebsiteModernizerAgent(self._context(extras)),
            ModernizeInput(
                target_url=url,
                theme_id=theme_id,
                template_id=template_id,
            ),
        )

    def _build_migration(self, extras: dict):
        state       = self._pm.active_state
        url         = state.target_url if state else ""
        meta        = state.metadata if state else {}
        theme_id    = str(extras.get("theme_id")   or meta.get("theme_id")   or "")
        template_id = str(extras.get("template_id") or meta.get("template_id") or "")
        return (
            MigrationAgent(self._context(extras)),
            MigrateInput(
                target_url=url,
                theme_id=theme_id,
                template_id=template_id,
            ),
        )

    def _build_target_crawler(self, extras: dict):
        state = self._pm.active_state
        url = state.target_url if state else ""
        return (
            TargetCrawlerAgent(self._context(extras)),
            CrawlTargetInput(target_url=url),
        )

    def _build_competitor_crawler(self, extras: dict):
        state = self._pm.active_state
        urls = list(state.competitor_urls) if state else []
        return (
            CompetitorCrawlerAgent(self._context(extras)),
            CrawlCompetitorsInput(competitor_urls=urls),
        )

    def _build_website_reviewer(self, extras: dict):
        target = self._require(TargetProject, "target")
        competitors = self._store.load(CompetitorProjects) or CompetitorProjects()
        return (
            WebsiteReviewerAgent(self._context(extras)),
            ReviewInput(target=target, competitors=competitors),
        )

    def _build_design(self, extras: dict):
        target = self._require(TargetProject, "target")
        op_content = self._store.load(OpContent) or OpContent()
        return (
            DesignRecommendationAgent(self._context(extras)),
            DesignInput(business=target.business, op_content=op_content),
        )

    def _build_renderer(self, extras: dict):
        op_content = self._require(OpContent, "op_content")
        design = self._store.load(DesignRecommendation) or DesignRecommendation()
        return (
            LiveDemoRendererAgent(self._context(extras)),
            RenderAgentInput(op_content=op_content, design=design),
        )

    def _build_qa(self, extras: dict):
        render = self._store.load(RenderResult) or RenderResult()
        return (
            QAReviewerAgent(self._context(extras)),
            QAAgentInput(render=render),
        )
