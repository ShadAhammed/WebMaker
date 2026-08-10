"""
Stage 5 — Brand signals from CSS / inline styles (deterministic, best-effort).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from webmaker.core.logging import get_logger

log = get_logger("acquisition.brand")

_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
_RGB_RE = re.compile(r"rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+", re.I)
_FONT_RE = re.compile(r"font-family\s*:\s*([^;}{]+)", re.I)
_RADIUS_RE = re.compile(r"border-radius\s*:\s*([^;}{]+)", re.I)
_PAD_RE = re.compile(r"padding(?:-[\w]+)?\s*:\s*([^;}{]+)", re.I)
_BTN_RE = re.compile(r"\.(?:btn|button|b-btn)[^{]*\{([^}]+)\}", re.I)


def extract_brand(data_dir: Path, package_dir: Path) -> dict[str, Any]:
    """Extract colors, fonts, spacing, button-ish rules from HTML/CSS snippets."""
    data_dir = Path(data_dir)
    package_dir = Path(package_dir)

    blobs: list[str] = []
    raw_dir = data_dir / "raw"
    if raw_dir.is_dir():
        for p in sorted(raw_dir.glob("*.html"))[:12]:
            try:
                blobs.append(p.read_text(encoding="utf-8", errors="replace")[:500_000])
            except OSError:
                pass

    text = "\n".join(blobs)
    hex_colors = Counter(_HEX_RE.findall(text))
    # Drop near-white/black noise less aggressively — keep top hues
    primary = [c for c, _ in hex_colors.most_common(12) if c.lower() not in ("#fff", "#ffffff", "#000", "#000000")]
    secondary = primary[3:8]
    primary = primary[:3]

    fonts = []
    for m in _FONT_RE.finditer(text):
        fam = m.group(1).strip().strip("'\"")
        if fam and fam not in fonts:
            fonts.append(fam)
        if len(fonts) >= 8:
            break

    radii = list(dict.fromkeys(m.group(1).strip() for m in _RADIUS_RE.finditer(text)))[:8]
    paddings = list(dict.fromkeys(m.group(1).strip() for m in _PAD_RE.finditer(text)))[:12]
    button_styles = []
    for m in _BTN_RE.finditer(text):
        button_styles.append(re.sub(r"\s+", " ", m.group(1).strip())[:200])
        if len(button_styles) >= 5:
            break

    payload = {
        "primary_colors": primary,
        "secondary_colors": secondary,
        "all_colors_sample": [{"color": c, "count": n} for c, n in hex_colors.most_common(20)],
        "fonts": fonts,
        "spacing_samples": paddings,
        "border_radius": radii,
        "button_style_samples": button_styles,
        "icon_style": "unknown",
    }
    out = package_dir / "brand.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(
        "Brand extracted — colors={c} fonts={f}",
        c=len(primary) + len(secondary), f=len(fonts),
    )
    return payload
