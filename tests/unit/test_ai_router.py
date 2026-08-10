"""
tests/unit/test_ai_router.py
==============================
Unit tests for AIRouter.

Provider SDKs (google-genai, anthropic, openai) are fully mocked —
no real network calls or API keys are required.

Coverage:
  - Configuration loading / provider availability
  - Provider client initialisation
  - select_provider (affinity, settings, fallback)
  - _build_fallback_chain
  - complete() / request() routing and response normalisation
  - analyze_content / generate_text
  - Retry on transient errors
  - No retry on auth errors
  - Fallback across providers
  - Empty response rejection
  - health_check
  - Secret redaction
  - Error handling
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from webmaker.config.settings import Settings
from webmaker.core.exceptions import (
    AIError,
    AIProviderUnavailableError,
    AIResponseError,
)
from webmaker.core.types import AIProvider
from webmaker.modules.ai_router import (
    AIResponse,
    AIRouter,
    _ClaudeProvider,
    _DeepSeekProvider,
    _GeminiProvider,
    _OpenAIProvider,
    _RequestOptions,
    _is_retryable,
    _redact_secrets,
    _wrap_provider_error,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_router(settings: Settings) -> AIRouter:
    """Build an AIRouter with cache disabled (avoids cross-test pollution)."""
    return AIRouter(settings, enable_cache=False)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def settings_no_keys(test_settings: Settings) -> Settings:
    return Settings(
        project_root=test_settings.project_root,
        gemini_api_key="",
        claude_api_key="",
        deepseek_api_key="",
        gpt_api_key="",
        ai_provider="auto",
    )


@pytest.fixture
def settings_gemini_only(test_settings: Settings) -> Settings:
    return Settings(
        project_root=test_settings.project_root,
        gemini_api_key="fake-gemini-key",
        claude_api_key="",
        deepseek_api_key="",
        gpt_api_key="",
        gemini_model="gemini-1.5-pro",
        ai_provider="auto",
    )


@pytest.fixture
def settings_all(test_settings: Settings) -> Settings:
    return Settings(
        project_root=test_settings.project_root,
        gemini_api_key="fake-gemini-key",
        claude_api_key="fake-claude-key",
        deepseek_api_key="fake-deepseek-key",
        gpt_api_key="fake-gpt-key",
        gemini_model="gemini-1.5-pro",
        claude_model="claude-3-5-sonnet-20241022",
        deepseek_model="deepseek-chat",
        gpt_model="gpt-5.5-pro",
        ai_provider="auto",
    )


@pytest.fixture
def settings_claude_preferred(test_settings: Settings) -> Settings:
    return Settings(
        project_root=test_settings.project_root,
        gemini_api_key="fake-gemini-key",
        claude_api_key="fake-claude-key",
        deepseek_api_key="fake-deepseek-key",
        gpt_api_key="fake-gpt-key",
        ai_provider="claude",
    )


def _mock_provider_response(text: str = "Hello from AI", provider: str = "gemini") -> AIResponse:
    return AIResponse(
        text=text,
        provider=provider,
        model="test-model",
        finish_reason="stop",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        latency_s=0.01,
    )


# ── Configuration / init ──────────────────────────────────────────────────────

class TestAIRouterInit:
    def test_init_no_keys(self, settings_no_keys: Settings) -> None:
        router = make_router(settings_no_keys)
        assert router.available_providers() == []
        assert router._clients == {}

    def test_init_with_gemini_key(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        assert AIProvider.GEMINI in router.available_providers()
        assert AIProvider.GEMINI in router._clients
        assert isinstance(router._clients[AIProvider.GEMINI], _GeminiProvider)

    def test_init_all_providers(self, settings_all: Settings) -> None:
        router = make_router(settings_all)
        assert set(router.available_providers()) == {
            AIProvider.GEMINI, AIProvider.CLAUDE, AIProvider.DEEPSEEK, AIProvider.OPENAI,
        }
        assert isinstance(router._clients[AIProvider.CLAUDE], _ClaudeProvider)
        assert isinstance(router._clients[AIProvider.DEEPSEEK], _DeepSeekProvider)
        assert isinstance(router._clients[AIProvider.OPENAI], _OpenAIProvider)

    def test_is_available_false_without_key(self, settings_no_keys: Settings) -> None:
        router = make_router(settings_no_keys)
        assert router.is_available(AIProvider.GEMINI) is False
        assert router.is_available(AIProvider.CLAUDE) is False
        assert router.is_available(AIProvider.DEEPSEEK) is False
        assert router.is_available(AIProvider.OPENAI) is False

    def test_is_available_true_with_key(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        assert router.is_available(AIProvider.GEMINI) is True
        assert router.is_available(AIProvider.CLAUDE) is False

    def test_get_model_name(self, settings_all: Settings) -> None:
        router = make_router(settings_all)
        assert router.get_model_name(AIProvider.GEMINI) == "gemini-1.5-pro"
        assert router.get_model_name(AIProvider.CLAUDE) == "claude-3-5-sonnet-20241022"
        assert router.get_model_name(AIProvider.DEEPSEEK) == "deepseek-chat"
        assert router.get_model_name(AIProvider.OPENAI) == "gpt-5.5-pro"

    def test_whitespace_key_treated_as_missing(self, test_settings: Settings) -> None:
        s = Settings(
            project_root=test_settings.project_root,
            gemini_api_key="   ",
            claude_api_key="",
            deepseek_api_key="",
            gpt_api_key="",
        )
        router = make_router(s)
        assert router.available_providers() == []


# ── Provider selection ────────────────────────────────────────────────────────

class TestSelectProvider:
    def test_raises_when_none_available(self, settings_no_keys: Settings) -> None:
        router = make_router(settings_no_keys)
        with pytest.raises(AIProviderUnavailableError):
            router.select_provider("any")

    def test_preferred_wins(self, settings_all: Settings) -> None:
        router = make_router(settings_all)
        assert router.select_provider(
            "business_analysis", preferred=AIProvider.DEEPSEEK
        ) == AIProvider.DEEPSEEK

    def test_task_affinity_claude_business(self, settings_all: Settings) -> None:
        router = make_router(settings_all)
        assert router.select_provider("business_analysis") == AIProvider.CLAUDE
        assert router.select_provider("competitor_analysis") == AIProvider.DEEPSEEK

    def test_task_affinity_claude(self, settings_all: Settings) -> None:
        router = make_router(settings_all)
        assert router.select_provider("page_copy") == AIProvider.CLAUDE
        assert router.select_provider("content_generation") == AIProvider.CLAUDE
        assert router.select_provider("qa_review") == AIProvider.CLAUDE

    def test_task_affinity_deepseek(self, settings_all: Settings) -> None:
        router = make_router(settings_all)
        assert router.select_provider("competitor_analysis") == AIProvider.DEEPSEEK
        assert router.select_provider("content_review") == AIProvider.DEEPSEEK

    def test_task_affinity_openai(self, settings_all: Settings) -> None:
        router = make_router(settings_all)
        assert router.select_provider("design_recommendation") == AIProvider.OPENAI
        assert router.select_provider("qa_visual_review") == AIProvider.OPENAI

    def test_settings_ai_provider(self, settings_claude_preferred: Settings) -> None:
        router = make_router(settings_claude_preferred)
        # No task affinity for "misc" → settings preference
        assert router.select_provider("misc") == AIProvider.CLAUDE

    def test_falls_back_when_affinity_unavailable(
        self, settings_gemini_only: Settings
    ) -> None:
        router = make_router(settings_gemini_only)
        # qa_review prefers claude, but only gemini is available
        assert router.select_provider("qa_review") == AIProvider.GEMINI


class TestFallbackChain:
    def test_preferred_first(self, settings_all: Settings) -> None:
        router = make_router(settings_all)
        chain = router._build_fallback_chain(AIProvider.DEEPSEEK)
        assert chain[0] == AIProvider.DEEPSEEK
        assert set(chain) == {
            AIProvider.GEMINI, AIProvider.CLAUDE, AIProvider.DEEPSEEK, AIProvider.OPENAI,
        }

    def test_excludes_unavailable(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        chain = router._build_fallback_chain(AIProvider.GEMINI)
        assert chain == [AIProvider.GEMINI]


# ── complete / request ────────────────────────────────────────────────────────

class TestCompleteAndRequest:
    def test_complete_returns_text(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        mock_client = MagicMock()
        mock_client.model = "gemini-1.5-pro"
        mock_client.complete.return_value = _mock_provider_response("OK result")
        router._clients[AIProvider.GEMINI] = mock_client

        text = router.complete("Say hello", provider=AIProvider.GEMINI)
        assert text == "OK result"
        mock_client.complete.assert_called_once()

    def test_request_returns_ai_response(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        mock_client = MagicMock()
        mock_client.model = "gemini-1.5-pro"
        mock_client.complete.return_value = _mock_provider_response("Detailed")
        router._clients[AIProvider.GEMINI] = mock_client

        resp = router.request("prompt", provider=AIProvider.GEMINI)
        assert isinstance(resp, AIResponse)
        assert resp.text == "Detailed"
        assert resp.provider == "gemini"

    def test_passes_system_and_options(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        mock_client = MagicMock()
        mock_client.model = "m"
        mock_client.complete.return_value = _mock_provider_response("x")
        router._clients[AIProvider.GEMINI] = mock_client

        router.complete(
            "user prompt",
            provider=AIProvider.GEMINI,
            system="sys",
            max_tokens=100,
            temperature=0.1,
        )
        opts: _RequestOptions = mock_client.complete.call_args.args[0]
        assert opts.system == "sys"
        assert opts.max_tokens == 100
        assert opts.temperature == 0.1
        assert opts.prompt == "user prompt"

    def test_empty_prompt_raises(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        with pytest.raises(AIError, match="non-empty"):
            router.complete("   ")

    def test_no_providers_raises(self, settings_no_keys: Settings) -> None:
        router = make_router(settings_no_keys)
        with pytest.raises(AIProviderUnavailableError):
            router.complete("hello")

    def test_empty_response_rejected(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        mock_client = MagicMock()
        mock_client.model = "m"
        mock_client.complete.return_value = _mock_provider_response("")
        router._clients[AIProvider.GEMINI] = mock_client

        with pytest.raises(AIError):
            router.complete("hello", provider=AIProvider.GEMINI)

    def test_empty_response_via_request(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        mock_client = MagicMock()
        mock_client.model = "m"
        mock_client.complete.return_value = _mock_provider_response("  ")
        router._clients[AIProvider.GEMINI] = mock_client

        with pytest.raises((AIResponseError, AIError)):
            router.request("hello", provider=AIProvider.GEMINI, allow_fallback=False)

    def test_merges_context_into_prompt(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        mock_client = MagicMock()
        mock_client.model = "m"
        mock_client.complete.return_value = _mock_provider_response("ok")
        router._clients[AIProvider.GEMINI] = mock_client

        router.request(
            "Base",
            provider=AIProvider.GEMINI,
            context={"city": "Berlin"},
        )
        opts: _RequestOptions = mock_client.complete.call_args.args[0]
        assert "Berlin" in opts.prompt
        assert "Base" in opts.prompt


# ── Fallback + retries ────────────────────────────────────────────────────────

class TestRetryAndFallback:
    def test_retries_transient_error(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        router._max_retries = 2
        mock_client = MagicMock()
        mock_client.model = "m"
        mock_client.complete.side_effect = [
            AIError("rate limit 429", provider="gemini", retryable=True),
            _mock_provider_response("recovered"),
        ]
        router._clients[AIProvider.GEMINI] = mock_client

        with patch("webmaker.modules.ai_router.time.sleep"):
            text = router.complete("hi", provider=AIProvider.GEMINI)

        assert text == "recovered"
        assert mock_client.complete.call_count == 2

    def test_no_retry_on_auth_error(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        router._max_retries = 3
        mock_client = MagicMock()
        mock_client.model = "m"
        mock_client.complete.side_effect = AIProviderUnavailableError(
            "gemini authentication failed — check API key",
            provider="gemini",
        )
        router._clients[AIProvider.GEMINI] = mock_client

        with patch("webmaker.modules.ai_router.time.sleep") as sleep:
            with pytest.raises(AIError):
                router.request(
                    "hi",
                    provider=AIProvider.GEMINI,
                    allow_fallback=False,
                )
        sleep.assert_not_called()
        assert mock_client.complete.call_count == 1

    def test_fallback_to_next_provider(self, settings_all: Settings) -> None:
        router = make_router(settings_all)
        gemini = MagicMock()
        gemini.model = "g"
        gemini.complete.side_effect = AIError("boom", provider="gemini", retryable=False)
        claude = MagicMock()
        claude.model = "c"
        claude.complete.return_value = _mock_provider_response(
            "from claude", provider="claude"
        )
        deepseek = MagicMock()
        deepseek.model = "d"

        router._clients[AIProvider.GEMINI] = gemini
        router._clients[AIProvider.CLAUDE] = claude
        router._clients[AIProvider.DEEPSEEK] = deepseek
        router._max_retries = 0

        text = router.complete("hi", provider=AIProvider.GEMINI)
        assert text == "from claude"
        gemini.complete.assert_called()
        claude.complete.assert_called_once()

    def test_all_providers_fail(self, settings_all: Settings) -> None:
        router = make_router(settings_all)
        router._max_retries = 0
        for p in (AIProvider.GEMINI, AIProvider.CLAUDE, AIProvider.DEEPSEEK, AIProvider.OPENAI):
            m = MagicMock()
            m.model = "x"
            m.complete.side_effect = AIError("down", provider=p.value, retryable=False)
            router._clients[p] = m

        with pytest.raises(AIError, match="All AI providers failed"):
            router.complete("hi")


# ── analyze_content / generate_text ───────────────────────────────────────────

class TestHighLevelHelpers:
    def test_analyze_content_parses_json(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        mock_client = MagicMock()
        mock_client.model = "m"
        mock_client.complete.return_value = _mock_provider_response(
            '{"score": 0.9, "notes": ["ok"]}'
        )
        router._clients[AIProvider.GEMINI] = mock_client

        result = router.analyze_content("<p>Hi</p>", "summarise", provider=AIProvider.GEMINI)
        assert result["score"] == 0.9
        assert result["notes"] == ["ok"]

    def test_analyze_content_raw_fallback(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        mock_client = MagicMock()
        mock_client.model = "m"
        mock_client.complete.return_value = _mock_provider_response("not json at all")
        router._clients[AIProvider.GEMINI] = mock_client

        result = router.analyze_content("c", "t", provider=AIProvider.GEMINI)
        assert result["raw"] == "not json at all"

    def test_generate_text(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        mock_client = MagicMock()
        mock_client.model = "m"
        mock_client.complete.return_value = _mock_provider_response("Generated copy")
        router._clients[AIProvider.GEMINI] = mock_client

        text = router.generate_text(
            "page_copy",
            {"company": "Acme"},
            provider=AIProvider.GEMINI,
        )
        assert text == "Generated copy"
        opts: _RequestOptions = mock_client.complete.call_args.args[0]
        assert "Acme" in opts.prompt
        assert "page_copy" in opts.prompt


# ── health_check ──────────────────────────────────────────────────────────────

class TestHealthCheck:
    def test_health_check_ok(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        mock_client = MagicMock()
        mock_client.model = "m"
        mock_client.complete.return_value = _mock_provider_response("OK")
        router._clients[AIProvider.GEMINI] = mock_client
        assert router.health_check(AIProvider.GEMINI) is True

    def test_health_check_unavailable_provider(
        self, settings_gemini_only: Settings
    ) -> None:
        router = make_router(settings_gemini_only)
        assert router.health_check(AIProvider.CLAUDE) is False

    def test_health_check_auto_false(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        assert router.health_check(AIProvider.AUTO) is False

    def test_health_check_failure(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        mock_client = MagicMock()
        mock_client.model = "m"
        mock_client.complete.side_effect = AIError("down", retryable=False)
        router._clients[AIProvider.GEMINI] = mock_client
        router._max_retries = 0
        assert router.health_check(AIProvider.GEMINI) is False


# ── Provider adapters (unit, mocked SDK) ──────────────────────────────────────

class TestGeminiAdapter:
    def test_send_normalises_response(self) -> None:
        provider = _GeminiProvider("key", "gemini-1.5-pro")
        fake_client = MagicMock()
        fake_result = MagicMock()
        fake_result.text = "  Gemini says hi  "
        fake_result.usage_metadata = MagicMock(
            prompt_token_count=3,
            candidates_token_count=2,
            total_token_count=5,
        )
        fake_client.models.generate_content.return_value = fake_result
        provider._client = fake_client

        fake_types = MagicMock()
        fake_types.GenerateContentConfig = MagicMock(side_effect=lambda **kw: kw)

        import sys
        with patch.dict(sys.modules, {"google.genai.types": fake_types}):
            resp = provider._send(_RequestOptions(prompt="hi", system="sys"))

        assert resp.text == "Gemini says hi"
        assert resp.provider == "gemini"
        assert resp.usage["total_tokens"] == 5
        fake_client.models.generate_content.assert_called_once()


class TestClaudeAdapter:
    def test_send_normalises_response(self) -> None:
        provider = _ClaudeProvider("key", "claude-3-5-sonnet-20241022")
        block = MagicMock()
        block.type = "text"
        block.text = "Claude reply"
        fake_result = MagicMock()
        fake_result.content = [block]
        fake_result.stop_reason = "end_turn"
        fake_result.usage = MagicMock(input_tokens=4, output_tokens=6)

        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_result
        provider._client = fake_client

        resp = provider._send(_RequestOptions(prompt="hi", system="be brief"))
        assert resp.text == "Claude reply"
        assert resp.provider == "claude"
        assert resp.usage["prompt_tokens"] == 4
        assert resp.finish_reason == "end_turn"
        kwargs = fake_client.messages.create.call_args.kwargs
        assert kwargs["system"] == "be brief"


class TestDeepSeekAdapter:
    def test_send_normalises_response(self) -> None:
        provider = _DeepSeekProvider("key", "deepseek-chat")
        msg = MagicMock()
        msg.content = "DeepSeek reply"
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "stop"
        fake_result = MagicMock()
        fake_result.choices = [choice]
        fake_result.usage = MagicMock(
            prompt_tokens=2, completion_tokens=3, total_tokens=5
        )

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_result
        provider._client = fake_client

        resp = provider._send(_RequestOptions(prompt="review this", system="reviewer"))
        assert resp.text == "DeepSeek reply"
        assert resp.provider == "deepseek"
        assert resp.usage["total_tokens"] == 5
        kwargs = fake_client.chat.completions.create.call_args.kwargs
        assert kwargs["messages"][0]["role"] == "system"
        assert kwargs["messages"][1]["role"] == "user"


class TestOpenAIAdapter:
    def test_send_gpt5_uses_max_completion_tokens(self) -> None:
        provider = _OpenAIProvider("key", "gpt-5.5-pro")
        msg = MagicMock()
        msg.content = "GPT reply"
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "stop"
        fake_result = MagicMock()
        fake_result.choices = [choice]
        fake_result.usage = MagicMock(
            prompt_tokens=2, completion_tokens=3, total_tokens=5
        )

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_result
        provider._client = fake_client

        resp = provider._send(_RequestOptions(prompt="rank themes", system="designer"))
        assert resp.text == "GPT reply"
        assert resp.provider == "openai"
        kwargs = fake_client.chat.completions.create.call_args.kwargs
        assert "max_completion_tokens" in kwargs
        assert "temperature" not in kwargs
        assert kwargs["messages"][0]["role"] == "system"

    def test_send_gpt4_uses_temperature(self) -> None:
        provider = _OpenAIProvider("key", "gpt-4o")
        msg = MagicMock()
        msg.content = "ok"
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "stop"
        fake_result = MagicMock()
        fake_result.choices = [choice]
        fake_result.usage = None
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_result
        provider._client = fake_client

        provider._send(_RequestOptions(prompt="hi", temperature=0.2, max_tokens=100))
        kwargs = fake_client.chat.completions.create.call_args.kwargs
        assert kwargs["max_tokens"] == 100
        assert kwargs["temperature"] == 0.2


# ── Helpers ───────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_redact_secrets(self) -> None:
        assert "sk-***" in _redact_secrets("token sk-abcdefghijklmnop error")
        assert "AIza***" in _redact_secrets("AIzaSyDummyKeyValueXX")
        assert "key=***" in _redact_secrets("api key=secretvalue")

    def test_wrap_rate_limit(self) -> None:
        err = _wrap_provider_error("gemini", Exception("Error 429 rate limit"))
        assert isinstance(err, AIError)
        assert err.context.get("retryable") is True

    def test_wrap_auth(self) -> None:
        exc = Exception("Invalid API key")
        exc.status_code = 401  # type: ignore[attr-defined]
        err = _wrap_provider_error("claude", exc)
        assert isinstance(err, AIProviderUnavailableError)

    def test_is_retryable(self) -> None:
        assert _is_retryable(AIError("x", retryable=True)) is True
        assert _is_retryable(AIError("x", retryable=False)) is False
        assert _is_retryable(AIProviderUnavailableError("auth")) is False

    def test_parse_json_fenced(self, settings_gemini_only: Settings) -> None:
        router = make_router(settings_gemini_only)
        data = router._parse_json_object('```json\n{"a": 1}\n```')
        assert data == {"a": 1}
