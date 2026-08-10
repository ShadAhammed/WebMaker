"""
webmaker.agents.base
====================
The agent contract shared by every WebMaker V2 agent.

``BaseAgent`` is generic over its input and output artifact types. Concrete
agents implement :meth:`_run`, and callers invoke :meth:`run`, which validates
both the input and the produced output against the declared Pydantic models.

Agents never call each other; the Orchestrator owns execution order and IO.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from webmaker.core.exceptions import WebMakerError
from webmaker.core.logging import get_logger

log = get_logger("agent")

TIn = TypeVar("TIn", bound=BaseModel)
TOut = TypeVar("TOut", bound=BaseModel)


class AgentError(WebMakerError):
    """Raised when an agent fails or produces a schema-invalid artifact."""


@dataclass
class AgentContext:
    """Execution context passed to every agent.

    Attributes:
        project_slug: Project folder slug (identity for artifacts).
        data_dir:     Directory holding the project's ``json/`` outputs.
        settings:     Application settings singleton.
        extras:       Optional free-form knobs (e.g. page_slugs, regenerate).
                      Values here are runtime hints, never cross-agent artifacts.
    """

    project_slug: str
    data_dir: Path
    settings: object
    extras: dict[str, object] = field(default_factory=dict)


class BaseAgent(ABC, Generic[TIn, TOut]):
    """Abstract single-responsibility agent.

    Subclasses set :attr:`name`, :attr:`input_model`, :attr:`output_model`
    and implement :meth:`_run`. Use :meth:`run` to execute with validation.
    """

    name: str = "agent"
    input_model: type[TIn]
    output_model: type[TOut]

    def __init__(self, context: AgentContext) -> None:
        self._ctx = context

    @property
    def context(self) -> AgentContext:
        """The execution context for this agent instance."""
        return self._ctx

    # ── Public entry ────────────────────────────────────────────────────────

    def run(self, data: TIn) -> TOut:
        """Validate input, execute the agent, validate and stamp the output.

        Args:
            data: The typed input artifact.

        Returns:
            The typed, validated output artifact with provenance meta stamped.

        Raises:
            AgentError: If input/output validation fails or the agent errors.
        """
        self._validate_input(data)
        log.info("Agent {n} started (project={p})", n=self.name, p=self._ctx.project_slug)
        try:
            output = self._run(data)
        except WebMakerError:
            raise
        except Exception as exc:  # noqa: BLE001 — normalise to AgentError
            raise AgentError(
                f"Agent {self.name!r} raised: {exc}",
                agent=self.name,
            ) from exc

        output = self._validate_output(output)
        self._stamp(output)
        log.info("Agent {n} completed", n=self.name)
        return output

    # ── Subclass hook ───────────────────────────────────────────────────────

    @abstractmethod
    def _run(self, data: TIn) -> TOut:
        """Perform the agent's single responsibility. Must not mutate *data*."""

    # ── Validation helpers ──────────────────────────────────────────────────

    def _validate_input(self, data: TIn) -> None:
        expected = getattr(self, "input_model", None)
        if expected is None:
            raise AgentError(f"Agent {self.name!r} declares no input_model", agent=self.name)
        if not isinstance(data, expected):
            raise AgentError(
                f"Agent {self.name!r} expected input {expected.__name__}, "
                f"got {type(data).__name__}",
                agent=self.name,
            )
        try:
            # Re-validate to guarantee the artifact is well-formed.
            expected.model_validate(data.model_dump())
        except ValidationError as exc:
            raise AgentError(
                f"Agent {self.name!r} received invalid input: {exc}",
                agent=self.name,
            ) from exc

    def _validate_output(self, output: TOut) -> TOut:
        expected = getattr(self, "output_model", None)
        if expected is None:
            raise AgentError(f"Agent {self.name!r} declares no output_model", agent=self.name)
        if not isinstance(output, expected):
            raise AgentError(
                f"Agent {self.name!r} returned {type(output).__name__}, "
                f"expected {expected.__name__}",
                agent=self.name,
            )
        try:
            return expected.model_validate(output.model_dump())
        except ValidationError as exc:
            raise AgentError(
                f"Agent {self.name!r} produced invalid output: {exc}",
                agent=self.name,
            ) from exc

    def _stamp(self, output: TOut) -> None:
        """Populate ArtifactMeta on the output if present."""
        meta = getattr(output, "meta", None)
        if meta is None:
            return
        meta.agent = self.name
        meta.project = self._ctx.project_slug
        if not meta.artifact_id:
            meta.artifact_id = f"{self._ctx.project_slug}:{self.name}"
