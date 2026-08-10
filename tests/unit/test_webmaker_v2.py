"""
tests/unit/test_webmaker_v2.py
==============================
Unit tests for the WebMaker V2 agent architecture:

- schema round-trip (dump -> validate) for every artifact
- BaseAgent contract (validation, stamping, error normalisation)
- ArtifactStore typed load/save + OP-Content selection persistence
- Orchestrator single-agent rerun from stored artifacts + missing-artifact stop
- WebsiteReviewer with a mocked AIRouter (no live calls)
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from webmaker.agents.base import AgentContext, AgentError, BaseAgent
from webmaker.agents.design_recommendation import DesignInput, DesignRecommendationAgent
from webmaker.agents.website_reviewer import ReviewInput, WebsiteReviewerAgent
from webmaker.data.theme_catalog import THEMES
from webmaker.orchestrator import Orchestrator, OrchestratorError
from webmaker.orchestrator.store import ArtifactStore
from webmaker.schemas import (
    BusinessProfile,
    CompetitorProject,
    CompetitorProjects,
    DesignRecommendation,
    OpContent,
    QAArtifact,
    Recommendation,
    RenderRequest,
    RenderResult,
    SectionReview,
    TargetProject,
)
from webmaker.schemas.target import CrawledPage


# ── Schema round-trip ─────────────────────────────────────────────────────────

def _sample_artifacts() -> list:
    biz = BusinessProfile(name="Acme", industry="Entrümpelung", location="Berlin",
                          services=["clearance"], primary_color="#0055ff")
    target = TargetProject(
        target_url="https://acme.de", total_pages=2,
        pages=[CrawledPage(url="https://acme.de", title="Home", page_type="home",
                           word_count=120, headings=["h1"])],
        business=biz,
    )
    competitors = CompetitorProjects(
        competitors=[CompetitorProject(url="https://rival.de", name="Rival",
                                       strengths=["clear pricing"], keywords=["berlin"])],
    )
    op = OpContent(
        sections=[SectionReview(page_slug="homepage", section="hero", summary="weak",
                                recommendations=[Recommendation(
                                    id="r1", page_slug="homepage", section="hero",
                                    current="No H1", issue="missing headline",
                                    recommendation="add H1", reason="SEO",
                                    source="seo-best-practice", priority="high")])],
        page_slugs=["homepage"], summary="overall ok",
    )
    design = DesignRecommendation(selected_theme="kadence", selected_template="home-services",
                                  color_palette=["#0055ff"], visual_style="clean")
    req = RenderRequest(theme_id="kadence", template_id="home-services",
                        page_slugs=["homepage"], approved=[])
    res = RenderResult(wp_url="http://localhost", pages_rendered=["homepage"],
                       theme_applied="kadence", success=True)
    qa = QAArtifact(wp_url="http://localhost", overall_score=0.8, passed=True)
    return [biz, target, competitors, op, design, req, res, qa]


@pytest.mark.parametrize("artifact", _sample_artifacts())
def test_schema_round_trip(artifact) -> None:
    dumped = artifact.model_dump(mode="json")
    restored = type(artifact).model_validate(dumped)
    assert restored == artifact
    # JSON serialisable and stable.
    assert json.loads(json.dumps(dumped, sort_keys=True)) == dumped


def test_recommendation_has_exact_spec_fields() -> None:
    fields = set(Recommendation.model_fields)
    for required in ("current", "issue", "recommendation", "reason", "source",
                     "priority", "selected", "id", "page_slug", "section"):
        assert required in fields


def test_op_content_selected_filter() -> None:
    op = OpContent(sections=[SectionReview(page_slug="homepage", section="hero",
        recommendations=[
            Recommendation(id="a", selected=True),
            Recommendation(id="b", selected=False),
        ])])
    assert [r.id for r in op.selected_recommendations()] == ["a"]
    assert len(op.all_recommendations()) == 2


# ── BaseAgent contract ────────────────────────────────────────────────────────

class _InModel(BusinessProfile):
    pass


def _ctx(tmp_path: Path) -> AgentContext:
    return AgentContext(project_slug="proj", data_dir=tmp_path,
                        settings=SimpleNamespace(), extras={})


class _GoodAgent(BaseAgent[BusinessProfile, DesignRecommendation]):
    name = "good"
    input_model = BusinessProfile
    output_model = DesignRecommendation

    def _run(self, data: BusinessProfile) -> DesignRecommendation:
        return DesignRecommendation(selected_theme="kadence")


class _BadOutputAgent(BaseAgent[BusinessProfile, DesignRecommendation]):
    name = "bad"
    input_model = BusinessProfile
    output_model = DesignRecommendation

    def _run(self, data: BusinessProfile) -> DesignRecommendation:
        return OpContent()  # wrong type


class _RaisingAgent(BaseAgent[BusinessProfile, DesignRecommendation]):
    name = "boom"
    input_model = BusinessProfile
    output_model = DesignRecommendation

    def _run(self, data: BusinessProfile) -> DesignRecommendation:
        raise ValueError("kaboom")


def test_base_agent_runs_and_stamps(tmp_path: Path) -> None:
    agent = _GoodAgent(_ctx(tmp_path))
    out = agent.run(BusinessProfile(name="Acme"))
    assert out.selected_theme == "kadence"
    assert out.meta.agent == "good"
    assert out.meta.project == "proj"
    assert out.meta.artifact_id == "proj:good"


def test_base_agent_rejects_wrong_input_type(tmp_path: Path) -> None:
    agent = _GoodAgent(_ctx(tmp_path))
    with pytest.raises(AgentError):
        agent.run(OpContent())  # type: ignore[arg-type]


def test_base_agent_rejects_wrong_output_type(tmp_path: Path) -> None:
    agent = _BadOutputAgent(_ctx(tmp_path))
    with pytest.raises(AgentError):
        agent.run(BusinessProfile())


def test_base_agent_normalises_exceptions(tmp_path: Path) -> None:
    agent = _RaisingAgent(_ctx(tmp_path))
    with pytest.raises(AgentError):
        agent.run(BusinessProfile())


# ── ArtifactStore ─────────────────────────────────────────────────────────────

def test_artifact_store_round_trip_and_selection_persistence(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    op = OpContent(sections=[SectionReview(page_slug="homepage", section="hero",
        recommendations=[Recommendation(id="x", selected=False)])])

    path = store.save(op)
    assert path.is_file()

    # Flip selection, save again, reload — selection persists.
    op.sections[0].recommendations[0].selected = True
    store.save(op)
    reloaded = store.load(OpContent)
    assert reloaded is not None
    assert reloaded.selected_recommendations()[0].id == "x"


def test_artifact_store_missing_returns_none(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    assert store.load(OpContent) is None


# ── Orchestrator ──────────────────────────────────────────────────────────────

class _FakeManager:
    def __init__(self, project_dir: Path, data_dir: Path, *, target_url: str = "",
                 competitor_urls: list[str] | None = None) -> None:
        self._project_dir = project_dir
        self._data_dir = data_dir
        self._state = SimpleNamespace(
            target_url=target_url,
            competitor_urls=competitor_urls or [],
            project_dir=str(project_dir),
        )

    def get_project_dir(self) -> Path:
        return self._project_dir

    def get_data_dir(self) -> Path:
        return self._data_dir

    @property
    def active_state(self):
        return self._state

    def sync_crawl_data_dir(self, url: str | None = None) -> Path:
        return self._data_dir


def _orchestrator(tmp_path: Path, **kwargs) -> Orchestrator:
    project_dir = tmp_path / "proj"
    (project_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    manager = _FakeManager(project_dir, tmp_path / "data", **kwargs)
    return Orchestrator(SimpleNamespace(), "proj", manager=manager)


def test_orchestrator_single_agent_rerun_from_stored_artifact(tmp_path: Path) -> None:
    orch = _orchestrator(tmp_path)
    # Seed the upstream artifact only.
    target = TargetProject(target_url="https://acme.de",
                           business=BusinessProfile(name="Acme", industry="clearance",
                                                    services=["entrümpelung"]))
    orch.store.save(target)

    # Rerun a single downstream agent without running the crawler.
    design = orch.run_agent("design_recommendation")
    assert isinstance(design, DesignRecommendation)
    assert design.selected_theme  # deterministic pick made
    # Persisted to disk.
    assert orch.store.load(DesignRecommendation) is not None


def test_orchestrator_stops_on_missing_upstream(tmp_path: Path) -> None:
    orch = _orchestrator(tmp_path)
    with pytest.raises(OrchestratorError):
        orch.run_agent("website_reviewer")  # no TargetProject stored


def test_ensure_crawl_artifacts_runs_missing_agents(tmp_path: Path, monkeypatch) -> None:
    orch = _orchestrator(tmp_path, target_url="https://acme.de")
    called: list[str] = []

    def fake_run(name: str, *, extras=None):
        called.append(name)
        if name == "target_crawler":
            art = TargetProject(
                target_url="https://acme.de",
                business=BusinessProfile(name="Acme", industry="clearance", services=["x"]),
            )
            orch.store.save(art)
            return art
        if name == "competitor_crawler":
            art = CompetitorProjects()
            orch.store.save(art)
            return art
        raise AssertionError(name)

    monkeypatch.setattr(orch, "run_agent", fake_run)
    orch.ensure_crawl_artifacts()
    assert called == ["target_crawler", "competitor_crawler"]
    # Second call is a no-op when artifacts already exist.
    called.clear()
    orch.ensure_crawl_artifacts()
    assert called == []


def test_orchestrator_unknown_agent(tmp_path: Path) -> None:
    orch = _orchestrator(tmp_path)
    with pytest.raises(OrchestratorError):
        orch.run_agent("does_not_exist")


# ── WebsiteReviewer with mocked AIRouter ──────────────────────────────────────

class _FakeRouter:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls: list[dict] = []

    def request(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(text=json.dumps(self._payload))


def test_website_reviewer_parses_ai_json(tmp_path: Path) -> None:
    payload = {
        "summary": "needs work",
        "sections": [
            {
                "page_slug": "homepage",
                "section": "hero",
                "summary": "weak hero",
                "recommendations": [
                    {
                        "current": "no headline",
                        "issue": "missing H1",
                        "recommendation": "add a clear H1",
                        "reason": "SEO + clarity",
                        "source": "seo-best-practice",
                        "priority": "critical",
                    }
                ],
            }
        ],
    }
    router = _FakeRouter(payload)
    agent = WebsiteReviewerAgent(_ctx(tmp_path), router=router)
    target = TargetProject(business=BusinessProfile(name="Acme"))
    out = agent.run(ReviewInput(target=target, competitors=CompetitorProjects()))

    assert out.summary == "needs work"
    recs = out.all_recommendations()
    assert len(recs) == 1
    assert recs[0].priority == "critical"
    assert recs[0].selected is False  # defaults to not approved
    assert recs[0].id  # id assigned
    assert router.calls  # AI was called


def test_website_reviewer_degrades_without_ai(tmp_path: Path) -> None:
    class _Boom:
        def request(self, *a, **k):
            raise RuntimeError("no key")

    agent = WebsiteReviewerAgent(_ctx(tmp_path), router=_Boom())
    out = agent.run(ReviewInput(target=TargetProject(), competitors=CompetitorProjects()))
    assert isinstance(out, OpContent)
    assert out.all_recommendations() == []


def test_design_recommendation_is_deterministic(tmp_path: Path) -> None:
    class _NoGpt:
        def is_available(self, provider):
            return False

        def request(self, *a, **k):
            raise AssertionError("should not call GPT when unavailable")

    agent = DesignRecommendationAgent(_ctx(tmp_path), router=_NoGpt())
    biz = BusinessProfile(industry="home services", services=["cleaning"])
    a = agent.run(DesignInput(business=biz))
    b = agent.run(DesignInput(business=biz))
    assert a.options == b.options
    assert a.selected_theme == b.selected_theme
    assert len(a.patterns) == 8
    assert {str(p.slot) for p in a.patterns} == {
        "hero", "services", "about", "process", "testimonial", "faq", "cta", "footer"
    }
    assert all(p.justification for p in a.patterns)


def test_design_recommendation_uses_gpt_ranking(tmp_path: Path) -> None:
    # Pick a real catalog pair so merge accepts it.
    theme = THEMES[0]
    tmpl = theme["templates"][0]
    payload = {
        "selected_theme": theme["id"],
        "selected_template": tmpl["id"],
        "theme_justification": "best SEO theme",
        "template_justification": "best brand fit",
        "typography": "sans-serif headings",
        "color_palette": ["#112233"],
        "visual_style": "bold local trust",
        "business_style": "trades",
        "patterns": [
            {"slot": "hero", "pattern_id": "hero-trust-local", "justification": "local trust"},
            {"slot": "services", "pattern_id": "services-card-grid-3", "justification": "clear services"},
            {"slot": "about", "pattern_id": "about-story-photo", "justification": "human story"},
            {"slot": "process", "pattern_id": "process-steps-4", "justification": "simple steps"},
            {"slot": "testimonial", "pattern_id": "testimonial-quote-cards", "justification": "proof"},
            {"slot": "faq", "pattern_id": "faq-accordion-local", "justification": "local FAQ"},
            {"slot": "cta", "pattern_id": "cta-phone-band", "justification": "call now"},
            {"slot": "footer", "pattern_id": "footer-local-compact", "justification": "legal + contact"},
        ],
    }

    class _GptRouter:
        def __init__(self):
            self.calls = []

        def is_available(self, provider):
            from webmaker.core.types import AIProvider
            return provider == AIProvider.OPENAI

        def request(self, prompt, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(text=json.dumps(payload))

    router = _GptRouter()
    agent = DesignRecommendationAgent(_ctx(tmp_path), router=router)
    out = agent.run(DesignInput(business=BusinessProfile(name="Acme", industry="cleaning")))
    assert out.selected_theme == theme["id"]
    assert out.selected_template == tmpl["id"]
    assert out.visual_style == "bold local trust"
    assert out.color_palette == ["#112233"]
    assert out.theme_justification == "best SEO theme"
    assert out.pattern_for("hero") and out.pattern_for("hero").pattern_id == "hero-trust-local"
    assert router.calls
    assert router.calls[0].get("provider").value == "openai"


def test_qa_reviewer_merges_gpt_visual_opinion(tmp_path: Path) -> None:
    from webmaker.agents.qa_reviewer import QAAgentInput, QAReviewerAgent
    from webmaker.core.types import QAReport

    class _FakeReviewer:
        def review_from_directory(self, *a, **k):
            assert k.get("content_ai") == "claude"
            return QAReport(
                wp_url="http://localhost",
                overall_score=0.7,
                passed=True,
                recommendations=["[Claude content] fix CTA"],
            )

    class _GptRouter:
        def is_available(self, provider):
            from webmaker.core.types import AIProvider
            return provider == AIProvider.OPENAI

        def request(self, prompt, **kwargs):
            return SimpleNamespace(text=json.dumps({
                "summary": "Looks cleaner than average.",
                "looks_significantly_better": True,
                "score": 0.85,
                "strengths": ["clear hero"],
                "weaknesses": ["nav crowded"],
                "recommendations": ["shorten nav labels"],
            }))

    agent = QAReviewerAgent(
        _ctx(tmp_path), reviewer=_FakeReviewer(), router=_GptRouter()
    )
    out = agent.run(QAAgentInput(render=RenderResult(wp_url="http://localhost", success=True)))
    assert "Looks cleaner" in out.visual_review
    assert out.content_review
    assert any("fix CTA" in r for r in out.recommendations)
    assert any(r.startswith("[GPT visual]") for r in out.recommendations)
    assert not any("DeepSeek" in r for r in out.recommendations)
