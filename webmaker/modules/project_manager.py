"""
webmaker.modules.project_manager
==================================
Central controller for WebMaker.  Creates and loads projects, persists
state, tracks per-phase progress, and orchestrates the existing modules
in pipeline order — without implementing crawl / AI / WordPress / QA logic.

Pipeline order
--------------
1. WebsiteCrawler
2. BusinessAnalyzer
3. CompetitorAnalyzer
4. ContentOptimizer
5. WordPressGenerator
6. QAReviewer

On failure the current phase is marked failed, state is saved, and the
pipeline stops safely.  Resume continues from the first non-completed phase.

Primary class: ProjectManager
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from webmaker.core.exceptions import (
    ProjectAlreadyExistsError,
    ProjectError,
    ProjectNotFoundError,
)
from webmaker.core.logging import get_logger
from webmaker.core.progress import ProgressManager, progress_manager as default_progress
from webmaker.core.schema import write_versioned_json
from webmaker.core.types import ProjectConfig, ProjectStatus
from webmaker.modules.job_manager import (
    Job,
    JobManager,
    JobResult,
    JobStatus,
    JobType,
)
from webmaker.plugins import load_plugins
from webmaker.plugins.registry import PluginRegistry, plugin_registry as default_plugins

if TYPE_CHECKING:
    from webmaker.config.settings import Settings

log = get_logger("project_manager")


# ── Phase model ────────────────────────────────────────────────────────────────

class PhaseName(str, Enum):
    """Ordered pipeline phases."""

    CRAWL      = "crawl"
    ANALYZE    = "analyze"
    COMPETE    = "compete"
    OPTIMIZE   = "optimize"
    GENERATE   = "generate"
    REVIEW     = "review"
    FIX        = "fix"


class PhaseStatus(str, Enum):
    """Per-phase execution status."""

    NOT_STARTED = "not_started"
    RUNNING     = "running"
    COMPLETED   = "completed"
    FAILED      = "failed"


# Pipeline order (immutable)
_PIPELINE: tuple[PhaseName, ...] = (
    PhaseName.CRAWL,
    PhaseName.ANALYZE,
    PhaseName.COMPETE,
    PhaseName.OPTIMIZE,
    PhaseName.GENERATE,
    PhaseName.REVIEW,
    PhaseName.FIX,
)

# Map phase → ProjectStatus while that phase is active
_PHASE_TO_STATUS: dict[PhaseName, ProjectStatus] = {
    PhaseName.CRAWL:    ProjectStatus.CRAWLING,
    PhaseName.ANALYZE:  ProjectStatus.ANALYZING,
    PhaseName.COMPETE:  ProjectStatus.COMPETING,
    PhaseName.OPTIMIZE: ProjectStatus.OPTIMIZING,
    PhaseName.GENERATE: ProjectStatus.GENERATING,
    PhaseName.REVIEW:   ProjectStatus.REVIEWING,
    PhaseName.FIX:      ProjectStatus.FIXING,
}

# Subdirectories created for every new project
_PROJECT_SUBDIRS: tuple[str, ...] = (
    "pages",
    "images",
    "screenshots",
    "assets",
    "raw",
    "json",
    "json/pages",
    "logs",
    "config",
    "qa",
)


class PhaseRecord(BaseModel):
    """Persistent status record for one pipeline phase."""

    name:          str   = ""
    status:        str   = PhaseStatus.NOT_STARTED.value
    started_at:    str   = ""
    finished_at:   str   = ""
    duration_s:    float = 0.0
    error:         str   = ""
    warnings:      list[str] = Field(default_factory=list)
    artifacts:     list[str] = Field(default_factory=list)


class ProjectState(BaseModel):
    """Full ``project.json`` payload persisted on disk.

    Extends the lightweight shared :class:`ProjectConfig` with phase
    tracking, competitor URLs, and generated-file inventory.
    """

    id:                str
    name:              str
    target_url:        str
    competitor_urls:   list[str] = Field(default_factory=list)
    status:            str       = ProjectStatus.PENDING.value
    created_at:        str       = ""
    updated_at:        str       = ""
    last_modified:     str       = ""
    project_dir:       str       = ""
    data_dir:          str       = ""   # crawler output dir (may equal project_dir)
    output_dir:        str       = ""
    completed_phases:  list[str] = Field(default_factory=list)
    pending_phases:    list[str] = Field(default_factory=list)
    phases:            dict[str, PhaseRecord] = Field(default_factory=dict)
    generated_files:   list[str] = Field(default_factory=list)
    settings_snapshot: dict[str, Any] = Field(default_factory=dict)
    notes:             str       = ""
    last_error:        str       = ""
    metadata:          dict[str, Any] = Field(default_factory=dict)

    def to_project_config(self) -> ProjectConfig:
        """Convert to the shared ProjectConfig type."""
        try:
            status = ProjectStatus(self.status)
        except ValueError:
            status = ProjectStatus.PENDING
        return ProjectConfig(
            id         = self.id,
            name       = self.name,
            target_url = self.target_url,
            status     = status,
            created_at = _parse_dt(self.created_at),
            updated_at = _parse_dt(self.updated_at or self.last_modified),
            output_dir = Path(self.output_dir) if self.output_dir else Path("outputs"),
            notes      = self.notes,
            metadata   = {
                **self.metadata,
                "project_dir":      self.project_dir,
                "data_dir":         self.data_dir,
                "competitor_urls":  self.competitor_urls,
                "completed_phases": self.completed_phases,
                "pending_phases":   self.pending_phases,
            },
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    if not value:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return datetime.utcnow()


# ── Main class ────────────────────────────────────────────────────────────────

class ProjectManager:
    """Creates, loads, saves, and coordinates WebMaker projects.

    Responsibilities:
    - Maintain the projects directory and per-project folder trees.
    - Persist ``project.json`` metadata and phase status.
    - Orchestrate WebsiteCrawler → BusinessAnalyzer → CompetitorAnalyzer →
      ContentOptimizer → WordPressGenerator → QAReviewer.
    - Resume from the last successfully completed phase.
    - Verify the local environment (PHP, WP-CLI, WordPress, MariaDB).

    Args:
        settings:     Application settings instance.
        projects_dir: Override for the projects storage directory.
    """

    def __init__(
        self,
        settings:     "Settings",
        projects_dir: Path | None = None,
        *,
        progress: ProgressManager | None = None,
        plugins: PluginRegistry | None = None,
        load_user_plugins: bool = True,
    ) -> None:
        self._settings     = settings
        self._projects_dir = Path(projects_dir or settings.projects_dir)
        self._active:      ProjectState | None = None
        self._progress     = progress or default_progress
        self._plugins      = plugins or default_plugins

        # Lazily constructed module instances (overridable in tests)
        self._crawler             = None
        self._business_analyzer   = None
        self._competitor_analyzer = None
        self._content_optimizer   = None
        self._wordpress_generator = None
        self._qa_reviewer         = None
        self._website_fixer       = None

        self._projects_dir.mkdir(parents=True, exist_ok=True)

        # Job system (used for granular tasks; pipeline API unchanged)
        jobs_dir = self._projects_dir / ".jobs"
        self._jobs = JobManager(
            storage_dir=jobs_dir,
            progress=self._progress,
            plugins=self._plugins,
        )
        self._register_job_handlers()

        if load_user_plugins:
            try:
                load_plugins(self._settings.project_root / "plugins")
            except Exception as exc:
                log.warning("Plugin discovery failed: {e}", e=exc)

        log.info(
            "ProjectManager ready (projects_dir={d})", d=self._projects_dir
        )

    @property
    def jobs(self) -> JobManager:
        """Internal JobManager used for discrete tasks."""
        return self._jobs

    @property
    def progress(self) -> ProgressManager:
        """Progress event bus for this manager."""
        return self._progress

    # ── Project lifecycle ──────────────────────────────────────────────────────

    def create_project(
        self,
        target_url:       str,
        name:             str = "",
        *,
        competitor_urls:  list[str] | None = None,
        force:            bool = False,
    ) -> ProjectConfig:
        """Initialise a new project for *target_url*.

        Creates the project folder tree and writes ``project.json``.

        Args:
            target_url:      Fully qualified URL of the website to analyse.
            name:            Optional human-readable project name (also used
                             as the folder slug when provided).
            competitor_urls: Optional competitor website URLs for phase 3.
            force:           If True, overwrite an existing project with the
                             same slug.

        Returns:
            Newly created ProjectConfig (status = PENDING).

        Raises:
            ProjectAlreadyExistsError: If a project for this slug already
                                       exists and *force* is False.
            ProjectError:              If the URL is invalid.
        """
        url = self._normalise_url(target_url)
        if not url:
            raise ProjectError(f"Invalid target URL: {target_url!r}")

        slug = self._slugify(name) if name.strip() else self._domain_slug(url)
        if not slug:
            slug = "project"

        project_dir = self._projects_dir / slug
        state_path  = project_dir / "project.json"

        if state_path.exists() and not force:
            raise ProjectAlreadyExistsError(
                f"Project already exists: {slug}",
                path=str(project_dir),
            )

        # Also guard against flat UUID-style index files from early stubs
        existing = self._find_by_url(url)
        if existing and not force:
            raise ProjectAlreadyExistsError(
                f"A project for {url!r} already exists (id={existing.id})",
                project_id=existing.id,
            )

        now = _utc_now()
        project_id = self._generate_id()
        display_name = name.strip() or slug

        self._ensure_project_dirs(project_dir)

        phases = {
            p.value: PhaseRecord(name=p.value)
            for p in _PIPELINE
        }

        state = ProjectState(
            id               = project_id,
            name             = display_name,
            target_url       = url,
            competitor_urls  = list(competitor_urls or []),
            status           = ProjectStatus.PENDING.value,
            created_at       = now,
            updated_at       = now,
            last_modified    = now,
            project_dir      = str(project_dir),
            data_dir         = str(project_dir),
            output_dir       = str(self._settings.outputs_dir / slug),
            completed_phases = [],
            pending_phases   = [p.value for p in _PIPELINE],
            phases           = phases,
            generated_files  = [],
            settings_snapshot = {
                "wordpress_dir": str(self._settings.wordpress_dir),
                "wordpress_url": self._settings.wordpress_url,
                "projects_dir":  str(self._projects_dir),
            },
            metadata = {
                "slug": slug,
            },
        )

        self._active = state
        self._write_state(state)
        self._append_project_log(state, f"Project created: {display_name} ({url})")
        self._progress.emit(
            0.0,
            "Creating project",
            phase="create",
            project_id=project_id,
            module="project_manager",
            status="running",
        )

        log.info(
            "Project created: id={i} name={n} dir={d}",
            i=project_id, n=display_name, d=project_dir,
        )
        return state.to_project_config()

    def load_project(self, project_id: str) -> ProjectConfig:
        """Load a previously saved project by ID or folder slug.

        Args:
            project_id: UUID string **or** project folder slug.

        Returns:
            Restored ProjectConfig.

        Raises:
            ProjectNotFoundError: If no matching project exists.
        """
        state = self._load_state(project_id)
        self._active = state
        log.info("Project loaded: {i} ({n})", i=state.id, n=state.name)
        return state.to_project_config()

    def open_project(self, project_id: str) -> ProjectConfig:
        """Alias for :meth:`load_project`."""
        return self.load_project(project_id)

    def save_project(self, project: ProjectConfig | None = None) -> None:
        """Persist project state to disk.

        If *project* is provided, merges its fields into the active
        :class:`ProjectState` (or loads it first).  If *project* is None,
        saves the currently active project.

        Args:
            project: Optional ProjectConfig to merge/save.

        Raises:
            ProjectError: If no project is active and none is provided.
        """
        if project is not None:
            # Merge into active state or load by id
            if self._active is None or self._active.id != project.id:
                try:
                    self._active = self._load_state(project.id)
                except ProjectNotFoundError:
                    # Build fresh state from ProjectConfig
                    self._active = self._state_from_config(project)

            self._merge_config_into_state(self._active, project)

        if self._active is None:
            raise ProjectError("No active project to save")

        self._active.updated_at    = _utc_now()
        self._active.last_modified = self._active.updated_at
        self._write_state(self._active)
        log.debug("Project saved: {i}", i=self._active.id)

    def delete_project(self, project_id: str) -> None:
        """Remove a project folder and its associated files.

        Args:
            project_id: UUID string or folder slug.

        Raises:
            ProjectNotFoundError: If the project does not exist.
        """
        state = self._load_state(project_id)
        project_dir = Path(state.project_dir)

        if self._active and self._active.id == state.id:
            self._active = None

        if project_dir.exists():
            shutil.rmtree(project_dir, ignore_errors=True)
            log.info("Deleted project directory: {d}", d=project_dir)
        else:
            # Legacy flat file
            flat = self._project_file(state.id)
            if flat.exists():
                flat.unlink()

        log.info("Project deleted: {i}", i=state.id)

    def list_projects(self) -> list[ProjectConfig]:
        """Return all saved projects sorted by creation date (newest first).

        Returns:
            List of ProjectConfig models.
        """
        projects: list[ProjectConfig] = []

        # Folder-based projects (project.json inside each dir)
        for path in sorted(self._projects_dir.iterdir() if self._projects_dir.exists() else []):
            if not path.is_dir():
                continue
            state_file = path / "project.json"
            if not state_file.exists():
                continue
            try:
                state = self._read_state_file(state_file)
                projects.append(state.to_project_config())
            except ProjectError as exc:
                log.warning("Skipping corrupt project at {p}: {e}", p=path, e=exc)

        # Legacy flat UUID json files
        for path in self._projects_dir.glob("*.json"):
            if path.name == "index.json":
                continue
            try:
                cfg = self._project_from_file(path)
                # Avoid duplicates if already loaded via folder
                if not any(p.id == cfg.id for p in projects):
                    projects.append(cfg)
            except ProjectError:
                continue

        projects.sort(key=lambda p: p.created_at, reverse=True)
        return projects

    # ── State helpers ──────────────────────────────────────────────────────────

    @property
    def active_project(self) -> ProjectConfig | None:
        """Currently active project, or None if no project is loaded."""
        return self._active.to_project_config() if self._active else None

    @property
    def active_state(self) -> ProjectState | None:
        """Full active ProjectState (including phase tracking)."""
        return self._active

    def set_status(self, status: ProjectStatus) -> None:
        """Update the active project's status and save to disk.

        Args:
            status: New ProjectStatus value.

        Raises:
            ProjectError: If no project is active.
        """
        if self._active is None:
            raise ProjectError("No active project")
        self._active.status        = status.value
        self._active.updated_at    = _utc_now()
        self._active.last_modified = self._active.updated_at
        self._write_state(self._active)
        log.info("Project status → {s}", s=status.value)

    def set_competitor_urls(self, urls: list[str]) -> None:
        """Set competitor URLs on the active project and save.

        Args:
            urls: List of competitor website URLs.

        Raises:
            ProjectError: If no project is active.
        """
        if self._active is None:
            raise ProjectError("No active project")
        cleaned = [u for u in (self._normalise_url(x) for x in urls) if u]
        self._active.competitor_urls = cleaned
        self.save_project()
        log.info("Competitor URLs set: {n}", n=len(cleaned))

    def get_phase_status(self, phase: str | PhaseName) -> PhaseStatus:
        """Return the status of a single phase for the active project.

        Args:
            phase: Phase name or PhaseName enum.

        Returns:
            PhaseStatus value.

        Raises:
            ProjectError: If no project is active or phase is unknown.
        """
        if self._active is None:
            raise ProjectError("No active project")
        key = phase.value if isinstance(phase, PhaseName) else str(phase)
        record = self._active.phases.get(key)
        if record is None:
            raise ProjectError(f"Unknown phase: {key}")
        return PhaseStatus(record.status)

    def get_data_dir(self) -> Path:
        """Return the data directory used by analysis modules.

        Prefers ``data_dir`` (crawler output) over ``project_dir``.

        Raises:
            ProjectError: If no project is active.
        """
        if self._active is None:
            raise ProjectError("No active project")
        return Path(self._active.data_dir or self._active.project_dir)

    def get_project_dir(self) -> Path:
        """Return the active project's root directory.

        Raises:
            ProjectError: If no project is active.
        """
        if self._active is None:
            raise ProjectError("No active project")
        return Path(self._active.project_dir)

    def sync_crawl_data_dir(self, url: str | None = None) -> Path:
        """Keep ``data_dir`` on the named project folder (target owns crawl).

        Competitor crawls live under ``projects/competitors/<slug>/`` separately.
        """
        if self._active is None:
            raise ProjectError("No active project")
        self._active.data_dir = str(Path(self._active.project_dir))
        self.save_project()
        return self.get_data_dir()

    # ── Workflow orchestration ─────────────────────────────────────────────────

    def run_pipeline(
        self,
        *,
        competitor_urls: list[str] | None = None,
        force_phases:    list[str] | None = None,
        skip_phases:     list[str] | None = None,
        stop_on_error:   bool = True,
    ) -> ProjectConfig:
        """Execute the full module pipeline for the active project.

        Skips phases already marked ``completed`` unless listed in
        *force_phases*.  Stops on the first failure when *stop_on_error*
        is True (default).

        Args:
            competitor_urls: Override / set competitor URLs before compete.
            force_phases:    Phase names to re-run even if completed.
            skip_phases:     Phase names to skip entirely.
            stop_on_error:   Halt the pipeline on first failure.

        Returns:
            Updated ProjectConfig.

        Raises:
            ProjectError: If no project is active.
        """
        if self._active is None:
            raise ProjectError("No active project — create or load one first")

        if competitor_urls is not None:
            self.set_competitor_urls(competitor_urls)

        force = {p.lower() for p in (force_phases or [])}
        skip  = {p.lower() for p in (skip_phases or [])}

        self._append_project_log(
            self._active,
            f"Pipeline started (force={sorted(force) or 'none'}, "
            f"skip={sorted(skip) or 'none'})",
        )
        self._progress.emit_phase(
            "create",
            "Starting pipeline",
            project_id=self._active.id,
            within_phase=1.0,
        )

        for phase in _PIPELINE:
            if phase.value in skip:
                log.info("Skipping phase: {p}", p=phase.value)
                continue

            record = self._active.phases.get(phase.value)
            if (
                record
                and record.status == PhaseStatus.COMPLETED.value
                and phase.value not in force
            ):
                log.info("Phase already completed — skipping: {p}", p=phase.value)
                continue

            ok = self._run_phase(phase)
            if not ok and stop_on_error:
                self.set_status(ProjectStatus.FAILED)
                self._append_project_log(
                    self._active,
                    f"Pipeline stopped after failed phase: {phase.value}",
                )
                self._progress.emit(
                    self._progress.PIPELINE_MILESTONES.get(phase.value, 0.0),
                    f"Pipeline failed at {phase.value}",
                    phase=phase.value,
                    project_id=self._active.id,
                    status="failed",
                    module="project_manager",
                )
                return self._active.to_project_config()

        # All requested phases done?
        # Skipped phases remain pending so a later resume/tab can run them.
        pending = [
            p.value for p in _PIPELINE
            if self._active.phases.get(p.value)
            and self._active.phases[p.value].status != PhaseStatus.COMPLETED.value
        ]
        self._active.pending_phases = pending
        if not pending:
            self.set_status(ProjectStatus.COMPLETED)
            self._append_project_log(self._active, "Pipeline completed successfully")
            self._progress.emit(
                100.0,
                "Completed",
                phase="complete",
                project_id=self._active.id,
                status="completed",
                module="project_manager",
            )
        else:
            self.save_project()

        return self._active.to_project_config()

    def resume(
        self,
        *,
        competitor_urls: list[str] | None = None,
        stop_on_error:   bool = True,
    ) -> ProjectConfig:
        """Resume the active project from the first incomplete phase.

        Does not re-run completed phases.

        Args:
            competitor_urls: Optional competitor URL override.
            stop_on_error:   Halt on first failure.

        Returns:
            Updated ProjectConfig.
        """
        log.info("Resuming project from last completed phase")
        return self.run_pipeline(
            competitor_urls=competitor_urls,
            force_phases=[],
            stop_on_error=stop_on_error,
        )

    def run_phase(
        self,
        phase: str | PhaseName,
        *,
        force: bool = True,
    ) -> bool:
        """Run a single pipeline phase on the active project.

        Args:
            phase: Phase name.
            force: If True (default), run even when already completed.

        Returns:
            True if the phase completed successfully.

        Raises:
            ProjectError: If no project is active or the phase is unknown.
        """
        if self._active is None:
            raise ProjectError("No active project")

        if isinstance(phase, str):
            try:
                phase_enum = PhaseName(phase.lower())
            except ValueError as exc:
                raise ProjectError(f"Unknown phase: {phase}") from exc
        else:
            phase_enum = phase

        record = self._active.phases.get(phase_enum.value)
        if (
            record
            and record.status == PhaseStatus.COMPLETED.value
            and not force
        ):
            log.info("Phase {p} already completed", p=phase_enum.value)
            return True

        return self._run_phase(phase_enum)

    # ── Job API (granular tasks; does not replace the pipeline) ─────────────────

    def run_job(
        self,
        job_type: str | JobType,
        *,
        params: dict[str, Any] | None = None,
        max_retries: int = 1,
    ) -> JobResult:
        """Create and immediately execute a discrete job for the active project.

        Examples: generate_homepage, rebuild_wordpress, run_qa, rerun_competitors.
        """
        if self._active is None:
            raise ProjectError("No active project — create or load one first")
        job = self._jobs.create_job(
            job_type,
            self._active.id,
            params=params,
            max_retries=max_retries,
            enqueue=False,
        )
        return self._jobs.execute(job.id)

    def enqueue_job(
        self,
        job_type: str | JobType,
        *,
        params: dict[str, Any] | None = None,
        max_retries: int = 1,
    ) -> Job:
        """Queue a job for later :meth:`JobManager.process_queue`."""
        if self._active is None:
            raise ProjectError("No active project — create or load one first")
        return self._jobs.create_job(
            job_type,
            self._active.id,
            params=params,
            max_retries=max_retries,
            enqueue=True,
        )

    def _register_job_handlers(self) -> None:
        """Wire JobType handlers to existing phase / module methods."""

        def _require_active(job: Job) -> None:
            if self._active is None or self._active.id != job.project_id:
                # Load project by id if needed
                self.load_project(job.project_id)

        def _phase_job(phase: PhaseName):
            def handler(job: Job) -> JobResult:
                _require_active(job)
                self._jobs.update_progress(job.id, 20, f"Starting {phase.value}")
                ok = self._run_phase(phase)
                arts = list(
                    (self._active.phases.get(phase.value) or PhaseRecord()).artifacts
                ) if self._active else []
                return JobResult(
                    job_id=job.id,
                    success=ok,
                    status=JobStatus.COMPLETED.value if ok else JobStatus.FAILED.value,
                    message=f"Phase {phase.value} {'ok' if ok else 'failed'}",
                    artifacts=arts,
                    error="" if ok else (self._active.last_error if self._active else "failed"),
                )
            return handler

        def _optimize_pages(slugs: list[str]):
            def handler(job: Job) -> JobResult:
                _require_active(job)
                assert self._active is not None
                data_dir = self.get_data_dir()
                self._jobs.update_progress(job.id, 25, f"Generating pages: {slugs}")
                optimizer = self._get_content_optimizer()
                # Prefer selective API when available; else full optimize
                if hasattr(optimizer, "optimize_pages"):
                    summary = optimizer.optimize_pages(data_dir, slugs=slugs)
                else:
                    summary = optimizer.optimize_from_directory(data_dir)
                artifacts = [
                    str(p) for p in sorted((data_dir / "json").glob("optimized_*.json"))
                    if any(s in p.name for s in slugs) or not slugs
                ]
                return JobResult(
                    job_id=job.id,
                    success=True,
                    status=JobStatus.COMPLETED.value,
                    message=f"Generated: {', '.join(slugs)}",
                    artifacts=artifacts,
                    data=summary if isinstance(summary, dict) else {},
                )
            return handler

        self._jobs.register_handlers({
            JobType.GENERATE_HOMEPAGE.value:     _optimize_pages(["homepage"]),
            JobType.GENERATE_FAQ.value:          _optimize_pages(["faq"]),
            JobType.GENERATE_SERVICE_PAGE.value: _optimize_pages(["services"]),
            JobType.GENERATE_ABOUT.value:        _optimize_pages(["about"]),
            JobType.GENERATE_CONTACT.value:      _optimize_pages(["contact"]),
            JobType.REBUILD_WORDPRESS.value:     _phase_job(PhaseName.GENERATE),
            JobType.RUN_QA.value:                _phase_job(PhaseName.REVIEW),
            JobType.RERUN_COMPETITORS.value:     _phase_job(PhaseName.COMPETE),
            JobType.RUN_PHASE.value:             (
                lambda job: _phase_job(
                    PhaseName(str(job.params.get("phase", "crawl")).lower())
                )(job)
            ),
        })

    # ── Environment verification ───────────────────────────────────────────────

    def verify_environment(self) -> dict[str, bool]:
        """Check that required external tools are available.

        Returns:
            Mapping of tool name to availability boolean.
            Keys: ``php``, ``wpcli``, ``wordpress``, ``mariadb``, ``projects_dir``.
        """
        result = {
            "php":          self._settings.php_exe.exists(),
            "wpcli":        self._settings.wpcli_path.exists(),
            "wordpress":    (
                self._settings.wordpress_dir.exists()
                and (self._settings.wordpress_dir / "wp-config.php").exists()
            ),
            "mariadb":      (
                (self._settings.mariadb_dir / "bin" / "mysqld.exe").exists()
                or (self._settings.mariadb_dir / "bin" / "mysqld").exists()
            ),
            "projects_dir": self._projects_dir.exists(),
        }
        log.info("Environment check: {r}", r=result)
        return result

    # ── Internal: phase runners ────────────────────────────────────────────────

    def _run_phase(self, phase: PhaseName) -> bool:
        """Execute one phase, update status, and persist state.

        Args:
            phase: Phase to run.

        Returns:
            True on success, False on failure.
        """
        assert self._active is not None
        state = self._active

        record = state.phases.get(phase.value) or PhaseRecord(name=phase.value)
        record.status     = PhaseStatus.RUNNING.value
        record.started_at = _utc_now()
        record.error      = ""
        record.finished_at = ""
        record.duration_s = 0.0
        state.phases[phase.value] = record

        state.status = _PHASE_TO_STATUS[phase].value
        state.updated_at = _utc_now()
        self._write_state(state)

        log.info("=== Phase START: {p} ===", p=phase.value)
        self._append_project_log(state, f"Phase started: {phase.value}")
        self._progress.emit_phase(
            phase.value,
            {
                "crawl": "Crawling website",
                "analyze": "Business analysis",
                "compete": "Competitor analysis",
                "optimize": "Generating content",
                "generate": "Building WordPress",
                "review": "Running QA",
                "fix": "Fixing website",
            }.get(phase.value, f"Running {phase.value}"),
            project_id=state.id,
            within_phase=0.0,
        )
        self._plugins.call_before_phase(
            phase.value,
            state,
            {"project_id": state.id, "data_dir": state.data_dir},
        )
        t0 = time.perf_counter()

        runners: dict[PhaseName, Callable[[], list[str]]] = {
            PhaseName.CRAWL:    self._phase_crawl,
            PhaseName.ANALYZE:  self._phase_analyze,
            PhaseName.COMPETE:  self._phase_compete,
            PhaseName.OPTIMIZE: self._phase_optimize,
            PhaseName.GENERATE: self._phase_generate,
            PhaseName.REVIEW:   self._phase_review,
            PhaseName.FIX:      self._phase_fix,
        }

        try:
            artifacts = runners[phase]()
            elapsed = time.perf_counter() - t0

            record.status      = PhaseStatus.COMPLETED.value
            record.finished_at = _utc_now()
            record.duration_s  = round(elapsed, 3)
            record.artifacts   = artifacts
            record.error       = ""

            if phase.value not in state.completed_phases:
                state.completed_phases.append(phase.value)
            state.pending_phases = [
                p.value for p in _PIPELINE
                if p.value not in state.completed_phases
            ]
            for art in artifacts:
                if art not in state.generated_files:
                    state.generated_files.append(art)

            state.updated_at    = _utc_now()
            state.last_modified = state.updated_at
            state.last_error    = ""
            self._write_state(state)

            log.info(
                "=== Phase DONE: {p} ({t:.1f}s) ===",
                p=phase.value, t=elapsed,
            )
            self._append_project_log(
                state,
                f"Phase completed: {phase.value} ({elapsed:.1f}s)",
            )
            self._progress.emit_phase(
                phase.value,
                f"Completed {phase.value}",
                project_id=state.id,
                within_phase=1.0,
                status="completed",
            )
            self._plugins.call_after_phase(
                phase.value,
                state,
                success=True,
                context={"project_id": state.id, "artifacts": artifacts},
            )
            return True

        except Exception as exc:
            elapsed = time.perf_counter() - t0
            record.status      = PhaseStatus.FAILED.value
            record.finished_at = _utc_now()
            record.duration_s  = round(elapsed, 3)
            record.error       = str(exc)

            state.status        = ProjectStatus.FAILED.value
            state.last_error    = str(exc)
            state.updated_at    = _utc_now()
            state.last_modified = state.updated_at
            # Keep phase in pending
            if phase.value not in state.pending_phases:
                state.pending_phases.append(phase.value)
            self._write_state(state)

            log.error(
                "=== Phase FAILED: {p} — {e} ===",
                p=phase.value, e=exc,
            )
            self._append_project_log(
                state,
                f"Phase failed: {phase.value} — {exc}",
            )
            self._progress.emit_phase(
                phase.value,
                f"Failed {phase.value}",
                project_id=state.id,
                status="failed",
            )
            self._plugins.call_after_phase(
                phase.value,
                state,
                success=False,
                context={"project_id": state.id, "error": str(exc)},
            )
            return False

    def _phase_crawl(self) -> list[str]:
        """Run WebsiteCrawler on the active project's target URL.

        Crawl output always lands inside the named project folder
        (``project_dir``), not a separate domain-slug folder.
        """
        assert self._active is not None

        project_dir = Path(self._active.project_dir)
        force = bool(self._active.metadata.get("force_crawl"))
        pages_json = project_dir / "json" / "pages.json"

        if pages_json.is_file() and not force:
            log.info(
                "Crawl outputs already present — skipping re-crawl ({p})",
                p=pages_json,
            )
            self._active.data_dir = str(project_dir)
            artifacts = []
            for name in ("pages.json", "crawl_summary.json", "images.json", "navigation.json"):
                p = project_dir / "json" / name
                if p.exists():
                    artifacts.append(str(p))
            return artifacts

        if force:
            self._wipe_crawl_media(project_dir)

        crawler = self._get_crawler()
        result = crawler.crawl(self._active.target_url, output_dir=project_dir)

        # Named project folder owns all target crawl artifacts.
        self._active.data_dir = str(project_dir)

        artifacts = []
        data = project_dir
        for name in ("pages.json", "crawl_summary.json", "images.json", "navigation.json"):
            p = data / "json" / name
            if p.exists():
                artifacts.append(str(p))

        self._active.metadata["crawl_total_pages"] = getattr(result, "total_pages", 0)
        self._active.metadata["crawl_errors"] = list(getattr(result, "errors", []) or [])
        self._active.metadata["force_crawl"] = False
        return artifacts

    @staticmethod
    def _wipe_crawl_media(project_dir: Path) -> None:
        """Clear crawl media for clean screenshots; keep business/competitor AI outputs."""
        import shutil

        for sub in ("screenshots", "pages", "images", "assets", "raw"):
            path = project_dir / sub
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                log.info("Wiped crawl folder for fresh run: {p}", p=path)

        json_dir = project_dir / "json"
        # Remove crawl-only JSON; keep business_profile / competitor analysis.
        for name in (
            "pages.json",
            "crawl_summary.json",
            "images.json",
            "navigation.json",
        ):
            p = json_dir / name
            if p.is_file():
                p.unlink(missing_ok=True)
        pages_sub = json_dir / "pages"
        if pages_sub.is_dir():
            shutil.rmtree(pages_sub, ignore_errors=True)

        for sub in ("pages", "images", "screenshots", "assets", "raw",
                    "json", "json/pages", "artifacts"):
            (project_dir / sub).mkdir(parents=True, exist_ok=True)

    def _phase_analyze(self) -> list[str]:
        """Run BusinessAnalyzer on crawler output (skips Claude if profile/.md exists)."""
        assert self._active is not None
        data_dir = self.get_data_dir()
        analyzer = self._get_business_analyzer()
        force_ai = bool(self._active.metadata.get("force_ai"))
        info = analyzer.analyze_from_directory(data_dir, force_ai=force_ai)

        profile = data_dir / "json" / "business_profile.json"
        artifacts = [str(profile)] if profile.exists() else []
        md = data_dir / "json" / "target_business.md"
        if md.exists():
            artifacts.append(str(md))
        self._active.metadata["company_name"] = getattr(info, "name", "") or ""
        self._active.metadata["industry"] = getattr(info, "industry", "") or ""
        return artifacts

    def _phase_compete(self) -> list[str]:
        """Run CompetitorAnalyzer when competitor URLs are configured."""
        assert self._active is not None
        data_dir = self.get_data_dir()
        urls = list(self._active.competitor_urls)

        if not urls:
            log.warning(
                "No competitor URLs configured — compete phase completed with no analysis"
            )
            record = self._active.phases[PhaseName.COMPETE.value]
            record.warnings.append(
                "No competitor URLs provided; skipped competitor crawl/analysis"
            )
            return []

        analyzer = self._get_competitor_analyzer()
        force = bool(
            self._active.metadata.get("force_compete")
            or self._active.metadata.get("force_crawl")
        )
        analyzer.analyze_from_urls(
            urls, data_dir, force=force, max_competitors=max(len(urls), 1),
        )

        artifacts = []
        for name in (
            "competitors.json",
            "competitor_analysis.json",
            "comparison_report.json",
            "competitor_structure.md",
        ):
            p = data_dir / "json" / name
            if p.exists():
                artifacts.append(str(p))
        self._active.metadata["force_compete"] = False
        return artifacts

    def _phase_optimize(self) -> list[str]:
        """Run ContentOptimizer on prior analysis outputs."""
        assert self._active is not None
        data_dir = self.get_data_dir()
        optimizer = self._get_content_optimizer()
        raw_slugs = self._active.metadata.get("page_slugs") or []
        page_slugs = tuple(str(s) for s in raw_slugs if str(s).strip()) or None
        if page_slugs:
            log.info("Optimising selected pages only: {s}", s=list(page_slugs))
            summary = optimizer.optimize_from_directory(
                data_dir, page_slugs=page_slugs
            )
        else:
            summary = optimizer.optimize_from_directory(data_dir)

        artifacts = []
        json_dir = data_dir / "json"
        for path in sorted(json_dir.glob("optimized_*.json")):
            artifacts.append(str(path))
        for name in ("meta_data.json", "content_review.json"):
            p = json_dir / name
            if p.exists():
                artifacts.append(str(p))

        if isinstance(summary, dict):
            self._active.metadata["pages_generated"] = summary.get("pages_generated", [])
            errors = summary.get("errors") or []
            if errors:
                self._active.phases[PhaseName.OPTIMIZE.value].warnings.extend(
                    str(e) for e in errors
                )
        return artifacts

    def _phase_generate(self) -> list[str]:
        """Run WordPressGenerator against optimised content."""
        assert self._active is not None
        data_dir = self.get_data_dir()
        generator = self._get_wordpress_generator()
        regenerate = bool(self._active.metadata.get("regenerate"))
        # First-time build without regenerate: create pages but don't wipe.
        # Regenerate: wipe + full rebuild. Update-only is used by the fix path.
        result = generator.generate_from_directory(
            data_dir,
            reset=regenerate,
            update_only=False,
        )

        artifacts = []
        report = data_dir / "json" / "generation_report.json"
        if report.exists():
            artifacts.append(str(report))

        self._active.metadata["wp_url"] = getattr(result, "wp_url", "") or ""
        self._active.metadata["pages_created"] = list(
            getattr(result, "pages_created", []) or []
        )
        if getattr(result, "errors", None):
            self._active.phases[PhaseName.GENERATE.value].warnings.extend(
                str(e) for e in result.errors
            )
        if getattr(result, "success", True) is False:
            raise ProjectError(
                "WordPress generation reported failure",
                errors=list(getattr(result, "errors", []) or []),
            )
        return artifacts

    def _phase_review(self) -> list[str]:
        """Run QAReviewer on the generated site / project JSON."""
        assert self._active is not None
        data_dir = self.get_data_dir()
        reviewer = self._get_qa_reviewer()
        report = reviewer.review_from_directory(
            data_dir,
            wp_url=self._active.metadata.get("wp_url") or self._settings.wordpress_url,
            skip_live_checks=False,
            skip_ai=False,
        )

        artifacts = []
        for name in (
            "qa_report.json",
            "seo_review.json",
            "content_review.json",
            "website_score.json",
        ):
            p = data_dir / "json" / name
            if p.exists():
                artifacts.append(str(p))

        self._active.metadata["qa_score"] = getattr(report, "overall_score", 0.0)
        self._active.metadata["qa_passed"] = getattr(report, "passed", False)
        better = bool(getattr(report, "significantly_better_than_original", False))
        self._active.metadata["significantly_better_than_original"] = better
        comment = str(getattr(report, "comparison_comment", "") or "")
        self._active.metadata["qa_comparison_comment"] = comment
        if comment:
            self._append_project_log(
                self._active,
                f"QA vs original: better={better} — {comment[:400]}",
            )
        return artifacts

    def _phase_fix(self) -> list[str]:
        """Run WebsiteFixer (Claude Sonnet) then rebuild WordPress pages."""
        assert self._active is not None
        data_dir = self.get_data_dir()
        fixer = self._get_website_fixer()
        raw_slugs = self._active.metadata.get("page_slugs") or []
        page_slugs = tuple(str(s) for s in raw_slugs if str(s).strip()) or None
        regenerate = bool(self._active.metadata.get("regenerate"))
        # Default: edit current demo in place. Regenerate = wipe + full rebuild.
        summary = fixer.fix_from_directory(
            data_dir,
            page_slugs=page_slugs,
            reset=regenerate,
            update_only=not regenerate,
        )

        artifacts = []
        report = data_dir / "json" / "fix_report.json"
        if report.exists():
            artifacts.append(str(report))
        for path in sorted((data_dir / "json").glob("optimized_*.json")):
            artifacts.append(str(path))

        if isinstance(summary, dict):
            self._active.metadata["pages_fixed"] = summary.get("pages_fixed", [])
            errors = summary.get("errors") or []
            if errors:
                self._active.phases[PhaseName.FIX.value].warnings.extend(
                    str(e) for e in errors
                )
        return artifacts

    def run_optimize_fix(
        self,
        *,
        page_slugs: list[str] | None = None,
        max_improve_rounds: int = 5,
        force: bool = True,
        regenerate: bool = False,
    ) -> ProjectConfig:
        """Optimize → generate → QA, then Fix↔QA until significantly better.

        Args:
            page_slugs:          Subset of pages (homepage/about/…); None = all.
            max_improve_rounds:  Max Sonnet fix → QA cycles after the first review.
            force:               Re-run phases even if previously completed.
            regenerate:          If True, re-optimise content and wipe/rebuild the
                                 demo. If False, keep existing optimized JSON and
                                 demo pages; only QA and in-place fix edits run
                                 (unless no demo content exists yet — then a
                                 first build without wipe is performed).

        Returns:
            Updated ProjectConfig.
        """
        if self._active is None:
            raise ProjectError("No active project — create or load one first")

        slugs = [s.strip().lower() for s in (page_slugs or []) if str(s).strip()]
        self._active.metadata["page_slugs"] = slugs
        self._active.metadata["max_improve_rounds"] = int(max_improve_rounds)
        self._active.metadata["regenerate"] = bool(regenerate)
        self._active.metadata["significantly_better_than_original"] = False
        self.save_project()

        data_dir = self.get_data_dir()
        json_dir = data_dir / "json"
        has_optimized = any(json_dir.glob("optimized_*.json"))
        has_generation = (json_dir / "generation_report.json").is_file()
        demo_ready = has_optimized and has_generation

        if regenerate:
            mode = "regenerate (wipe + rebuild)"
            force_phases_first = ["optimize", "generate", "review"]
            skip_first = ["crawl", "analyze", "compete", "fix"]
        elif demo_ready:
            mode = "edit existing demo (QA → fix in place)"
            force_phases_first = ["review"]
            skip_first = [
                "crawl", "analyze", "compete", "optimize", "generate", "fix",
            ]
        else:
            mode = "first build (no wipe)"
            force_phases_first = ["optimize", "generate", "review"]
            skip_first = ["crawl", "analyze", "compete", "fix"]

        self._append_project_log(
            self._active,
            f"Build started [{mode}]: pages={slugs or 'all'}, "
            f"max_rounds={max_improve_rounds}",
        )

        result = self.run_pipeline(
            force_phases=force_phases_first if force else [],
            skip_phases=skip_first,
            stop_on_error=True,
        )
        if self._active.status == ProjectStatus.FAILED.value:
            return result

        rounds = max(0, int(max_improve_rounds))
        for i in range(1, rounds + 1):
            if self._active.metadata.get("significantly_better_than_original"):
                self._append_project_log(
                    self._active,
                    f"Demo already significantly better — stopping before fix round {i}",
                )
                break

            self._append_project_log(
                self._active,
                f"Improve round {i}/{rounds}: Sonnet fix → QA",
            )
            self._progress.emit(
                min(95.0, 70.0 + i * 4.0),
                f"Improve round {i}/{rounds}",
                phase="fix",
                project_id=self._active.id,
                module="project_manager",
            )
            ok_fix = self.run_phase(PhaseName.FIX, force=True)
            if not ok_fix:
                self.set_status(ProjectStatus.FAILED)
                return self._active.to_project_config()

            ok_qa = self.run_phase(PhaseName.REVIEW, force=True)
            if not ok_qa:
                self.set_status(ProjectStatus.FAILED)
                return self._active.to_project_config()

            if self._active.metadata.get("significantly_better_than_original"):
                self._append_project_log(
                    self._active,
                    f"QA accepted demo as significantly better after round {i}",
                )
                break
        else:
            if rounds and not self._active.metadata.get(
                "significantly_better_than_original"
            ):
                msg = (
                    f"Reached max improve rounds ({rounds}) without "
                    "QA confirming significantly better than original"
                )
                self._append_project_log(self._active, msg)
                fix_rec = self._active.phases.get(PhaseName.FIX.value)
                if fix_rec:
                    fix_rec.warnings.append(msg)

        pending = [
            p.value for p in _PIPELINE
            if self._active.phases.get(p.value)
            and self._active.phases[p.value].status != PhaseStatus.COMPLETED.value
        ]
        self._active.pending_phases = pending
        if not pending and self._active.metadata.get(
            "significantly_better_than_original"
        ):
            self.set_status(ProjectStatus.COMPLETED)
        else:
            self.save_project()

        return self._active.to_project_config()

    def run_theme_apply(
        self,
        *,
        theme_id: str,
        template_id: str,
        page_slugs: list[str] | None = None,
    ) -> "ProjectConfig":
        """Install a theme+template and hydrate it with the client's content.

        Pipeline:
        1. ``install_theme_stack``   — download/activate theme + plugins
        2. ``import_starter_template`` — pull template content into WordPress
        3. ``hydrate_template_content`` — overwrite placeholder content with
           the client's AI-generated JSON (optimized_*.json)

        The active project must already have run the Optimize phase so that
        ``optimized_*.json`` files exist in the project's ``json/`` directory.

        Args:
            theme_id:     Catalog theme id (e.g. ``"kadence"``).
            template_id:  Catalog template id (e.g. ``"home-services"``).
            page_slugs:   Optional subset of pages to hydrate; ``None`` = all.

        Returns:
            Updated ProjectConfig.

        Raises:
            ProjectError: If no active project or optimized content is missing.
        """
        if self._active is None:
            raise ProjectError("No active project — create or load one first")

        data_dir = self.get_data_dir()
        json_dir = data_dir / "json"

        if not any(json_dir.glob("optimized_*.json")):
            raise ProjectError(
                "No optimized_*.json found. "
                "Run the Optimize phase before applying a theme.",
                project_id=self._active.id,
            )

        self._active.metadata["theme_id"] = theme_id
        self._active.metadata["template_id"] = template_id
        self.save_project()

        generator = self._get_wordpress_generator()
        errors: list[str] = []

        self._append_project_log(
            self._active,
            f"Theme apply started: theme={theme_id}, template={template_id}",
        )
        self._progress.emit(
            10.0, "Installing theme stack",
            phase="generate", project_id=self._active.id,
            module="project_manager", status="running",
        )

        # 1. Install theme + plugins
        try:
            generator.install_theme_stack(theme_id)
            self._append_project_log(
                self._active, f"Theme stack installed: {theme_id}"
            )
        except Exception as exc:
            msg = f"Theme install failed: {exc}"
            errors.append(msg)
            log.error(msg)
            self._active.last_error = msg
            self.set_status(ProjectStatus.FAILED)
            return self._active.to_project_config()

        self._progress.emit(
            40.0, "Importing starter template",
            phase="generate", project_id=self._active.id,
            module="project_manager", status="running",
        )

        # 2. Import starter template
        try:
            generator.import_starter_template(template_id, theme_id)
            self._append_project_log(
                self._active, f"Template imported: {template_id}"
            )
        except Exception as exc:
            # Non-fatal: if import fails we still hydrate with our content
            log.warning(
                "Template import had issues ({e}); proceeding with hydration",
                e=exc,
            )
            self._append_project_log(
                self._active,
                f"Template import warning (continuing): {exc}",
            )

        self._progress.emit(
            65.0, "Hydrating template with client content",
            phase="generate", project_id=self._active.id,
            module="project_manager", status="running",
        )

        # 3. Hydrate with client content
        try:
            result = generator.hydrate_template_content(
                data_dir, page_slugs=page_slugs
            )
            self._active.metadata["wp_url"] = (
                getattr(result, "wp_url", "") or self._settings.wordpress_url
            )
            self._active.metadata["theme_applied"] = theme_id
            self._active.metadata["template_applied"] = template_id
            pages_done = list(getattr(result, "pages_created", []) or [])
            self._active.metadata["pages_hydrated"] = pages_done
            if getattr(result, "errors", None):
                errors.extend(str(e) for e in result.errors)
            self._append_project_log(
                self._active,
                f"Hydration complete: {len(pages_done)} page(s) updated",
            )
        except Exception as exc:
            msg = f"Content hydration failed: {exc}"
            errors.append(msg)
            log.error(msg)
            self._active.last_error = msg
            self.set_status(ProjectStatus.FAILED)
            return self._active.to_project_config()

        self._progress.emit(
            100.0, "Theme applied",
            phase="generate", project_id=self._active.id,
            module="project_manager", status="completed",
        )

        if errors:
            self._append_project_log(
                self._active, f"Theme apply finished with warnings: {errors}"
            )
        else:
            self.set_status(ProjectStatus.COMPLETED)

        return self._active.to_project_config()

    # ── Internal: module accessors (lazy, injectable) ──────────────────────────

    def _get_crawler(self):
        if self._crawler is None:
            from webmaker.modules.website_crawler import WebsiteCrawler
            self._crawler = WebsiteCrawler(self._settings)
        return self._crawler

    def _get_business_analyzer(self):
        if self._business_analyzer is None:
            from webmaker.modules.business_analyzer import BusinessAnalyzer
            self._business_analyzer = BusinessAnalyzer(self._settings)
        return self._business_analyzer

    def _get_competitor_analyzer(self):
        if self._competitor_analyzer is None:
            from webmaker.modules.competitor_analyzer import CompetitorAnalyzer
            self._competitor_analyzer = CompetitorAnalyzer(self._settings)
        return self._competitor_analyzer

    def _get_content_optimizer(self):
        if self._content_optimizer is None:
            from webmaker.modules.content_optimizer import ContentOptimizer
            self._content_optimizer = ContentOptimizer(self._settings)
        return self._content_optimizer

    def _get_wordpress_generator(self):
        if self._wordpress_generator is None:
            from webmaker.modules.wordpress_generator import WordPressGenerator
            self._wordpress_generator = WordPressGenerator(self._settings)
        return self._wordpress_generator

    def _get_qa_reviewer(self):
        if self._qa_reviewer is None:
            from webmaker.modules.qa_reviewer import QAReviewer
            self._qa_reviewer = QAReviewer(self._settings)
        return self._qa_reviewer

    def _get_website_fixer(self):
        if self._website_fixer is None:
            from webmaker.modules.website_fixer import WebsiteFixer
            self._website_fixer = WebsiteFixer(self._settings)
        return self._website_fixer

    # ── Internal: persistence ──────────────────────────────────────────────────

    def _write_state(self, state: ProjectState) -> None:
        """Write ``project.json`` into the project directory."""
        project_dir = Path(state.project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / "project.json"
        write_versioned_json(path, state.model_dump())

    def _load_state(self, project_id: str) -> ProjectState:
        """Load ProjectState by UUID or folder slug."""
        # 1) Direct folder slug
        candidate = self._projects_dir / project_id / "project.json"
        if candidate.exists():
            return self._read_state_file(candidate)

        # 2) Scan folders for matching id / case-insensitive name
        needle = project_id.casefold()
        if self._projects_dir.exists():
            for path in self._projects_dir.iterdir():
                state_file = path / "project.json"
                if not state_file.exists():
                    continue
                try:
                    state = self._read_state_file(state_file)
                    if (
                        state.id == project_id
                        or path.name == project_id
                        or path.name.casefold() == needle
                        or str((state.metadata or {}).get("slug") or "").casefold()
                        == needle
                        or str(state.name or "").casefold() == needle
                    ):
                        return state
                except ProjectError:
                    continue

        # 3) Legacy flat file
        flat = self._project_file(project_id)
        if flat.exists():
            cfg = self._project_from_file(flat)
            return self._state_from_config(cfg)

        raise ProjectNotFoundError(
            f"Project not found: {project_id}",
            project_id=project_id,
        )

    def _read_state_file(self, path: Path) -> ProjectState:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(
                f"Corrupt project file: {path}",
                detail=str(exc),
            ) from exc

        if not isinstance(data, dict) or "id" not in data:
            raise ProjectError(f"Invalid project.json: {path}")

        # Ensure phases exist (and add any new pipeline phases like "fix")
        if "phases" not in data or not data["phases"]:
            data["phases"] = {
                p.value: PhaseRecord(name=p.value).model_dump()
                for p in _PIPELINE
            }
        else:
            for p in _PIPELINE:
                if p.value not in data["phases"]:
                    data["phases"][p.value] = PhaseRecord(name=p.value).model_dump()
        return ProjectState(**data)

    def _project_from_file(self, path: Path) -> ProjectConfig:
        """Deserialise a legacy ProjectConfig from *path*."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ProjectConfig(**data)
        except (OSError, json.JSONDecodeError, Exception) as exc:
            raise ProjectError(
                f"Corrupt project file: {path}",
                detail=str(exc),
            ) from exc

    def _state_from_config(self, cfg: ProjectConfig) -> ProjectState:
        """Build a ProjectState from a shared ProjectConfig."""
        meta = dict(cfg.metadata or {})
        slug = meta.get("slug") or self._slugify(cfg.name) or cfg.id[:8]
        project_dir = Path(meta.get("project_dir") or (self._projects_dir / slug))
        now = _utc_now()
        return ProjectState(
            id               = cfg.id,
            name             = cfg.name,
            target_url       = cfg.target_url,
            competitor_urls  = list(meta.get("competitor_urls") or []),
            status           = cfg.status.value if isinstance(cfg.status, ProjectStatus) else str(cfg.status),
            created_at       = cfg.created_at.isoformat() if cfg.created_at else now,
            updated_at       = cfg.updated_at.isoformat() if cfg.updated_at else now,
            last_modified    = now,
            project_dir      = str(project_dir),
            data_dir         = str(meta.get("data_dir") or project_dir),
            output_dir       = str(cfg.output_dir),
            completed_phases = list(meta.get("completed_phases") or []),
            pending_phases   = list(meta.get("pending_phases") or [p.value for p in _PIPELINE]),
            phases           = {
                p.value: PhaseRecord(name=p.value)
                for p in _PIPELINE
            },
            notes            = cfg.notes,
            metadata         = meta,
        )

    def _merge_config_into_state(
        self,
        state: ProjectState,
        cfg:   ProjectConfig,
    ) -> None:
        state.name       = cfg.name or state.name
        state.target_url = cfg.target_url or state.target_url
        state.status     = (
            cfg.status.value if isinstance(cfg.status, ProjectStatus) else str(cfg.status)
        )
        state.notes      = cfg.notes
        if cfg.metadata:
            state.metadata.update(cfg.metadata)
            if "competitor_urls" in cfg.metadata:
                state.competitor_urls = list(cfg.metadata["competitor_urls"] or [])

    def _find_by_url(self, url: str) -> ProjectState | None:
        """Return an existing project with the same target URL, if any."""
        norm = self._normalise_url(url)
        for cfg in self.list_projects():
            if self._normalise_url(cfg.target_url) == norm:
                try:
                    return self._load_state(cfg.id)
                except ProjectNotFoundError:
                    continue
        return None

    def _append_project_log(self, state: ProjectState, message: str) -> None:
        """Append a line to the project's log file."""
        try:
            log_dir = Path(state.project_dir) / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            line = f"{_utc_now()} | {message}\n"
            with (log_dir / "project.log").open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            log.warning("Could not write project log: {e}", e=exc)

    # ── Internal: helpers ──────────────────────────────────────────────────────

    def _project_file(self, project_id: str) -> Path:
        """Return the legacy flat JSON file path for *project_id*."""
        return self._projects_dir / f"{project_id}.json"

    def _generate_id(self) -> str:
        """Generate a new unique project ID."""
        return str(uuid.uuid4())

    def _ensure_project_dirs(self, project_dir: Path) -> None:
        """Create the standard subdirectory tree for a project."""
        project_dir.mkdir(parents=True, exist_ok=True)
        for sub in _PROJECT_SUBDIRS:
            (project_dir / sub).mkdir(parents=True, exist_ok=True)

    def _resolve_crawl_dir(self, url: str) -> Path | None:
        """Return the WebsiteCrawler output directory for *url*."""
        slug = self._domain_slug(url)
        if not slug:
            return None
        # Crawler writes under settings.projects_dir
        return self._settings.projects_dir / slug

    @staticmethod
    def _domain_slug(url: str) -> str:
        """Match WebsiteCrawler folder naming from a URL (lowercased)."""
        try:
            netloc = urlparse(url).netloc.lower()
            folder = re.sub(r"[.:]", "-", netloc.removeprefix("www."))
            folder = re.sub(r"-+", "-", folder).strip("-")
            return folder or ""
        except Exception:
            return ""

    @staticmethod
    def _slugify(text: str) -> str:
        """Folder-safe project name; preserves case (e.g. ``DemoBiz``)."""
        text = text.strip()
        text = re.sub(r'[<>:"/\\|?*]', "", text)
        text = re.sub(r"[\s_]+", "-", text)
        return re.sub(r"-+", "-", text).strip("-")[:80]

    @staticmethod
    def _normalise_url(url: str) -> str:
        if not url or not isinstance(url, str):
            return ""
        url = url.strip()
        try:
            from urllib.parse import urldefrag, urlunparse
            url, _ = urldefrag(url)
            p = urlparse(url)
            if p.scheme not in ("http", "https"):
                return ""
            path = p.path if p.path else "/"
            if path != "/" and path.endswith("/"):
                path = path.rstrip("/")
            return urlunparse((
                p.scheme.lower(), p.netloc.lower(), path, p.params, p.query, "",
            ))
        except Exception:
            return ""
