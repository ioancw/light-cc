"""Provider registry -- resolves model names to providers."""

from __future__ import annotations

import logging
from typing import Any

from core.providers.base import ModelProvider

logger = logging.getLogger(__name__)

_providers: list[ModelProvider] = []
_initialized = False


def _ensure_initialized() -> None:
    """Register built-in providers on first access."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    # Always register Anthropic (the native provider)
    try:
        from core.providers.anthropic import AnthropicProvider
        _providers.append(AnthropicProvider())
    except Exception as e:
        logger.debug(f"Anthropic provider not available: {e}")

    # Register OpenAI if the package is available
    try:
        import openai  # noqa: F401
        from core.providers.openai import OpenAIProvider
        _providers.append(OpenAIProvider())
    except ImportError:
        pass

    # Register Ollama (always available, uses httpx)
    try:
        from core.providers.ollama import OllamaProvider
        _providers.append(OllamaProvider())
    except Exception:
        pass


def register_provider(provider: ModelProvider) -> None:
    """Register a custom provider. Inserted at the front for priority."""
    _providers.insert(0, provider)


def get_provider(model: str) -> ModelProvider:
    """Find the provider that handles the given model name.

    Checks providers in order. The Anthropic provider is always registered
    and acts as the default for claude-* models.

    Raises RuntimeError if no provider supports the model.
    """
    _ensure_initialized()

    for provider in _providers:
        if provider.supports_model(model):
            return provider

    raise RuntimeError(
        f"No provider found for model '{model}'. "
        f"Available providers: {[p.name for p in _providers]}"
    )


def list_providers() -> list[str]:
    """List registered provider names."""
    _ensure_initialized()
    return [p.name for p in _providers]
