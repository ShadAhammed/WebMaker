"""
webmaker.plugins.registry
=========================
Plugin registration, discovery, and safe hook dispatch.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from webmaker.core.logging import get_logger
from webmaker.plugins.base import Plugin

log = get_logger("plugins")

_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_PLUGINS_DIR = _ROOT / "plugins"


class PluginRegistry:
    """Ordered registry of optional plugins."""

    def __init__(self) -> None:
        self._plugins: list[Plugin] = []

    def register(self, plugin: Plugin) -> None:
        """Add *plugin* (replacing same name if already registered)."""
        self._plugins = [p for p in self._plugins if p.name != plugin.name]
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: p.priority)
        log.info("Plugin registered: {n}", n=plugin.name)

    def unregister(self, name: str) -> None:
        self._plugins = [p for p in self._plugins if p.name != name]

    def clear(self) -> None:
        self._plugins.clear()

    def list(self) -> list[Plugin]:
        return list(self._plugins)

    def enabled(self) -> list[Plugin]:
        return [p for p in self._plugins if p.enabled]

    def discover(self, plugins_dir: Path | None = None) -> list[Plugin]:
        """Load ``*.py`` plugin modules from *plugins_dir*.

        Each module may expose ``PLUGIN`` (a Plugin instance) or
        ``create_plugin()`` returning a Plugin.
        """
        directory = Path(plugins_dir) if plugins_dir else _DEFAULT_PLUGINS_DIR
        loaded: list[Plugin] = []
        if not directory.is_dir():
            log.debug("No plugins directory at {d}", d=directory)
            return loaded

        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                plugin = self._load_file(path)
                if plugin is not None:
                    self.register(plugin)
                    loaded.append(plugin)
            except Exception as exc:
                log.warning("Failed to load plugin {p}: {e}", p=path.name, e=exc)
        return loaded

    def _load_file(self, path: Path) -> Plugin | None:
        mod_name = f"webmaker_user_plugin_{path.stem}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)

        if hasattr(module, "PLUGIN") and isinstance(module.PLUGIN, Plugin):
            return module.PLUGIN
        if hasattr(module, "create_plugin") and callable(module.create_plugin):
            plugin = module.create_plugin()
            if isinstance(plugin, Plugin):
                return plugin
        return None

    # ── Safe dispatch ──────────────────────────────────────────────────────────

    def call_before_phase(
        self,
        phase: str,
        project_state: Any,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = dict(context or {})
        for plugin in self.enabled():
            try:
                plugin.before_phase(phase, project_state, ctx)
            except Exception as exc:
                log.warning(
                    "Plugin {n} before_phase({p}) failed: {e}",
                    n=plugin.name, p=phase, e=exc,
                )

    def call_after_phase(
        self,
        phase: str,
        project_state: Any,
        *,
        success: bool,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = dict(context or {})
        for plugin in self.enabled():
            try:
                plugin.after_phase(phase, project_state, ctx, success=success)
            except Exception as exc:
                log.warning(
                    "Plugin {n} after_phase({p}) failed: {e}",
                    n=plugin.name, p=phase, e=exc,
                )

    def call_before_job(self, job: Any, context: dict[str, Any] | None = None) -> None:
        ctx = dict(context or {})
        for plugin in self.enabled():
            try:
                plugin.before_job(job, ctx)
            except Exception as exc:
                log.warning(
                    "Plugin {n} before_job failed: {e}",
                    n=plugin.name, e=exc,
                )

    def call_after_job(
        self,
        job: Any,
        result: Any,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = dict(context or {})
        for plugin in self.enabled():
            try:
                plugin.after_job(job, result, ctx)
            except Exception as exc:
                log.warning(
                    "Plugin {n} after_job failed: {e}",
                    n=plugin.name, e=exc,
                )


# Global registry used by ProjectManager / JobManager
plugin_registry = PluginRegistry()
