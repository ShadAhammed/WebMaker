"""Unit tests for lenient JSON extraction used by WebsiteReviewer."""

from __future__ import annotations

import json

from webmaker.core.json_util import loads_lenient, salvage_review_payload


def test_loads_valid_json():
    data = loads_lenient('{"summary": "ok", "sections": []}')
    assert data == {"summary": "ok", "sections": []}


def test_loads_fenced_json():
    text = 'Here you go:\n```json\n{"summary": "Hallo", "sections": []}\n```\n'
    data = loads_lenient(text)
    assert data["summary"] == "Hallo"


def test_unescaped_quote_inside_german_string():
    # Classic Claude breakage: raw " inside a recommendation string
    broken = (
        '{\n'
        '  "summary": "Gute Grundlage. Aber "klare Knöpfe" fehlen.",\n'
        '  "sections": [\n'
        '    {\n'
        '      "page_slug": "homepage",\n'
        '      "section": "Überschrift",\n'
        '      "summary": "kurz",\n'
        '      "recommendations": [\n'
        '        {\n'
        '          "current": "Text mit "Anführungszeichen" drin",\n'
        '          "issue": "unklar",\n'
        '          "recommendation": "Klarer machen",\n'
        '          "proposed_html": "Entrümpelung zum Festpreis",\n'
        '          "reason": "mehr Anrufe",\n'
        '          "source": "ux",\n'
        '          "priority": "high"\n'
        '        }\n'
        '      ]\n'
        '    }\n'
        '  ]\n'
        '}'
    )
    try:
        json.loads(broken)
        raised = False
    except json.JSONDecodeError:
        raised = True
    assert raised, "fixture must be invalid strict JSON"

    data = loads_lenient(broken)
    assert data is not None
    assert "Grundlage" in data["summary"]
    assert data["sections"][0]["page_slug"] == "homepage"
    assert data["sections"][0]["recommendations"][0]["priority"] == "high"


def test_smart_quotes_normalized():
    broken = '{"summary": "Hallo „Welt“", "sections": []}'
    data = loads_lenient(broken)
    assert data is not None
    assert "Welt" in data["summary"]


def test_raw_newline_in_string():
    broken = '{\n  "summary": "Zeile eins\nZeile zwei",\n  "sections": []\n}'
    data = loads_lenient(broken)
    assert data is not None
    assert "Zeile" in data["summary"]


def test_trailing_comma():
    broken = '{"summary": "ok", "sections": [],}'
    data = loads_lenient(broken)
    assert data == {"summary": "ok", "sections": []}


def test_salvage_partial_sections_when_middle_corrupt():
    # First section valid-ish after lenient load; overall object may still break
    text = (
        '{\n'
        '  "summary": "Teilweise ok",\n'
        '  "sections": [\n'
        '    {"page_slug": "homepage", "section": "Hero", "summary": "a", '
        '"recommendations": [{"current": "x", "issue": "y", '
        '"recommendation": "z", "proposed_html": "Neu", "reason": "r", '
        '"source": "ux", "priority": "medium"}]},\n'
        '    {"page_slug": "about", "section": "Text", "summary": "BROKEN '
        'with "quote" midstream without escape", "recommendations": []}\n'
        '  ]\n'
        '}'
    )
    data = loads_lenient(text)
    if data is None:
        data = salvage_review_payload(text)
    assert data is not None
    assert data.get("summary") or data.get("sections")
    # At least homepage should be recoverable
    slugs = [s.get("page_slug") for s in (data.get("sections") or [])]
    assert "homepage" in slugs or "Teilweise" in str(data.get("summary") or "")
