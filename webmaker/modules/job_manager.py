"""
webmaker.modules.job_manager
============================
Lightweight job system for running individual WebMaker tasks without
executing the full pipeline.

Jobs are queued, executed, cancelled, retried, and resumed independently.
``ProjectManager`` owns a :class:`JobManager` instance and uses it for
granular work; the existing pipeline API is unchanged.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from webmaker.core.logging import get_logger
from webmaker.core.progress import ProgressManager, progress_manager as default_progress
from webmaker.plugins.registry import PluginRegistry, plugin_registry as default_plugins

log = get_logger("job_manager")


class JobStatus(str, Enum):
    """Lifecycle status of a job."""

    PENDING    = "pending"
    QUEUED     = "queued"
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"
    CANCELLED  = "cancelled"
    RETRYING   = "retrying"


class JobType(str, Enum):
    """Supported discrete job types."""

    GENERATE_HOMEPAGE      = "generate_homepage"
    GENERATE_FAQ           = "generate_faq"
    GENERATE_SERVICE_PAGE  = "generate_service_page"
    GENERATE_ABOUT         = "generate_about"
    GENERATE_CONTACT       = "generate_contact"
    REBUILD_WORDPRESS      = "rebuild_wordpress"
    RUN_QA                 = "run_qa"
    RERUN_COMPETITORS      = "rerun_competitors"
    RUN_PHASE              = "run_phase"          # generic: params.phase
    CUSTOM                 = "custom"


class Job(BaseModel):
    """Persisted job descriptor."""

    id:             str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    project_id:     str = ""
    job_type:       str = JobType.CUSTOM.value
    status:         str = JobStatus.PENDING.value
    progress:       float = Field(default=0.0, ge=0.0, le=100.0)
    created_at:     str = ""
    started_at:     str = ""
    completed_at:   str = ""
    updated_at:     str = ""
    params:         dict[str, Any] = Field(default_factory=dict)
    execution_log:  list[str] = Field(default_factory=list)
    error:          str = ""
    result_data:    dict[str, Any] = Field(default_factory=dict)
    artifacts:      list[str] = Field(default_factory=list)
    attempts:       int = 0
    max_retries:    int = 1
    cancelled:      bool = False


class JobResult(BaseModel):
    """Outcome of a job execution attempt."""

    job_id:      str = ""
    success:     bool = False
    status:      str = JobStatus.FAILED.value
    message:     str = ""
    artifacts:   list[str] = Field(default_factory=list)
    data:        dict[str, Any] = Field(default_factory=dict)
    duration_s:  float = 0.0
    error:       str = ""


# Handler signature: (job) -> JobResult | dict | list[str] | None
JobHandler = Callable[[Job], Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    """Queue and execute discrete WebMaker jobs.

    Args:
        storage_dir:     Directory for job JSON persistence (optional).
        progress:        ProgressManager for event emission.
        plugins:         PluginRegistry for before/after_job hooks.
        max_log_lines:   Cap on execution_log length per job.
    """

    def __init__(
        self,
        storage_dir: Path | None = None,
        *,
        progress: ProgressManager | None = None,
        plugins: PluginRegistry | None = None,
        max_log_lines: int = 500,
    ) -> None:
        self._jobs: dict[str, Job] = {}
        self._queue: list[str] = []
        self._handlers: dict[str, JobHandler] = {}
        self._storage_dir = Path(storage_dir) if storage_dir else None
        self._progress = progress or default_progress
        self._plugins = plugins or default_plugins
        self._max_log_lines = max_log_lines
        self._running_id: str | None = None

        if self._storage_dir:
            self._storage_dir.mkdir(parents=True, exist_ok=True)

        log.info("JobManager ready (storage={d})", d=self._storage_dir or "memory")

    # ── Handler registration ───────────────────────────────────────────────────

    def register_handler(self, job_type: str | JobType, handler: JobHandler) -> None:
        """Register an execution handler for *job_type*."""
        key = job_type.value if isinstance(job_type, JobType) else str(job_type)
        self._handlers[key] = handler
        log.debug("Job handler registered: {t}", t=key)

    def register_handlers(self, mapping: dict[str, JobHandler]) -> None:
        """Register multiple handlers at once."""
        for key, handler in mapping.items():
            self.register_handler(key, handler)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def create_job(
        self,
        job_type: str | JobType,
        project_id: str,
        *,
        params: dict[str, Any] | None = None,
        max_retries: int = 1,
        enqueue: bool = True,
    ) -> Job:
        """Create a job (optionally enqueue it)."""
        jt = job_type.value if isinstance(job_type, JobType) else str(job_type)
        now = _utc_now()
        job = Job(
            project_id=project_id,
            job_type=jt,
            status=JobStatus.QUEUED.value if enqueue else JobStatus.PENDING.value,
            created_at=now,
            updated_at=now,
            params=dict(params or {}),
            max_retries=max_retries,
        )
        self._jobs[job.id] = job
        if enqueue:
            self._queue.append(job.id)
        self._log(job, f"Job created: type={jt}")
        self._persist(job)
        log.info("Job created id={i} type={t} project={p}", i=job.id, t=jt, p=project_id)
        return job

    def enqueue(self, job_id: str) -> Job:
        """Move an existing job onto the queue."""
        job = self.get_job(job_id)
        if job.id not in self._queue:
            self._queue.append(job.id)
        job.status = JobStatus.QUEUED.value
        job.cancelled = False
        job.updated_at = _utc_now()
        self._log(job, "Enqueued")
        self._persist(job)
        return job

    def get_job(self, job_id: str) -> Job:
        """Return a job by id.

        Raises:
            KeyError: If unknown.
        """
        if job_id not in self._jobs:
            # Try disk
            loaded = self._load_from_disk(job_id)
            if loaded is None:
                raise KeyError(f"Unknown job: {job_id}")
            self._jobs[job_id] = loaded
        return self._jobs[job_id]

    def list_jobs(
        self,
        *,
        project_id: str | None = None,
        status: str | JobStatus | None = None,
    ) -> list[Job]:
        """List jobs, optionally filtered."""
        status_val = status.value if isinstance(status, JobStatus) else status
        out: list[Job] = []
        for job in self._jobs.values():
            if project_id and job.project_id != project_id:
                continue
            if status_val and job.status != status_val:
                continue
            out.append(job)
        out.sort(key=lambda j: j.created_at, reverse=True)
        return out

    def cancel(self, job_id: str) -> Job:
        """Cancel a pending/queued/running job."""
        job = self.get_job(job_id)
        if job.status in (
            JobStatus.COMPLETED.value,
            JobStatus.CANCELLED.value,
        ):
            return job
        job.cancelled = True
        if job.status != JobStatus.RUNNING.value:
            job.status = JobStatus.CANCELLED.value
            job.completed_at = _utc_now()
            job.progress = job.progress  # keep
            if job.id in self._queue:
                self._queue.remove(job.id)
        job.updated_at = _utc_now()
        self._log(job, "Cancellation requested")
        self._persist(job)
        self._progress.emit(
            job.progress,
            f"Job cancelled: {job.job_type}",
            phase=f"job:{job.job_type}",
            project_id=job.project_id,
            job_id=job.id,
            status="cancelled",
            module="job_manager",
        )
        return job

    def retry(self, job_id: str, *, enqueue: bool = True) -> Job:
        """Reset a failed/cancelled job for another attempt."""
        job = self.get_job(job_id)
        job.error = ""
        job.cancelled = False
        job.status = JobStatus.QUEUED.value if enqueue else JobStatus.PENDING.value
        job.completed_at = ""
        job.progress = 0.0
        job.updated_at = _utc_now()
        self._log(job, "Retry requested")
        if enqueue and job.id not in self._queue:
            self._queue.append(job.id)
        self._persist(job)
        return job

    def resume(self, job_id: str) -> JobResult:
        """Resume a failed/interrupted job (retry + execute immediately)."""
        job = self.retry(job_id, enqueue=False)
        job.status = JobStatus.QUEUED.value
        return self.execute(job.id)

    def execute(self, job_id: str) -> JobResult:
        """Execute a single job immediately (does not require queue order)."""
        job = self.get_job(job_id)
        if job.cancelled or job.status == JobStatus.CANCELLED.value:
            return JobResult(
                job_id=job.id,
                success=False,
                status=JobStatus.CANCELLED.value,
                message="Job was cancelled",
                error="cancelled",
            )

        handler = self._handlers.get(job.job_type)
        if handler is None:
            # Allow RUN_PHASE generic via params
            if job.job_type == JobType.RUN_PHASE.value:
                handler = self._handlers.get(JobType.RUN_PHASE.value)
            if handler is None:
                result = JobResult(
                    job_id=job.id,
                    success=False,
                    status=JobStatus.FAILED.value,
                    message=f"No handler for job type: {job.job_type}",
                    error=f"No handler for job type: {job.job_type}",
                )
                self._finish_failed(job, result.error)
                return result

        if job.id in self._queue:
            self._queue.remove(job.id)

        job.status = JobStatus.RUNNING.value
        job.started_at = job.started_at or _utc_now()
        job.updated_at = _utc_now()
        job.attempts += 1
        job.progress = 5.0
        self._running_id = job.id
        self._log(job, f"Execution started (attempt {job.attempts})")
        self._persist(job)

        self._progress.emit(
            5.0,
            f"Running job: {job.job_type}",
            phase=f"job:{job.job_type}",
            project_id=job.project_id,
            job_id=job.id,
            module="job_manager",
        )
        self._plugins.call_before_job(job, {"project_id": job.project_id})

        t0 = time.perf_counter()
        try:
            if job.cancelled:
                raise RuntimeError("Job cancelled before handler")

            raw = handler(job)
            result = self._normalise_result(job, raw, time.perf_counter() - t0)

            if job.cancelled:
                job.status = JobStatus.CANCELLED.value
                job.completed_at = _utc_now()
                result.success = False
                result.status = JobStatus.CANCELLED.value
                result.message = "Job cancelled during execution"
            elif result.success:
                job.status = JobStatus.COMPLETED.value
                job.progress = 100.0
                job.completed_at = _utc_now()
                job.result_data = dict(result.data)
                job.artifacts = list(result.artifacts)
                job.error = ""
                self._log(job, f"Completed successfully ({result.duration_s:.2f}s)")
            else:
                self._finish_failed(job, result.error or result.message)
                self._plugins.call_after_job(job, result, {"project_id": job.project_id})
                self._running_id = None
                self._persist(job)
                return result

            job.updated_at = _utc_now()
            self._persist(job)
            self._progress.emit(
                job.progress,
                result.message or f"Job finished: {job.job_type}",
                phase=f"job:{job.job_type}",
                project_id=job.project_id,
                job_id=job.id,
                status=job.status,
                module="job_manager",
            )
            self._plugins.call_after_job(job, result, {"project_id": job.project_id})
            self._running_id = None
            return result

        except Exception as exc:
            elapsed = time.perf_counter() - t0
            self._finish_failed(job, str(exc))
            result = JobResult(
                job_id=job.id,
                success=False,
                status=JobStatus.FAILED.value,
                message=str(exc),
                error=str(exc),
                duration_s=round(elapsed, 3),
            )
            self._plugins.call_after_job(job, result, {"project_id": job.project_id})
            self._running_id = None
            # Auto-retry if attempts remain
            if job.attempts <= job.max_retries and not job.cancelled:
                self._log(job, f"Scheduling retry ({job.attempts}/{job.max_retries})")
                job.status = JobStatus.RETRYING.value
                self._persist(job)
                self.retry(job.id, enqueue=False)
                return self.execute(job.id)
            return result

    def process_queue(self, *, max_jobs: int | None = None) -> list[JobResult]:
        """Execute queued jobs in FIFO order."""
        results: list[JobResult] = []
        count = 0
        while self._queue:
            if max_jobs is not None and count >= max_jobs:
                break
            job_id = self._queue[0]
            results.append(self.execute(job_id))
            count += 1
        return results

    def update_progress(self, job_id: str, percent: float, message: str = "") -> None:
        """Update job progress and emit a ProgressEvent."""
        job = self.get_job(job_id)
        job.progress = max(0.0, min(100.0, float(percent)))
        job.updated_at = _utc_now()
        if message:
            self._log(job, message)
        self._persist(job)
        self._progress.emit(
            job.progress,
            message or f"{job.job_type} {job.progress:.0f}%",
            phase=f"job:{job.job_type}",
            project_id=job.project_id,
            job_id=job.id,
            module="job_manager",
        )

    # ── Internals ──────────────────────────────────────────────────────────────

    def _normalise_result(self, job: Job, raw: Any, elapsed: float) -> JobResult:
        if isinstance(raw, JobResult):
            raw.job_id = raw.job_id or job.id
            raw.duration_s = raw.duration_s or round(elapsed, 3)
            return raw
        if isinstance(raw, dict):
            return JobResult(
                job_id=job.id,
                success=bool(raw.get("success", True)),
                status=JobStatus.COMPLETED.value if raw.get("success", True) else JobStatus.FAILED.value,
                message=str(raw.get("message", "")),
                artifacts=list(raw.get("artifacts") or []),
                data={k: v for k, v in raw.items() if k not in ("success", "message", "artifacts", "error")},
                duration_s=round(elapsed, 3),
                error=str(raw.get("error", "")),
            )
        if isinstance(raw, list):
            return JobResult(
                job_id=job.id,
                success=True,
                status=JobStatus.COMPLETED.value,
                artifacts=[str(x) for x in raw],
                duration_s=round(elapsed, 3),
                message="OK",
            )
        return JobResult(
            job_id=job.id,
            success=True,
            status=JobStatus.COMPLETED.value,
            message=str(raw or "OK"),
            duration_s=round(elapsed, 3),
        )

    def _finish_failed(self, job: Job, error: str) -> None:
        job.status = JobStatus.FAILED.value
        job.error = error
        job.completed_at = _utc_now()
        job.updated_at = job.completed_at
        self._log(job, f"Failed: {error}")
        self._persist(job)
        self._progress.emit(
            job.progress,
            f"Job failed: {job.job_type}",
            phase=f"job:{job.job_type}",
            project_id=job.project_id,
            job_id=job.id,
            status="failed",
            module="job_manager",
            details={"error": error},
        )

    def _log(self, job: Job, message: str) -> None:
        line = f"[{_utc_now()}] {message}"
        job.execution_log.append(line)
        if len(job.execution_log) > self._max_log_lines:
            job.execution_log = job.execution_log[-self._max_log_lines :]

    def _persist(self, job: Job) -> None:
        if not self._storage_dir:
            return
        path = self._storage_dir / f"{job.id}.json"
        try:
            path.write_text(
                job.model_dump_json(indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Failed to persist job {i}: {e}", i=job.id, e=exc)

    def _load_from_disk(self, job_id: str) -> Job | None:
        if not self._storage_dir:
            return None
        path = self._storage_dir / f"{job_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Job(**data)
        except Exception as exc:
            log.warning("Corrupt job file {p}: {e}", p=path, e=exc)
            return None
