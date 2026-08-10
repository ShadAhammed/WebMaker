"""
Stage 2 — Store raw HTML, cleaned HTML, and DOM tree JSON.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from webmaker.core.logging import get_logger

log = get_logger("acquisition.html")


def process_html(data_dir: Path, package_dir: Path) -> list[dict[str, Any]]:
    """For each raw HTML file, write cleaned HTML + DOM summary under package/html."""
    data_dir = Path(data_dir)
    package_dir = Path(package_dir)
    html_out = package_dir / "html"
    html_out.mkdir(parents=True, exist_ok=True)
    dom_out = package_dir / "dom"
    dom_out.mkdir(parents=True, exist_ok=True)

    raw_dir = data_dir / "raw"
    results: list[dict[str, Any]] = []
    if not raw_dir.is_dir():
        log.warning("No raw/ directory — skipping HTML stage")
        return results

    from webmaker.agents.migration_agent.dom_extractor import extract_dom_from_html

    for raw_path in sorted(raw_dir.glob("*.html")):
        slug = raw_path.stem
        try:
            html = raw_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("Cannot read {p}: {e}", p=raw_path, e=exc)
            continue

        cleaned = _clean_html(html)
        cleaned_path = html_out / f"{slug}.html"
        cleaned_path.write_text(cleaned, encoding="utf-8")

        doc = extract_dom_from_html(html, url="")
        dom_payload = {
            "slug": slug,
            "title": doc.title,
            "images": doc.images[:50],
            "headings": doc.headings[:40],
            "links": [{"text": t, "href": h} for t, h in doc.links[:80]],
            "node_count": len(doc.root.depth_first()) if doc.root else 0,
        }
        dom_path = dom_out / f"{slug}.json"
        dom_path.write_text(
            json.dumps(dom_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        results.append({
            "slug": slug,
            "raw_path": str(raw_path.relative_to(data_dir)).replace("\\", "/"),
            "cleaned_path": str(cleaned_path.relative_to(package_dir)).replace("\\", "/"),
            "dom_path": str(dom_path.relative_to(package_dir)).replace("\\", "/"),
            "title": doc.title,
            "node_count": dom_payload["node_count"],
        })
        log.info("HTML processed slug={s} nodes={n}", s=slug, n=dom_payload["node_count"])

    return results


def _clean_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    return str(soup)
