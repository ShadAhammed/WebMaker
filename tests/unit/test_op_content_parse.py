"""WebsiteReviewer JSON parse / repair helpers."""

from __future__ import annotations

from webmaker.agents.website_reviewer.agent import WebsiteReviewerAgent


def test_parse_valid_json() -> None:
    raw = '{"summary": "ok", "sections": []}'
    data = WebsiteReviewerAgent._parse_json(raw)
    assert data == {"summary": "ok", "sections": []}


def test_parse_fenced_json() -> None:
    raw = 'Here you go:\n```json\n{"summary": "s", "sections": []}\n```\n'
    data = WebsiteReviewerAgent._parse_json(raw)
    assert data is not None
    assert data["summary"] == "s"


def test_repair_truncated_json() -> None:
    broken = (
        '{"summary": "partial", "sections": [{"page_slug": "homepage", '
        '"section": "hero", "recommendations": [{"current": "a", '
        '"issue": "b", "recommendation": "c", "reason": "d", '
        '"source": "seo", "priority": "high"}'
    )
    data = WebsiteReviewerAgent._repair_json(broken)
    assert data is not None
    assert data["summary"] == "partial"
    assert data["sections"][0]["page_slug"] == "homepage"
