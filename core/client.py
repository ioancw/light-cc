"""Anthropic SDK client singleton and multi-model provider access."""

from __future__ import annotations

import os

from anthropic import AsyncAnthropic

_client: AsyncAnthropic | None = None


def get_client() -> AsyncAnthropic:
    """Get the raw Anthropic SDK client (for direct SDK usage like title generation)."""
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in environment or .env")
        _client = AsyncAnthropic(api_key=api_key)
    return _client


def get_provider_for_model(model: str):
    """Get the appropriate ModelProvider for a given model name.

    Returns a ModelProvider instance that can stream messages for the model.
    Falls back through registered providers: Anthropic -> OpenAI -> Ollama.
    """
    from core.providers.registry import get_provider
    return get_provider(model)
