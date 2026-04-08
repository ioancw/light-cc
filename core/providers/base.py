"""Abstract base class for model providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator


@dataclass
class StreamEvent:
    """Normalized streaming event matching Anthropic's event types.

    Other providers translate their native events into this format so
    the agent loop (core/agent.py) can remain provider-agnostic.
    """

    type: str
    # content_block_start
    index: int | None = None
    content_block: dict[str, Any] | None = None
    # content_block_delta
    delta: dict[str, Any] | None = None
    # message_delta (usage)
    usage: dict[str, int] | None = None


class ModelProvider(ABC):
    """Base class for model providers (Anthropic, OpenAI, Ollama, etc.)."""

    name: str = "base"

    @abstractmethod
    async def stream_messages(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[StreamEvent]:
        """Stream a messages API call, yielding normalized StreamEvents.

        The events should match Anthropic's streaming format:
        - content_block_start (type=text or type=tool_use)
        - content_block_delta (type=text_delta or type=input_json_delta)
        - content_block_stop
        - message_delta (with usage)
        """
        ...

    @abstractmethod
    def translate_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Translate Anthropic-format tool schemas to this provider's format.

        For Anthropic provider, this is a no-op. For OpenAI, converts to
        function calling format. Returns the translated schemas.
        """
        ...

    @abstractmethod
    async def count_tokens(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
    ) -> int:
        """Estimate token count for the given context."""
        ...

    def supports_model(self, model: str) -> bool:
        """Check if this provider handles the given model name."""
        return False
