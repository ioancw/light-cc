"""Multi-model provider abstraction.

Providers translate between Light CC's internal format (Anthropic-native)
and each model API's format. The Anthropic provider is a thin pass-through;
others (OpenAI, Ollama) translate tool schemas and streaming events.
"""

from core.providers.base import ModelProvider, StreamEvent
from core.providers.registry import get_provider, register_provider

__all__ = ["ModelProvider", "StreamEvent", "get_provider", "register_provider"]
