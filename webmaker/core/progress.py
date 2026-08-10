"""
webmaker.core.progress
======================
Centralised progress reporting for CLI / future Streamlit UI.

Modules publish :class:`ProgressEvent` instances via :class:`ProgressManager`.
``ProjectManager`` aggregates pipeline-level percentages.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable

from pydantic import BaseModel, Field

from webmaker.core.logging import get_logger

log = get_logger("progress")


class ProgressEvent(BaseModel):
    """A single progress update emitted by a module or the orchestrator."""

    percent:     float = Field(ge=0.0, le=100.0, default=0.0)
    message:     str   = ""
    phase:       str   = ""          # crawl | analyze | … | job:<type>
    project_id:  str   = ""
    job_id:      str   = ""
    module:      str   = ""
    status:      str   = "running"   # running | completed | failed | cancelled
    details:     dict[str, Any] = Field(default_factory=dict)
    timestamp:   str   = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# Listener callback type
ProgressListener = Callable[[ProgressEvent], None]


class ProgressManager:
    """Thread-safe progress bus with optional listeners.

    Usage::

        progress = ProgressManager()
        progress.subscribe(print)
        progress.emit(10, "Crawling website", phase="crawl")
    """

    # Suggested pipeline milestones (ProjectManager may refine)
    PIPELINE_MILESTONES: dict[str, float] = {
        "create":   0.0,
        "crawl":    10.0,
        "analyze":  35.0,
        "compete":  50.0,
        "optimize": 60.0,
        "generate": 80.0,
        "review":   92.0,
        "fix":      97.0,
        "complete": 100.0,
    }

    def __init__(self) -> None:
        self._listeners: list[ProgressListener] = []
        self._history:   list[ProgressEvent] = []
        self._latest:    ProgressEvent | None = None
        self._lock = Lock()

    def subscribe(self, listener: ProgressListener) -> None:
        """Register a listener called for every emitted event."""
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def unsubscribe(self, listener: ProgressListener) -> None:
        """Remove a previously registered listener."""
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def clear_history(self) -> None:
        """Drop stored events (listeners stay registered)."""
        with self._lock:
            self._history.clear()
            self._latest = None

    @property
    def latest(self) -> ProgressEvent | None:
        """Most recent event, or None."""
        return self._latest

    @property
    def history(self) -> list[ProgressEvent]:
        """Copy of emitted events (oldest first)."""
        with self._lock:
            return list(self._history)

    def emit(
        self,
        percent: float,
        message: str,
        *,
        phase:      str = "",
        project_id: str = "",
        job_id:     str = "",
        module:     str = "",
        status:     str = "running",
        details:    dict[str, Any] | None = None,
    ) -> ProgressEvent:
        """Publish a progress event to all listeners and history."""
        event = ProgressEvent(
            percent=max(0.0, min(100.0, float(percent))),
            message=message,
            phase=phase,
            project_id=project_id,
            job_id=job_id,
            module=module,
            status=status,
            details=dict(details or {}),
        )
        with self._lock:
            self._latest = event
            self._history.append(event)
            listeners = list(self._listeners)

        log.debug(
            "Progress {p:.0f}% [{phase}] {m}",
            p=event.percent, phase=event.phase or "-", m=event.message,
        )
        for listener in listeners:
            try:
                listener(event)
            except Exception as exc:
                log.warning("Progress listener failed: {e}", e=exc)
        return event

    def emit_phase(
        self,
        phase: str,
        message: str,
        *,
        project_id: str = "",
        status: str = "running",
        within_phase: float = 0.0,
    ) -> ProgressEvent:
        """Emit using pipeline milestone percentages.

        ``within_phase`` (0–1) interpolates toward the next milestone.
        """
        base = self.PIPELINE_MILESTONES.get(phase, 0.0)
        # Find next milestone value
        ordered = list(self.PIPELINE_MILESTONES.items())
        next_pct = 100.0
        for i, (name, pct) in enumerate(ordered):
            if name == phase and i + 1 < len(ordered):
                next_pct = ordered[i + 1][1]
                break
        span = max(0.0, next_pct - base)
        percent = base + span * max(0.0, min(1.0, within_phase))
        return self.emit(
            percent,
            message,
            phase=phase,
            project_id=project_id,
            status=status,
            module="project_manager",
        )


# Process-wide default bus (modules may use a ProjectManager-owned instance)
progress_manager = ProgressManager()
