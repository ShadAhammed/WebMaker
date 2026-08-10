"""
webmaker.plugins.base
=====================
Common plugin interface.
"""

from __future__ import annotations

from typing import Any


class Plugin:
    """Base class for optional WebMaker plugins.

    Override any hook you need.  All hooks are optional; unimplemented
    hooks are no-ops.  Exceptions raised inside hooks are logged and
    swallowed so plugins cannot break the core pipeline.
    """

    name: str = "unnamed"
    enabled: bool = True
    priority: int = 100   # lower runs first

    # ── Pipeline hooks ─────────────────────────────────────────────────────────

    def before_phase(
        self,
        phase: str,
        project_state: Any,
        context: dict[str, Any],
    ) -> None:
        """Called before a ProjectManager pipeline phase starts."""

    def after_phase(
        self,
        phase: str,
        project_state: Any,
        context: dict[str, Any],
        *,
        success: bool,
    ) -> None:
        """Called after a pipeline phase finishes (success or failure)."""

    # ── Job hooks ──────────────────────────────────────────────────────────────

    def before_job(
        self,
        job: Any,
        context: dict[str, Any],
    ) -> None:
        """Called before a JobManager job starts executing."""

    def after_job(
        self,
        job: Any,
        result: Any,
        context: dict[str, Any],
    ) -> None:
        """Called after a JobManager job finishes."""

    def __repr__(self) -> str:
        return f"<Plugin {self.name} enabled={self.enabled}>"
