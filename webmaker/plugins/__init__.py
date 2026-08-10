"""
webmaker.plugins
================
Lightweight plugin architecture for WebMaker.

Plugins may hook into pipeline phases and jobs without modifying core modules.

Example::

    from webmaker.plugins import Plugin, register_plugin

    class SeoPlugin(Plugin):
        name = "seo"

        def after_phase(self, phase, project_state, context):
            ...

    register_plugin(SeoPlugin())
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from webmaker.core.logging import get_logger
from webmaker.plugins.base import Plugin
from webmaker.plugins.registry import PluginRegistry, plugin_registry

if TYPE_CHECKING:
    pass

log = get_logger("plugins")

__all__ = [
    "Plugin",
    "PluginRegistry",
    "plugin_registry",
    "register_plugin",
    "load_plugins",
]


def register_plugin(plugin: Plugin) -> None:
    """Register *plugin* on the global registry."""
    plugin_registry.register(plugin)


def load_plugins(plugins_dir: Any = None) -> list[Plugin]:
    """Discover and register plugins from ``plugins/`` (optional).

    Safe no-op when the directory is missing or empty.  The core pipeline
    works with zero plugins installed.
    """
    return plugin_registry.discover(plugins_dir)
