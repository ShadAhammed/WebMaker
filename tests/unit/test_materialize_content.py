"""Tests for surgical OP-Content materialize (must not destroy pages)."""

from __future__ import annotations

import json
from pathlib import Path

from webmaker.agents.live_demo_renderer.materialize_content import (
    _apply_tips,
    has_render_backup,
    materialize_optimized_pages,
    restore_last_render_backup,
)
from webmaker.schemas.render import RenderRequest
from webmaker.schemas.review import Recommendation


def _rec(**kwargs) -> Recommendation:
    defaults = dict(
        id="abc123",
        page_slug="homepage",
        section="Überschrift",
        current="",
        issue="fehlt",
        recommendation="Tipp",
        reason="warum",
        source="ux",
        priority="high",
        selected=True,
        proposed_html="",
    )
    defaults.update(kwargs)
    return Recommendation(**defaults)


def test_sanitize_hero_title_strips_glued_cta():
    from webmaker.agents.live_demo_renderer.materialize_content import (
        _sanitize_hero_title,
    )
    assert "Jetzt" not in _sanitize_hero_title(
        "Entrümpelung in Siegen – zum Festpreis, ohne versteckte KostenJetzt kostenlos anfragen"
    )
    assert "Festpreis" in _sanitize_hero_title(
        "Entrümpelung in Siegen – zum Festpreis, ohne versteckte KostenJetzt kostenlos anfragen"
    )

    rich = (
        "<!-- wp:html -->\n"
        '<section class="wm3-hero-overlay">'
        '<div class="wm3-hero-card">'
        '<h1 class="wm3-hero-card__title">Altes Headline</h1>'
        '<a class="wm3-btn wm3-btn--primary" href="tel:1">Anrufen</a>'
        "</div></section>\n"
        "<!-- /wp:html -->\n"
        "<!-- wp:group -->lots of other content that must survive<!-- /wp:group -->"
    )
    page = {
        "slug": "homepage",
        "title": "Startseite",
        "body_html": rich,
        "hero": {"heading": "Altes Headline", "subheading": "", "cta_primary": ""},
        "headings": ["Altes Headline"],
    }
    out = _apply_tips(
        page,
        [_rec(proposed_html="Neues Headline zum Festpreis")],
    )
    body = out["body_html"]
    assert "Neues Headline zum Festpreis" in body
    assert "lots of other content that must survive" in body
    assert len(body) > len(rich) * 0.8


def test_materialize_backs_up_and_patches(tmp_path: Path):
    data_dir = tmp_path / "proj"
    json_dir = data_dir / "json"
    json_dir.mkdir(parents=True)
    body = (
        '<h1 class="wm3-hero-card__title">Alt</h1>'
        + ("x" * 3000)
    )
    (json_dir / "optimized_homepage.json").write_text(
        json.dumps({"slug": "homepage", "body_html": body, "hero": {}}),
        encoding="utf-8",
    )
    req = RenderRequest(
        page_slugs=["homepage"],
        approved=[_rec(proposed_html="Neu Titel")],
    )
    written = materialize_optimized_pages(data_dir, req)
    assert written
    assert has_render_backup(data_dir)
    patched = json.loads(
        (json_dir / "optimized_homepage.json").read_text(encoding="utf-8")
    )
    assert "Neu Titel" in patched["body_html"]
    assert "xxx" in patched["body_html"]

    # Destroy then undo
    (json_dir / "optimized_homepage.json").write_text(
        json.dumps({"slug": "homepage", "body_html": "DESTROYED"}),
        encoding="utf-8",
    )
    restored = restore_last_render_backup(data_dir)
    assert restored == ["homepage"]
    back = json.loads(
        (json_dir / "optimized_homepage.json").read_text(encoding="utf-8")
    )
    assert back["body_html"] == body
