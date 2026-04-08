"""Anthropic provider -- thin wrapper around the native SDK.

Since Light CC's internal format is already Anthropic-native, this provider
is mostly a pass-through that adapts the SDK's streaming events into
StreamEvent dataclasses.
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from core.providers.base import ModelProvider, StreamEvent

# Models this provider handles
_ANTHROPIC_PREFIXES = ("claude-",)

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in environment or .env")
        _client = AsyncAnthropic(api_key=api_key)
    return _client


class AnthropicProvider(ModelProvider):
    name = "anthropic"

    def supports_model(self, model: str) -> bool:
        return any(model.startswith(p) for p in _ANTHROPIC_PREFIXES)

    def translate_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Already in Anthropic format -- no translation needed
        return tools

    async def stream_messages(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[StreamEvent]:
        client = _get_client()
        async with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools if tools else [],
        ) as stream:
            async for event in stream:
                yield self._translate_event(event)

            # Yield final usage from the completed message
            try:
                final_msg = await stream.get_final_message()
                if final_msg and final_msg.usage:
                    yield StreamEvent(
                        type="message_complete",
                        usage={
                            "input": final_msg.usage.input_tokens,
                            "output": final_msg.usage.output_tokens,
                        },
                    )
            except Exception:
                pass

    def _translate_event(self, event: Any) -> StreamEvent:
        """Convert an Anthropic SDK event to a StreamEvent."""
        if event.type == "content_block_start":
            block = event.content_block
            return StreamEvent(
                type="content_block_start",
                index=event.index,
                content_block={
                    "type": block.type,
                    "id": getattr(block, "id", None),
                    "name": getattr(block, "name", None),
                },
            )
        elif event.type == "content_block_delta":
            delta = event.delta
            delta_dict: dict[str, Any] = {"type": delta.type}
            if hasattr(delta, "text"):
                delta_dict["text"] = delta.text
            if hasattr(delta, "partial_json"):
                delta_dict["partial_json"] = delta.partial_json
            return StreamEvent(
                type="content_block_delta",
                index=event.index,
                delta=delta_dict,
            )
        elif event.type == "content_block_stop":
            return StreamEvent(type="content_block_stop", index=event.index)
        else:
            return StreamEvent(type=event.type)

    async def count_tokens(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
    ) -> int:
        """Estimate tokens using Anthropic's token counting."""
        client = _get_client()
        try:
            result = await client.messages.count_tokens(
                model="claude-sonnet-4-6-20250514",
                system=system,
                messages=messages,
                tools=tools if tools else [],
            )
            return result.input_tokens
        except Exception:
            # Rough estimate: ~4 chars per token
            import json
            total_chars = len(system) + len(json.dumps(messages)) + len(json.dumps(tools))
            return total_chars // 4
