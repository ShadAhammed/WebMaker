"""AIRouter cache + prompt integration tests (no live providers)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from webmaker.config.settings import Settings
from webmaker.core.types import AIProvider
from webmaker.modules.ai_cache import AICache
from webmaker.modules.ai_router import AIResponse, AIRouter


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        cache_dir=tmp_path / "cache",
        gemini_api_key="test-gemini-key",
        gemini_model="gemini-test",
        claude_api_key="",
        deepseek_api_key="",
        ai_provider="gemini",
    )


def test_request_uses_cache(settings: Settings, tmp_path: Path):
    cache = AICache(tmp_path / "cache", enabled=True)
    router = AIRouter(settings, cache=cache)

    # Stub the gemini client
    mock_client = MagicMock()
    mock_client.model = "gemini-test"
    mock_client.complete.return_value = AIResponse(
        text="cached-or-fresh",
        provider="gemini",
        model="gemini-test",
        finish_reason="stop",
    )
    router._clients[AIProvider.GEMINI] = mock_client
    router._providers[AIProvider.GEMINI] = True

    r1 = router.request("Hello world", provider=AIProvider.GEMINI, system="sys")
    assert r1.text == "cached-or-fresh"
    assert mock_client.complete.call_count == 1

    r2 = router.request("Hello world", provider=AIProvider.GEMINI, system="sys")
    assert r2.text == "cached-or-fresh"
    assert r2.finish_reason == "cache"
    assert mock_client.complete.call_count == 1  # no second call

    assert router.invalidate_cache() >= 1
    r3 = router.request("Hello world", provider=AIProvider.GEMINI, system="sys")
    assert mock_client.complete.call_count == 2
    assert r3.finish_reason != "cache"


def test_load_prompt_on_router(settings: Settings):
    router = AIRouter(settings, enable_cache=False)
    text = router.load_prompt("business")
    assert isinstance(text, str) and len(text) > 10
