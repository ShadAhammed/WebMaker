"""
webmaker.agents.live_demo_renderer.live_preview
===============================================
Module 5.3 — LivePreview.

Refreshes the operator's view of the live demo after a render: opens/refreshes
the browser and notifies the UI via an optional callback. Screenshot capture and
auto-refresh-on-tick are planned follow-ups (see TODO).
"""

from __future__ import annotations

import webbrowser
from typing import Callable

from webmaker.core.logging import get_logger

log = get_logger("renderer.preview")


def refresh_preview(
    wp_url: str,
    *,
    open_browser: bool = True,
    notify: Callable[[str], None] | None = None,
) -> None:
    """Refresh the live preview after a render pass.

    Args:
        wp_url:       The local WordPress demo URL.
        open_browser: If True, open/refresh the system browser at *wp_url*.
        notify:       Optional UI callback invoked with a status message.
    """
    if open_browser and wp_url:
        try:
            webbrowser.open(wp_url)
        except Exception as exc:  # noqa: BLE001 — preview is best-effort
            log.warning("Could not open browser for preview: {e}", e=exc)

    # TODO(live-preview): capture a fresh screenshot and push it to the UI, and
    # trigger this automatically whenever a recommendation checkbox is ticked.

    if notify is not None:
        try:
            notify(f"Preview refreshed: {wp_url}")
        except Exception:  # noqa: BLE001
            pass
