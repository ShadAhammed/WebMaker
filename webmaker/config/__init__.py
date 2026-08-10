"""
webmaker.config
===============
Configuration package. Exposes the singleton ``settings`` object and the
``Settings`` class for type annotations.
"""

from webmaker.config.settings import Settings, settings

__all__ = ["Settings", "settings"]
