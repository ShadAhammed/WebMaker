"""
Local FAQ inquiry assistant embed (site-wide).

No external AI / APIs — static JS + faq.json only.
Floating trigger uses a looping muted video avatar.
"""

from __future__ import annotations

import shutil
from html import escape
from pathlib import Path

from webmaker.agents.website_modernizer.image_bank import publish_local_for_wp
from webmaker.utils.project_paths import find_project_path, project_path

_ASSISTANT_SRC = find_project_path("assistant") or project_path("assistant")
_CHAT_DIR = find_project_path("images", "Chat") or project_path("images", "Chat")
_ICON_SRC = _CHAT_DIR / "icon.png"
_POSTER_SRC = _CHAT_DIR / "chat-icon-poster.jpg"
_VIDEO_SRC = _CHAT_DIR / "chat-icon-loop.mp4"


def _copy_if_newer(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if (
        not dest.exists()
        or dest.stat().st_size != src.stat().st_size
        or dest.stat().st_mtime < src.stat().st_mtime
    ):
        shutil.copy2(src, dest)


def publish_assistant_assets() -> dict[str, str]:
    """Copy assistant files + chat media into WP uploads. Returns public URLs."""
    urls: dict[str, str] = {}

    try:
        from webmaker.config.settings import settings
    except Exception:
        return urls

    dest_root = (
        Path(settings.wordpress_dir) / "wp-content" / "uploads" / "webmaker" / "assistant"
    )
    if _ASSISTANT_SRC.is_dir():
        dest_root.mkdir(parents=True, exist_ok=True)
        for src in _ASSISTANT_SRC.rglob("*"):
            if not src.is_file():
                continue
            if src.suffix.lower() not in {".js", ".css", ".json"}:
                continue
            rel = src.relative_to(_ASSISTANT_SRC)
            _copy_if_newer(src, dest_root / rel)

    base = f"{settings.wordpress_url.rstrip('/')}/wp-content/uploads/webmaker/assistant"
    urls["base"] = base
    urls["main"] = f"{base}/main.js"
    urls["css"] = f"{base}/styles/assistant.css"
    urls["faq"] = f"{base}/data/faq.json"

    # Prefer video poster for header avatar; fall back to static icon.png
    poster_src = _POSTER_SRC if _POSTER_SRC.is_file() else _ICON_SRC
    if poster_src.is_file():
        publish_local_for_wp(poster_src)
        icon_name = "chat-icon-poster.jpg" if poster_src == _POSTER_SRC else "icon.png"
        _copy_if_newer(poster_src, dest_root / icon_name)
        urls["icon"] = f"{base}/{icon_name}"
    else:
        urls["icon"] = ""

    if _VIDEO_SRC.is_file():
        publish_local_for_wp(_VIDEO_SRC)
        _copy_if_newer(_VIDEO_SRC, dest_root / "chat-icon-loop.mp4")
        urls["video"] = f"{base}/chat-icon-loop.mp4"
    else:
        urls["video"] = ""

    return urls


def inquiry_assistant_html() -> str:
    """Return wp:html block that mounts the site-wide inquiry assistant."""
    urls = publish_assistant_assets()
    if not urls.get("main"):
        return ""

    base = escape(urls["base"], quote=True)
    main = escape(urls["main"], quote=True)
    css = escape(urls.get("css") or "", quote=True)
    faq = escape(urls.get("faq") or "", quote=True)
    icon = escape(urls.get("icon") or "", quote=True)
    video = escape(urls.get("video") or "", quote=True)

    html = f"""<div id="fia-boot"
  data-base-url="{base}/"
  data-css-url="{css}"
  data-faq-url="{faq}"
  data-icon-url="{icon}"
  data-video-url="{video}"
  hidden></div>
<script type="module">
  import {{ mountAssistant }} from "{main}?v=fia2";
  const boot = document.getElementById("fia-boot");
  if (boot) {{
    mountAssistant({{
      baseUrl: boot.dataset.baseUrl || "",
      cssUrl: boot.dataset.cssUrl || "",
      faqUrl: boot.dataset.faqUrl || "",
      iconUrl: boot.dataset.iconUrl || "",
      videoUrl: boot.dataset.videoUrl || "",
    }});
  }}
</script>"""
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"
