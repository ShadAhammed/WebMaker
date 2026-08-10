"""
webmaker.modules.ai_router
===========================
Central gateway for all AI provider interactions.

Every WebMaker module must talk to Gemini, Claude, or DeepSeek **only**
through this module.  Provider SDKs are never imported elsewhere.

Architecture
------------
- ``AIResponse``      – normalised response returned to callers
- ``_BaseProvider``   – abstract adapter; add future providers by subclassing
- ``AIRouter``        – public façade: routing, retries, fallback, logging

Configuration (from Settings / .env)
------------------------------------
- ``GEMINI_API_KEY`` / ``GEMINI_MODEL``
- ``CLAUDE_API_KEY`` / ``CLAUDE_MODEL``
- ``DEEPSEEK_API_KEY`` / ``DEEPSEEK_MODEL``
- ``GPT_API_KEY`` / ``GPT_MODEL`` (OpenAI)
- ``AI_PROVIDER`` (gemini | claude | deepseek | openai | auto)
- Optional endpoint overrides via env:
  ``GEMINI_BASE_URL``, ``DEEPSEEK_BASE_URL``, ``OPENAI_BASE_URL``

Primary class: AIRouter
"""

from __future__ import annotations

import base64
import json
import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from pydantic import BaseModel, Field

from webmaker.core.exceptions import (
    AIError,
    AIProviderUnavailableError,
    AIResponseError,
)
from webmaker.core.logging import get_logger
from webmaker.core.prompts import load_prompt
from webmaker.core.types import AIProvider

if TYPE_CHECKING:
    from webmaker.config.settings import Settings
    from webmaker.modules.ai_cache import AICache

log = get_logger("ai_router")


# ── Constants ─────────────────────────────────────────────────────────────────

_DEFAULT_MAX_TOKENS = 4096
_DEFAULT_TEMPERATURE = 0.3
_DEFAULT_TIMEOUT_S = 120.0

# Retry policy for transient failures
_MAX_RETRIES = 3
_RETRY_BASE_DELAY_S = 1.0
_RETRY_MAX_DELAY_S = 20.0

# Default provider priority when AI_PROVIDER=auto (V2: Claude-first; Gemini last / unused)
_DEFAULT_PRIORITY: tuple[AIProvider, ...] = (
    AIProvider.CLAUDE,
    AIProvider.OPENAI,
    AIProvider.DEEPSEEK,
    AIProvider.GEMINI,
)

# Task → preferred provider affinity (used by select_provider)
_TASK_AFFINITY: dict[str, AIProvider] = {
    "business_analysis":     AIProvider.CLAUDE,
    "competitor_analysis":   AIProvider.DEEPSEEK,
    "page_copy":             AIProvider.CLAUDE,
    "meta_title":            AIProvider.CLAUDE,
    "content_generation":    AIProvider.CLAUDE,
    "website_review":        AIProvider.CLAUDE,
    "content_review":        AIProvider.DEEPSEEK,
    "qa_review":             AIProvider.CLAUDE,
    "second_opinion":        AIProvider.CLAUDE,
    "design_recommendation": AIProvider.OPENAI,
    "qa_visual_review":      AIProvider.OPENAI,
}

# Substrings that indicate a retryable provider error
_RETRYABLE_MARKERS = (
    "rate limit",
    "rate_limit",
    "429",
    "500",
    "502",
    "503",
    "504",
    "timeout",
    "timed out",
    "temporarily unavailable",
    "overloaded",
    "connection reset",
    "connection aborted",
    "server error",
    "resource_exhausted",
    "unavailable",
)


# ── Standardised response ─────────────────────────────────────────────────────

class AIResponse(BaseModel):
    """Normalised AI completion result shared by every provider.

    Callers that need metadata should use :meth:`AIRouter.request`.
    :meth:`AIRouter.complete` returns ``text`` only for convenience.
    """

    text:          str = ""
    provider:      str = ""
    model:         str = ""
    finish_reason: str = ""
    usage:         dict[str, int] = Field(default_factory=dict)
    latency_s:     float = 0.0
    retries:       int = 0
    raw:           dict[str, Any] = Field(default_factory=dict)


@dataclass
class _RequestOptions:
    """Internal request bundle passed to provider adapters.

    ``images`` is a list of filesystem paths to image files. Currently only
    the Claude adapter attaches them as vision (multimodal) content blocks;
    other providers ignore them and run text-only.
    """

    prompt:      str
    system:      str = ""
    max_tokens:  int = _DEFAULT_MAX_TOKENS
    temperature: float = _DEFAULT_TEMPERATURE
    context:     dict[str, Any] = field(default_factory=dict)
    images:      list[str] = field(default_factory=list)


# ── Vision helpers (Claude multimodal) ────────────────────────────────────────

_IMAGE_MEDIA: dict[str, str] = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif":  "image/gif",
}
_MAX_VISION_BYTES = 1_800_000  # prefer lean vision payloads


