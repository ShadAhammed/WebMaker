"""
webmaker.orchestrator.store
===========================
Typed, deterministic artifact persistence.

Artifacts are stored one-file-per-artifact under
``projects/<slug>/artifacts/<artifact_name>.json``. Files are written with
sorted keys so diffs are stable. Loading validates against the target model, so
a corrupt or schema-mismatched file raises rather than silently degrading.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from webmaker.core.logging import get_logger
from webmaker.schemas.base import Artifact

log = get_logger("orchestrator.store")

T = TypeVar("T", bound=Artifact)


class ArtifactStore:
    """Load and save :class:`Artifact` instances as JSON files."""

    def __init__(self, artifacts_dir: Path) -> None:
        self._dir = Path(artifacts_dir)

    @property
    def dir(self) -> Path:
        """The directory artifacts are stored in."""
        return self._dir

    def path_for(self, name: str) -> Path:
        """Return the on-disk path for an artifact filename stem."""
        return self._dir / f"{name}.json"

    def _name_of(self, model: type[Artifact]) -> str:
        # artifact_name is a field with a default; read it from a bare instance.
        try:
            return str(model().artifact_name)
        except ValidationError:
            # Fallback: use the class name lowercased.
            return model.__name__.lower()

    def exists(self, model: type[Artifact], name: str | None = None) -> bool:
        """True if the artifact file exists."""
        return self.path_for(name or self._name_of(model)).is_file()

    def save(self, artifact: Artifact, name: str | None = None) -> Path:
        """Persist *artifact* deterministically and return its path."""
        self._dir.mkdir(parents=True, exist_ok=True)
        stem = name or str(getattr(artifact, "artifact_name", "") or self._name_of(type(artifact)))
        path = self.path_for(stem)
        payload = artifact.model_dump(mode="json")
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        log.info("Saved artifact -> {p}", p=path)
        return path

    def load(self, model: type[T], name: str | None = None) -> T | None:
        """Load and validate an artifact, or return None if the file is absent.

        Raises:
            ValidationError: If the file exists but does not match *model*.
        """
        path = self.path_for(name or self._name_of(model))
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return model.model_validate(data)
