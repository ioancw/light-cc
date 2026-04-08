"""OpenAI provider -- translates between Anthropic and OpenAI formats.

Converts tool schemas from Anthropic's format to OpenAI's function calling
format, and translates OpenAI's streaming events back to StreamEvents.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator

from core.providers.base import ModelProvider, StreamEvent

logger = logging.getLogger(__name__)

_OPENAI_PREFIXES = ("gpt-", "o1-", "o3-", "o4-")

_client = None


def _get_client():
    global _client
    if _client is None:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise RuntimeError("openai package not installed. Run: pip install openai")
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set in environment")
        _client = AsyncOpenAI(api_key=api_key)
    return _client


class OpenAIProvider(ModelProvider):
    name = "openai"

    def supports_model(self, model: str) -> bool:
        return any(model.startswith(p) for p in _OPENAI_PREFIXES)

    def translate_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert Anthropic tool schemas to OpenAI function calling format."""
        openai_tools = []
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            })
        return openai_tools

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

        # Convert messages from Anthropic format to OpenAI format
        oai_messages = self._convert_messages(system, messages)
        oai_tools = self.translate_tools(tools) if tools else None

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": oai_messages,
            "stream": True,
        }
        if oai_tools:
            kwargs["tools"] = oai_tools

        # Track state for translating chunks to StreamEvents
        current_index = 0
        tool_call_map: dict[int, dict[str, Any]] = {}
        text_started = False

        async with client.chat.completions.create(**kwargs) as stream:
            async for chunk in stream:
                choice = chunk.choices[0] if chunk.choices else None
                if not choice:
                    continue

                delta = choice.delta

                # Text content
                if delta.content:
                    if not text_started:
                        yield StreamEvent(
                            type="content_block_start",
                            index=current_index,
                            content_block={"type": "text", "id": None, "name": None},
                        )
                        text_started = True
                    yield StreamEvent(
                        type="content_block_delta",
                        index=current_index,
                        delta={"type": "text_delta", "text": delta.content},
                    )

                # Tool calls
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        tc_index = tc.index
                        if tc_index not in tool_call_map:
                            # Close text block if open
                            if text_started:
                                yield StreamEvent(type="content_block_stop", index=current_index)
                                current_index += 1
                                text_started = False

                            tool_call_map[tc_index] = {
                                "id": tc.id or f"call_{tc_index}",
                                "name": tc.function.name if tc.function and tc.function.name else "",
                            }
                            yield StreamEvent(
                                type="content_block_start",
                                index=current_index + tc_index,
                                content_block={
                                    "type": "tool_use",
                                    "id": tool_call_map[tc_index]["id"],
                                    "name": tool_call_map[tc_index]["name"],
                                },
                            )

                        if tc.function and tc.function.arguments:
                            yield StreamEvent(
                                type="content_block_delta",
                                index=current_index + tc_index,
                                delta={"type": "input_json_delta", "partial_json": tc.function.arguments},
                            )

                # Finish reason
                if choice.finish_reason:
                    if text_started:
                        yield StreamEvent(type="content_block_stop", index=current_index)
                    for tc_index in tool_call_map:
                        yield StreamEvent(type="content_block_stop", index=current_index + tc_index)

            # Usage from the final chunk
            if chunk and chunk.usage:
                yield StreamEvent(
                    type="message_complete",
                    usage={
                        "input": chunk.usage.prompt_tokens,
                        "output": chunk.usage.completion_tokens,
                    },
                )

    def _convert_messages(self, system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert Anthropic-format messages to OpenAI format."""
        oai_messages: list[dict[str, Any]] = []

        if system:
            oai_messages.append({"role": "system", "content": system})

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if isinstance(content, str):
                oai_messages.append({"role": role, "content": content})
            elif isinstance(content, list):
                # Anthropic content blocks -> OpenAI format
                text_parts = []
                tool_calls = []
                tool_results = []

                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            })
                        elif block.get("type") == "tool_result":
                            tool_results.append(block)

                if role == "assistant":
                    msg_dict: dict[str, Any] = {"role": "assistant"}
                    if text_parts:
                        msg_dict["content"] = "\n".join(text_parts)
                    if tool_calls:
                        msg_dict["tool_calls"] = tool_calls
                    oai_messages.append(msg_dict)
                elif role == "user" and tool_results:
                    for tr in tool_results:
                        oai_messages.append({
                            "role": "tool",
                            "tool_call_id": tr.get("tool_use_id", ""),
                            "content": tr.get("content", ""),
                        })
                else:
                    if text_parts:
                        oai_messages.append({"role": role, "content": "\n".join(text_parts)})

        return oai_messages

    async def count_tokens(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
    ) -> int:
        """Rough token estimate for OpenAI models."""
        total_chars = len(system) + len(json.dumps(messages)) + len(json.dumps(tools))
        return total_chars // 4