def _encode_image_for_vision(path: Path) -> tuple[str, str] | None:
    """Return ``(media_type, base64)`` or ``None`` if unusable.

    Downscales large PNGs with Pillow when available to keep Claude requests lean.
    """
    media = _IMAGE_MEDIA.get(path.suffix.lower())
    if not media:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data:
        return None

    if len(data) > _MAX_VISION_BYTES:
        try:
            from io import BytesIO
            from PIL import Image

            img = Image.open(BytesIO(data))
            img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
            # Shrink until under limit (or give up)
            for max_w in (1280, 960, 720):
                w, h = img.size
                if w > max_w:
                    ratio = max_w / float(w)
                    img = img.resize((max_w, max(1, int(h * ratio))), Image.Resampling.LANCZOS)
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=75, optimize=True)
                data = buf.getvalue()
                media = "image/jpeg"
                if len(data) <= _MAX_VISION_BYTES:
                    break
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not shrink vision image {p}: {e}", p=path.name, e=exc)
            return None

    if len(data) > _MAX_VISION_BYTES:
        log.warning(
            "Skipping oversized vision image {p} ({n} bytes)",
            p=path.name, n=len(data),
        )
        return None

    return media, base64.standard_b64encode(data).decode("ascii")


def _claude_user_content(prompt: str, images: list[str] | None) -> str | list[dict[str, Any]]:
    """Build Claude Messages API user content (text, or text + images)."""
    paths = [p for p in (images or []) if p]
    if not paths:
        return prompt

    blocks: list[dict[str, Any]] = []
    attached = 0
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            continue
        encoded = _encode_image_for_vision(path)
        if encoded is None:
            continue
        media, b64 = encoded
        # Label so Claude knows which library ref this is
        blocks.append({
            "type": "text",
            "text": f"[Design Library screenshot: {path.parent.parent.name}/{path.parent.name}]",
        })
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media,
                "data": b64,
            },
        })
        attached += 1

    if not attached:
        return prompt

    blocks.append({"type": "text", "text": prompt})
    log.info("Claude vision request with {n} image(s)", n=attached)
    return blocks


# ── Provider adapters ─────────────────────────────────────────────────────────

