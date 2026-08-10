"""
webmaker.modules.ai_cache
=========================
Disk-backed AI response cache keyed by SHA-256 of model, provider,
system prompt, user prompt, and context.

Used by :class:`AIRouter` to skip identical provider calls.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from webmaker.core.logging import get_logger

log = get_logger("ai_cache")


class CacheEntry(BaseModel):
    """One cached AI response."""

    key:           str = ""
    provider:      str = ""
    model:         str = ""
    response_text: str = ""
    response:      dict[str, Any] = Field(default_factory=dict)
    created_at:    str = ""
    hits:          int = 0


class AICache:
    """Filesystem AI cache under ``cache/ai/``.

    Args:
        cache_dir: Root cache directory (typically ``settings.cache_dir``).
        enabled:   When False, all lookups miss and stores are no-ops.
        ttl_s:     Optional time-to-live in seconds (None = forever).
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        enabled: bool = True,
        ttl_s: float | None = None,
    ) -> None:
        self._root = Path(cache_dir) / "ai"
        self._enabled = enabled
        self._ttl_s = ttl_s
        if enabled:
            self._root.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        if self._enabled:
            self._root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def make_key(
        *,
        model: str,
        provider: str,
        system: str,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Build SHA-256 cache key from request identity fields."""
        payload = {
            "model":    model or "",
            "provider": provider or "",
            "system":   system or "",
            "prompt":   prompt or "",
            "context":  context or {},
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, key: str) -> CacheEntry | None:
        """Return a cache entry if present and not expired."""
        if not self._enabled or not key:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entry = CacheEntry(**data)
        except (OSError, json.JSONDecodeError, Exception) as exc:
            log.warning("Corrupt AI cache entry {k}: {e}", k=key[:12], e=exc)
            return None

        if self._ttl_s is not None and entry.created_at:
            try:
                created = datetime.fromisoformat(entry.created_at.replace("Z", "+00:00"))
                age = time.time() - created.timestamp()
                if age > self._ttl_s:
                    log.debug("AI cache expired: {k}", k=key[:12])
                    return None
            except ValueError:
                pass

        entry.hits += 1
        try:
            path.write_text(
                entry.model_dump_json(indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

        log.info("AI cache HIT — key={k} provider={p}", k=key[:12], p=entry.provider)
        return entry

    def set(
        self,
        key: str,
        *,
        provider: str,
        model: str,
        response_text: str,
        response: dict[str, Any] | None = None,
    ) -> CacheEntry | None:
        """Store a response under *key*."""
        if not self._enabled or not key:
            return None
        entry = CacheEntry(
            key=key,
            provider=provider,
            model=model,
            response_text=response_text,
            response=dict(response or {}),
            created_at=datetime.now(timezone.utc).isoformat(),
            hits=0,
        )
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(entry.model_dump_json(indent=2), encoding="utf-8")
            log.debug("AI cache STORE — key={k}", k=key[:12])
        except OSError as exc:
            log.warning("Failed to write AI cache: {e}", e=exc)
            return None
        return entry

    def invalidate(self, key: str | None = None) -> int:
        """Delete one entry or the entire AI cache.

        Args:
            key: Specific key to remove; None clears all AI cache files.

        Returns:
            Number of files removed.
        """
        if key:
            path = self._path(key)
            if path.exists():
                path.unlink(missing_ok=True)
                return 1
            return 0

        removed = 0
        if self._root.exists():
            for path in self._root.glob("*.json"):
                path.unlink(missing_ok=True)
                removed += 1
        log.info("AI cache cleared — {n} entries", n=removed)
        return removed

    def _path(self, key: str) -> Path:
        safe = re_sub_safe(key)
        return self._root / f"{safe}.json"


def re_sub_safe(key: str) -> str:
    """Keep only hex-safe characters for filenames."""
    return "".join(c for c in key if c.isalnum())[:64] or "empty"