class _BaseProvider(ABC):
    """Abstract AI provider adapter.

    Subclass and register in :meth:`AIRouter._init_clients` to add a
    new provider without touching other modules.
    """

    name: AIProvider

    def __init__(self, api_key: str, model: str, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._client: Any = None

    @property
    def model(self) -> str:
        """Configured model name for this provider."""
        return self._model

    @abstractmethod
    def _ensure_client(self) -> Any:
        """Lazily construct and return the vendor SDK client."""

    @abstractmethod
    def _send(self, options: _RequestOptions) -> AIResponse:
        """Perform one provider API call and return a normalised response."""

    def complete(self, options: _RequestOptions) -> AIResponse:
        """Public adapter entry — ensures client then sends."""
        self._ensure_client()
        return self._send(options)


class _GeminiProvider(_BaseProvider):
    """Google Gemini adapter via ``google-genai``."""

    name = AIProvider.GEMINI

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        super().__init__(api_key, model, timeout_s=timeout_s)
        self._base_url = base_url

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google import genai
        except ImportError as exc:
            raise AIProviderUnavailableError(
                "google-genai package is not installed",
                provider="gemini",
            ) from exc

        kwargs: dict[str, Any] = {"api_key": self._api_key}
        # Optional custom endpoint (Vertex / proxy) via env
        if self._base_url:
            try:
                from google.genai import types as genai_types
                kwargs["http_options"] = genai_types.HttpOptions(base_url=self._base_url)
            except Exception:
                pass
        self._client = genai.Client(**kwargs)
        return self._client

    def _send(self, options: _RequestOptions) -> AIResponse:
        from google.genai import types as genai_types

        client = self._ensure_client()
        config_kwargs: dict[str, Any] = {
            "temperature": options.temperature,
            "max_output_tokens": options.max_tokens,
        }
        if options.system:
            config_kwargs["system_instruction"] = options.system

        t0 = time.perf_counter()
        try:
            result = client.models.generate_content(
                model=self._model,
                contents=options.prompt,
                config=genai_types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:
            raise _wrap_provider_error("gemini", exc) from exc

        latency = time.perf_counter() - t0
        text = (getattr(result, "text", None) or "").strip()
        if not text:
            # Fallback: stitch candidate parts
            try:
                parts = []
                for cand in getattr(result, "candidates", None) or []:
                    content = getattr(cand, "content", None)
                    for part in getattr(content, "parts", None) or []:
                        t = getattr(part, "text", None)
                        if t:
                            parts.append(t)
                text = "\n".join(parts).strip()
            except Exception:
                text = ""

        usage: dict[str, int] = {}
        um = getattr(result, "usage_metadata", None)
        if um is not None:
            usage = {
                "prompt_tokens":     int(getattr(um, "prompt_token_count", 0) or 0),
                "completion_tokens": int(getattr(um, "candidates_token_count", 0) or 0),
                "total_tokens":      int(getattr(um, "total_token_count", 0) or 0),
            }

        return AIResponse(
            text=text,
            provider=self.name.value,
            model=self._model,
            finish_reason="stop",
            usage=usage,
            latency_s=round(latency, 3),
        )


class _ClaudeProvider(_BaseProvider):
    """Anthropic Claude adapter via ``anthropic``."""

    name = AIProvider.CLAUDE

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:
            raise AIProviderUnavailableError(
                "anthropic package is not installed",
                provider="claude",
            ) from exc

        self._client = anthropic.Anthropic(
            api_key=self._api_key,
            timeout=self._timeout_s,
        )
        return self._client

    def _send(self, options: _RequestOptions) -> AIResponse:
        client = self._ensure_client()
        content = _claude_user_content(options.prompt, options.images)
        kwargs: dict[str, Any] = {
            "model":      self._model,
            "max_tokens": options.max_tokens,
            "temperature": options.temperature,
            "messages":   [{"role": "user", "content": content}],
        }
        if options.system:
            kwargs["system"] = options.system

        t0 = time.perf_counter()
        try:
            result = client.messages.create(**kwargs)
        except Exception as exc:
            raise _wrap_provider_error("claude", exc) from exc

        latency = time.perf_counter() - t0
        parts: list[str] = []
        for block in getattr(result, "content", None) or []:
            if getattr(block, "type", "") == "text" or hasattr(block, "text"):
                t = getattr(block, "text", "") or ""
                if t:
                    parts.append(t)
        text = "\n".join(parts).strip()

        usage: dict[str, int] = {}
        u = getattr(result, "usage", None)
        if u is not None:
            usage = {
                "prompt_tokens":     int(getattr(u, "input_tokens", 0) or 0),
                "completion_tokens": int(getattr(u, "output_tokens", 0) or 0),
                "total_tokens":      int(
                    (getattr(u, "input_tokens", 0) or 0)
                    + (getattr(u, "output_tokens", 0) or 0)
                ),
            }

        return AIResponse(
            text=text,
            provider=self.name.value,
            model=self._model,
            finish_reason=str(getattr(result, "stop_reason", "") or "stop"),
            usage=usage,
            latency_s=round(latency, 3),
            raw={"vision_images": len(options.images or [])},
        )


# TODO(gpt-5.5): add a `_OpenAIProvider(_BaseProvider)` here for GPT-5.5, mirroring
# the DeepSeek adapter (OpenAI-compatible SDK). Register it in
# `AIRouter._init_clients` and add an `AIProvider.OPENAI` enum member. WebMaker V2
# will route DesignRecommendation and the QA second-opinion to it.
class _DeepSeekProvider(_BaseProvider):
    """DeepSeek adapter via OpenAI-compatible ``openai`` SDK."""

    name = AIProvider.DEEPSEEK

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        super().__init__(api_key, model, timeout_s=timeout_s)
        self._base_url = base_url or os.getenv(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        )

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIProviderUnavailableError(
                "openai package is not installed (required for DeepSeek)",
                provider="deepseek",
            ) from exc

        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout_s,
        )
        return self._client

    def _send(self, options: _RequestOptions) -> AIResponse:
        client = self._ensure_client()
        messages: list[dict[str, str]] = []
        if options.system:
            messages.append({"role": "system", "content": options.system})
        messages.append({"role": "user", "content": options.prompt})

        t0 = time.perf_counter()
        try:
            result = client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=options.max_tokens,
                temperature=options.temperature,
            )
        except Exception as exc:
            raise _wrap_provider_error("deepseek", exc) from exc

        latency = time.perf_counter() - t0
        choice = (result.choices or [None])[0]
        text = ""
        finish = "stop"
        if choice is not None:
            msg = getattr(choice, "message", None)
            text = (getattr(msg, "content", None) or "").strip()
            finish = str(getattr(choice, "finish_reason", "") or "stop")

        usage: dict[str, int] = {}
        u = getattr(result, "usage", None)
        if u is not None:
            usage = {
                "prompt_tokens":     int(getattr(u, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
                "total_tokens":      int(getattr(u, "total_tokens", 0) or 0),
            }

        return AIResponse(
            text=text,
            provider=self.name.value,
            model=self._model,
            finish_reason=finish,
            usage=usage,
            latency_s=round(latency, 3),
        )


class _OpenAIProvider(_BaseProvider):
    """OpenAI GPT adapter via the official ``openai`` SDK.

    Used for DesignRecommendation ranking and the QA visual second opinion.
    GPT-5-family models use ``max_completion_tokens`` and omit temperature.
    """

    name = AIProvider.OPENAI

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        super().__init__(api_key, model, timeout_s=timeout_s)
        self._base_url = base_url or os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIProviderUnavailableError(
                "openai package is not installed (required for GPT)",
                provider="openai",
            ) from exc

        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout_s,
        )
        return self._client

    def _is_reasoning_model(self) -> bool:
        m = (self._model or "").lower()
        return m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3")

    def _uses_responses_api(self) -> bool:
        """GPT-5.x Pro models are Responses-API only (not chat.completions)."""
        m = (self._model or "").lower().strip()
        if m.endswith("-pro") or "-pro-" in m:
            return True
        return m in {"gpt-5.5-pro", "gpt-5.4-pro", "gpt-5.2-pro", "gpt-5-pro"}

    def _send(self, options: _RequestOptions) -> AIResponse:
        if self._uses_responses_api():
            return self._send_responses(options)
        return self._send_chat(options)

    def _send_responses(self, options: _RequestOptions) -> AIResponse:
        client = self._ensure_client()
        # Responses API prefers structured input; keep system separate when possible.
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_output_tokens": max(int(options.max_tokens or 0), 2500),
        }
        if options.system:
            kwargs["instructions"] = options.system.strip()
            kwargs["input"] = options.prompt
        else:
            kwargs["input"] = options.prompt
        # Pro/reasoning models reject custom temperature on many accounts.
        t0 = time.perf_counter()
        try:
            result = client.responses.create(**kwargs)
        except Exception as exc:
            raise _wrap_provider_error("openai", exc) from exc
        latency = time.perf_counter() - t0

        text = (getattr(result, "output_text", None) or "").strip()
        if not text:
            # Fallback: walk output items for message text
            chunks: list[str] = []
            for item in getattr(result, "output", None) or []:
                for part in getattr(item, "content", None) or []:
                    val = getattr(part, "text", None)
                    if val:
                        chunks.append(str(val))
            text = "\n".join(chunks).strip()

        status = str(getattr(result, "status", "") or "stop")
        incomplete = getattr(result, "incomplete_details", None)
        if not text and incomplete is not None:
            raise _wrap_provider_error(
                "openai",
                RuntimeError(
                    f"incomplete response from {self._model}: {incomplete}"
                ),
            )

        usage: dict[str, int] = {}
        u = getattr(result, "usage", None)
        if u is not None:
            usage = {
                "prompt_tokens": int(
                    getattr(u, "input_tokens", 0)
                    or getattr(u, "prompt_tokens", 0)
                    or 0
                ),
                "completion_tokens": int(
                    getattr(u, "output_tokens", 0)
                    or getattr(u, "completion_tokens", 0)
                    or 0
                ),
                "total_tokens": int(getattr(u, "total_tokens", 0) or 0),
            }
            if not usage["total_tokens"]:
                usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]

        return AIResponse(
            text=text,
            provider=self.name.value,
            model=self._model,
            finish_reason=status,
            usage=usage,
            latency_s=round(latency, 3),
        )

    def _send_chat(self, options: _RequestOptions) -> AIResponse:
        client = self._ensure_client()
        messages: list[dict[str, str]] = []
        if options.system:
            messages.append({"role": "system", "content": options.system})
        messages.append({"role": "user", "content": options.prompt})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if self._is_reasoning_model():
            kwargs["max_completion_tokens"] = options.max_tokens
        else:
            kwargs["max_tokens"] = options.max_tokens
            kwargs["temperature"] = options.temperature

        t0 = time.perf_counter()
        try:
            result = client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise _wrap_provider_error("openai", exc) from exc

        latency = time.perf_counter() - t0
        choice = (result.choices or [None])[0]
        text = ""
        finish = "stop"
        if choice is not None:
            msg = getattr(choice, "message", None)
            text = (getattr(msg, "content", None) or "").strip()
            finish = str(getattr(choice, "finish_reason", "") or "stop")

        usage: dict[str, int] = {}
        u = getattr(result, "usage", None)
        if u is not None:
            usage = {
                "prompt_tokens":     int(getattr(u, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
                "total_tokens":      int(getattr(u, "total_tokens", 0) or 0),
            }

        return AIResponse(
            text=text,
            provider=self.name.value,
            model=self._model,
            finish_reason=finish,
            usage=usage,
            latency_s=round(latency, 3),
        )


# Provider registry type (extensible)
_ProviderFactory = type[_BaseProvider]


# ── Error helpers ─────────────────────────────────────────────────────────────

def _wrap_provider_error(provider: str, exc: Exception) -> AIError:
    """Convert a vendor SDK exception into an AIError without leaking secrets."""
    msg = str(exc)
    # Redact anything that looks like a key
    msg = _redact_secrets(msg)
    lower = msg.lower()

    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 401 or "invalid api key" in lower or "authentication" in lower:
        return AIProviderUnavailableError(
            f"{provider} authentication failed — check API key",
            provider=provider,
        )
    if status == 429 or "rate limit" in lower or "rate_limit" in lower:
        return AIError(
            f"{provider} rate limit exceeded",
            provider=provider,
            retryable=True,
        )

    retryable = any(m in lower for m in _RETRYABLE_MARKERS)
    return AIError(
        f"{provider} request failed: {msg[:300]}",
        provider=provider,
        retryable=retryable,
    )


def _redact_secrets(text: str) -> str:
    """Strip obvious API-key-like tokens from error strings."""
    import re
    text = re.sub(r"sk-[A-Za-z0-9_\-]{10,}", "sk-***", text)
    text = re.sub(r"AIza[A-Za-z0-9_\-]{10,}", "AIza***", text)
    text = re.sub(r"key[=:]\s*\S+", "key=***", text, flags=re.I)
    return text


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, AIProviderUnavailableError):
        # Auth / missing package — do not retry
        ctx = getattr(exc, "context", {}) or {}
        if "authentication" in str(exc).lower() or "not installed" in str(exc).lower():
            return False
        return False
    if isinstance(exc, AIError):
        return bool((getattr(exc, "context", {}) or {}).get("retryable"))
    lower = str(exc).lower()
    return any(m in lower for m in _RETRYABLE_MARKERS)


# ── Main router ───────────────────────────────────────────────────────────────

class AIRouter:
    """Routes AI requests to the appropriate provider.

    Responsibilities:
    - Load API keys and model names from Settings / .env.
    - Initialise provider adapters lazily.
    - Select providers by preference, task affinity, and availability.
    - Retry transient failures with exponential backoff.
    - Fall back across the provider chain when appropriate.
    - Return consistent ``str`` / :class:`AIResponse` outputs.
    - Never log API keys or prompt contents.

    Args:
        settings: Application settings with API keys and model names.
    """

    def __init__(
        self,
        settings: "Settings",
        *,
        cache: "AICache | None" = None,
        enable_cache: bool = True,
    ) -> None:
        self._settings = settings
        self._providers: dict[AIProvider, bool] = {}
        self._clients: dict[AIProvider, _BaseProvider] = {}
        self._max_retries = _MAX_RETRIES

        from webmaker.modules.ai_cache import AICache

        if cache is not None:
            self._cache = cache
        else:
            self._cache = AICache(
                Path(settings.cache_dir),
                enabled=enable_cache,
            )

        self._load_provider_config()
        self._init_clients()
        log.info(
            "AIRouter initialised — available providers: {p} cache={c}",
            p=[k.value for k, v in self._providers.items() if v],
            c=self._cache.enabled,
        )

    # ── Configuration ──────────────────────────────────────────────────────────

    def _load_provider_config(self) -> None:
        """Read API keys from settings and mark providers as available.

        A provider is available if its API key is a non-empty string.
        Does NOT make any network calls.
        """
        self._providers = {
            AIProvider.GEMINI:   bool(str(self._settings.gemini_api_key or "").strip()),
            AIProvider.CLAUDE:   bool(str(self._settings.claude_api_key or "").strip()),
            AIProvider.DEEPSEEK: bool(str(self._settings.deepseek_api_key or "").strip()),
            AIProvider.OPENAI:   bool(str(getattr(self._settings, "gpt_api_key", "") or "").strip()),
        }

    def _init_clients(self) -> None:
        """Construct adapter instances for every configured provider."""
        self._clients.clear()

        if self._providers.get(AIProvider.GEMINI):
            self._clients[AIProvider.GEMINI] = _GeminiProvider(
                api_key=self._settings.gemini_api_key.strip(),
                model=self._settings.gemini_model,
                base_url=os.getenv("GEMINI_BASE_URL") or None,
            )

        if self._providers.get(AIProvider.CLAUDE):
            self._clients[AIProvider.CLAUDE] = _ClaudeProvider(
                api_key=self._settings.claude_api_key.strip(),
                model=self._settings.claude_model,
            )

        if self._providers.get(AIProvider.DEEPSEEK):
            self._clients[AIProvider.DEEPSEEK] = _DeepSeekProvider(
                api_key=self._settings.deepseek_api_key.strip(),
                model=self._settings.deepseek_model,
                base_url=os.getenv("DEEPSEEK_BASE_URL") or None,
            )

        if self._providers.get(AIProvider.OPENAI):
            self._clients[AIProvider.OPENAI] = _OpenAIProvider(
                api_key=str(self._settings.gpt_api_key).strip(),
                model=str(getattr(self._settings, "gpt_model", "") or "gpt-5.5-pro"),
                base_url=os.getenv("OPENAI_BASE_URL") or None,
            )

    def available_providers(self) -> list[AIProvider]:
        """Return the list of providers that have API keys configured.

        Returns:
            List of AIProvider enum values.
        """
        return [p for p, ok in self._providers.items() if ok]

    def is_available(self, provider: AIProvider) -> bool:
        """Check whether *provider* is configured.

        Args:
            provider: Provider to check.

        Returns:
            True if an API key is present for this provider.
        """
        return self._providers.get(provider, False)

    def get_model_name(self, provider: AIProvider) -> str:
        """Return the configured model name for *provider*."""
        client = self._clients.get(provider)
        if client is not None:
            return client.model
        mapping = {
            AIProvider.GEMINI:   self._settings.gemini_model,
            AIProvider.CLAUDE:   self._settings.claude_model,
            AIProvider.DEEPSEEK: self._settings.deepseek_model,
            AIProvider.OPENAI:   getattr(self._settings, "gpt_model", "") or "",
        }
        return mapping.get(provider, "")

    @property
    def cache(self) -> "AICache":
        """Return the AI response cache instance."""
        return self._cache

    def invalidate_cache(self, key: str | None = None) -> int:
        """Invalidate one cached response or the entire AI cache."""
        return self._cache.invalidate(key)

    def load_prompt(self, name: str, **variables: Any) -> str:
        """Load a prompt template from ``prompts/`` (PromptLoader)."""
        return load_prompt(name, **variables)

    # ── Request routing ────────────────────────────────────────────────────────

    def complete(
        self,
        prompt: str,
        *,
        provider: AIProvider = AIProvider.AUTO,
        system: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Send a completion request and return the response text.

        This is the primary interface used by all WebMaker modules.

        Args:
            prompt:      User message / prompt text.
            provider:    Preferred provider; AUTO selects automatically.
            system:      Optional system message.
            max_tokens:  Token limit override.
            temperature: Sampling temperature override.

        Returns:
            The provider's response text.

        Raises:
            AIProviderUnavailableError: If no configured provider is reachable.
            AIError: If all providers fail.
            AIResponseError: If the response text is empty.
        """
        response = self.request(
            prompt,
            provider=provider,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.text

    def request(
        self,
        prompt: str,
        *,
        provider: AIProvider = AIProvider.AUTO,
        system: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
        context: dict[str, Any] | None = None,
        task: str = "complete",
        allow_fallback: bool = True,
        use_cache: bool = True,
        images: list[str] | None = None,
    ) -> AIResponse:
        """Send a completion request and return a standardised :class:`AIResponse`.

        Args:
            prompt:         User / task prompt.
            provider:       Preferred provider (AUTO = select automatically).
            system:         Optional system instruction.
            max_tokens:     Max completion tokens.
            temperature:    Sampling temperature.
            context:        Optional structured context (appended to prompt).
            task:           Task id for logging / provider affinity.
            allow_fallback: Try other providers if the preferred one fails.
            use_cache:      When True, consult/store the AI cache (disabled for
                            ``health_check`` automatically).
            images:         Optional filesystem paths to images for vision
                            (Claude only; other providers ignore).

        Returns:
            Normalised AIResponse.

        Raises:
            AIProviderUnavailableError: No providers configured.
            AIError / AIResponseError: On hard failure.
        """
        if not prompt or not str(prompt).strip():
            raise AIError("Prompt must be a non-empty string")

        image_paths = [str(p) for p in (images or []) if p]
        options = _RequestOptions(
            prompt=self._merge_context(str(prompt), context),
            system=system or "",
            max_tokens=max_tokens if max_tokens is not None else _DEFAULT_MAX_TOKENS,
            temperature=(
                temperature if temperature is not None else _DEFAULT_TEMPERATURE
            ),
            context=dict(context or {}),
            images=image_paths,
        )

        # Prefer Claude when vision images are attached.
        preferred = provider
        if image_paths and preferred == AIProvider.AUTO:
            preferred = AIProvider.CLAUDE

        chain = self._build_fallback_chain(
            preferred if preferred != AIProvider.AUTO else self.select_provider(task)
        )
        if not allow_fallback:
            chain = chain[:1]
        # Vision only works on Claude — put Claude first if images present.
        if image_paths and AIProvider.CLAUDE in chain:
            chain = [AIProvider.CLAUDE] + [p for p in chain if p != AIProvider.CLAUDE]

        if not chain:
            raise AIProviderUnavailableError(
                "No AI providers have API keys configured. "
                "Set GEMINI_API_KEY, CLAUDE_API_KEY, DEEPSEEK_API_KEY, or GPT_API_KEY in .env"
            )

        cache_enabled = (
            use_cache
            and self._cache.enabled
            and (task or "") not in ("health_check",)
            and not image_paths  # never cache multimodal vision requests
        )

        errors: list[str] = []
        for chosen in chain:
            self._log_request(
                chosen,
                task,
                prompt_tokens=max(1, len(options.prompt) // 4),
            )
            try:
                # Cache lookup before provider call
                key = self._cache_key_for(chosen, options) if cache_enabled else ""
                hit = self._cache.get(key) if key else None
                if hit is not None:
                    return AIResponse(
                        text=hit.response_text,
                        provider=hit.provider or chosen.value,
                        model=hit.model or self.get_model_name(chosen),
                        finish_reason="cache",
                        usage={},
                        latency_s=0.0,
                        retries=0,
                        raw={"cached": True, "cache_key": key[:16]},
                    )

                response = self._call_with_retries(chosen, options, task=task)
                self._validate_response(response)
                if key:
                    self._cache.set(
                        key,
                        provider=response.provider,
                        model=response.model,
                        response_text=response.text,
                        response={"finish_reason": response.finish_reason},
                    )
                log.info(
                    "AI request completed — provider={p} model={m} "
                    "latency={t:.2f}s retries={r} chars={n}",
                    p=response.provider,
                    m=response.model,
                    t=response.latency_s,
                    r=response.retries,
                    n=len(response.text),
                )
                return response
            except (AIError, AIResponseError) as exc:
                errors.append(f"{chosen.value}: {exc}")
                log.warning(
                    "AI provider {p} failed for task={t}: {e}",
                    p=chosen.value, t=task, e=_redact_secrets(str(exc)),
                )
                if not allow_fallback:
                    raise
                continue

        raise AIError(
            "All AI providers failed. " + " | ".join(errors[:3]),
            attempts=len(errors),
        )

    def analyze_content(
        self,
        content: str,
        task: str,
        *,
        provider: AIProvider = AIProvider.AUTO,
    ) -> dict[str, Any]:
        """Ask an AI provider to analyse *content* for *task*.

        Expects a JSON object in the model response.  Falls back to a
        ``{"raw": text}`` wrapper if parsing fails.

        Args:
            content:  Text or HTML content to analyse.
            task:     High-level description of the analysis task.
            provider: Preferred provider.

        Returns:
            Dict with structured analysis output.

        Raises:
            AIError: If the provider request fails.
        """
        system = self.load_prompt("analyze_content")
        prompt = (
            f"Task: {task}\n\n"
            f"Content to analyse:\n{content}\n\n"
            "Return a JSON object with your analysis."
        )
        response = self.request(
            prompt,
            provider=provider,
            system=system,
            task=task or "analyze_content",
            temperature=0.2,
        )
        parsed = self._parse_json_object(response.text)
        if parsed is None:
            return {"raw": response.text, "provider": response.provider}
        parsed.setdefault("provider", response.provider)
        return parsed

    def generate_text(
        self,
        task: str,
        context: dict[str, Any],
        *,
        provider: AIProvider = AIProvider.AUTO,
    ) -> str:
        """Generate text for a structured task from a context dict.

        Args:
            task:     Task identifier (e.g. ``"page_copy"``, ``"meta_title"``).
            context:  Variables injected into the task prompt.
            provider: Preferred provider.

        Returns:
            Generated text string.

        Raises:
            AIError: If the provider request fails.
        """
        system = self.load_prompt("generate_text")
        prompt = (
            f"Task: {task}\n\n"
            f"Context (JSON):\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
            "Produce the requested text only."
        )
        return self.complete(
            prompt,
            provider=provider,
            system=system,
            temperature=0.4,
        )

    # ── Provider selection ─────────────────────────────────────────────────────

    def select_provider(
        self,
        task: str,
        *,
        preferred: AIProvider = AIProvider.AUTO,
    ) -> AIProvider:
        """Choose the best available provider for *task*.

        Priority:
        1. Explicit *preferred* (if available)
        2. Task affinity map
        3. Settings ``AI_PROVIDER``
        4. Default priority chain among available providers

        Args:
            task:      Task identifier for provider affinity logic.
            preferred: Caller preference; may be overridden if unavailable.

        Returns:
            A concrete AIProvider (never AUTO).

        Raises:
            AIProviderUnavailableError: If no provider is available.
        """
        available = self.available_providers()
        if not available:
            raise AIProviderUnavailableError(
                "No AI providers configured",
            )

        # 1. Explicit preferred
        if preferred != AIProvider.AUTO and preferred in available:
            return preferred

        # 2. Task affinity
        affinity = _TASK_AFFINITY.get((task or "").lower().strip())
        if affinity and affinity in available:
            return affinity

        # 3. Settings preference
        configured = self._settings_preferred()
        if configured and configured in available:
            return configured

        # 4. Default priority
        for p in _DEFAULT_PRIORITY:
            if p in available:
                return p

        return available[0]

    def health_check(self, provider: AIProvider) -> bool:
        """Perform a lightweight liveness check against *provider*.

        Sends a minimal request to verify the provider is reachable and
        the API key is valid.

        Args:
            provider: Provider to test (must not be AUTO).

        Returns:
            True if the provider responds successfully.
        """
        if provider == AIProvider.AUTO:
            return False
        if not self.is_available(provider):
            return False
        try:
            response = self.request(
                "Reply with OK",
                provider=provider,
                system="Respond with exactly: OK",
                max_tokens=8,
                temperature=0.0,
                task="health_check",
                allow_fallback=False,
            )
            return bool(response.text.strip())
        except Exception as exc:
            log.warning(
                "Health check failed for {p}: {e}",
                p=provider.value,
                e=_redact_secrets(str(exc)),
            )
            return False

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _build_fallback_chain(self, preferred: AIProvider) -> list[AIProvider]:
        """Return an ordered list of providers to try.

        Args:
            preferred: The caller's preferred provider (never AUTO ideally).

        Returns:
            List starting with *preferred* (if available), followed by
            other available providers in priority order.
        """
        if preferred == AIProvider.AUTO:
            preferred = self._settings_preferred() or _DEFAULT_PRIORITY[0]

        available = self.available_providers()
        chain: list[AIProvider] = []

        if preferred in available:
            chain.append(preferred)

        # Settings order, then default priority
        ordered = []
        cfg = self._settings_preferred()
        if cfg:
            ordered.append(cfg)
        ordered.extend(_DEFAULT_PRIORITY)

        for p in ordered:
            if p in available and p not in chain:
                chain.append(p)

        for p in available:
            if p not in chain:
                chain.append(p)

        return chain

    def _settings_preferred(self) -> AIProvider | None:
        """Parse Settings.ai_provider into an AIProvider, or None for auto."""
        raw = str(getattr(self._settings, "ai_provider", "auto") or "auto").strip().lower()
        if raw in ("", "auto"):
            return None
        # Accept "gpt" as an alias for the OpenAI provider.
        if raw == "gpt":
            raw = "openai"
        try:
            return AIProvider(raw)
        except ValueError:
            log.warning("Unknown AI_PROVIDER value: {v}", v=raw)
            return None

    def _call_with_retries(
        self,
        provider: AIProvider,
        options: _RequestOptions,
        *,
        task: str,
    ) -> AIResponse:
        """Invoke *provider* with exponential backoff on retryable errors."""
        client = self._clients.get(provider)
        if client is None:
            raise AIProviderUnavailableError(
                f"Provider {provider.value} is not initialised",
                provider=provider.value,
            )

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                log.info(
                    "AI request started — provider={p} model={m} task={t} attempt={a}",
                    p=provider.value,
                    m=client.model,
                    t=task,
                    a=attempt + 1,
                )
                response = client.complete(options)
                response.retries = attempt
                return response
            except Exception as exc:
                last_exc = exc
                wrapped = exc if isinstance(exc, AIError) else _wrap_provider_error(
                    provider.value, exc
                )
                if attempt >= self._max_retries or not _is_retryable(wrapped):
                    if isinstance(wrapped, AIError):
                        raise wrapped
                    raise AIError(str(wrapped), provider=provider.value) from exc

                delay = min(
                    _RETRY_MAX_DELAY_S,
                    _RETRY_BASE_DELAY_S * (2 ** attempt) + random.uniform(0, 0.5),
                )
                log.warning(
                    "AI retry scheduled — provider={p} attempt={a}/{m} "
                    "delay={d:.1f}s reason={r}",
                    p=provider.value,
                    a=attempt + 1,
                    m=self._max_retries + 1,
                    d=delay,
                    r=_redact_secrets(str(wrapped))[:160],
                )
                time.sleep(delay)

        assert last_exc is not None
        if isinstance(last_exc, AIError):
            raise last_exc
        raise AIError(
            f"{provider.value} failed after retries: {_redact_secrets(str(last_exc))[:300]}",
            provider=provider.value,
        ) from last_exc

    def _validate_response(self, response: AIResponse) -> None:
        """Reject empty / invalid provider responses."""
        if not isinstance(response, AIResponse):
            raise AIResponseError("Provider returned a non-AIResponse object")
        if not response.text or not response.text.strip():
            raise AIResponseError(
                f"Empty response from {response.provider or 'unknown'}",
                provider=response.provider,
                model=response.model,
            )

    def _cache_key_for(self, provider: AIProvider, options: _RequestOptions) -> str:
        """Build SHA-256 cache key for provider + request options."""
        from webmaker.modules.ai_cache import AICache

        return AICache.make_key(
            model=self.get_model_name(provider),
            provider=provider.value,
            system=options.system,
            prompt=options.prompt,
            context=options.context,
        )

    @staticmethod
    def _merge_context(prompt: str, context: dict[str, Any] | None) -> str:
        if not context:
            return prompt
        try:
            blob = json.dumps(context, ensure_ascii=False, indent=2, default=str)
        except (TypeError, ValueError):
            blob = str(context)
        return f"{prompt}\n\n---\nAdditional context:\n{blob}"

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any] | None:
        raw = (text or "").strip()
        if not raw:
            return None
        # Strip fences
        import re
        fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw, re.DOTALL)
        if fence:
            raw = fence.group(1)
        else:
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end > start:
                raw = raw[start : end + 1]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _log_request(
        self,
        provider: AIProvider,
        task: str,
        prompt_tokens: int,
    ) -> None:
        """Record a request in the audit log without logging prompt content.

        Args:
            provider:      Provider that will handle the request.
            task:          Task identifier.
            prompt_tokens: Estimated input token count.
        """
        log.debug(
            "AI request — provider={p} model={m} task={t} tokens≈{n}",
            p=provider.value,
            m=self.get_model_name(provider),
            t=task,
            n=prompt_tokens,
        )


# ── Public re-exports for convenience ─────────────────────────────────────────

__all__ = [
    "AIRouter",
    "AIResponse",
]
