"""Ollama provider -- connects to local Ollama instance via HTTP.

Uses Ollama's /api/chat endpoint with streaming. Translates tool schemas
to Ollama's function calling format (OpenAI-compatible).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator

import httpx

from core.providers.base import ModelProvider, StreamEvent

logger = logging.getLogger(__name__)

# Ollama models are usually short names like "llama3", "mistral", "codellama"
# We match anything that doesn't look like an Anthropic or OpenAI model
_KNOWN_PREFIXES_TO_EXCLUDE = ("claude-", "gpt-", "o1-", "o3-", "o4-")


class OllamaProvider(ModelProvider):
    name = "ollama"

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    def supports_model(self, model: str) -> bool:
        return not any(model.startswith(p) for p in _KNOWN_PREFIXES_TO_EXCLUDE)

    def translate_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert Anthropic tool schemas to Ollama/OpenAI function format."""
        ollama_tools = []
        for tool in tools:
            ollama_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            })
        return ollama_tools

    async def stream_messages(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[StreamEvent]:
        ollama_messages = self._convert_messages(system, messages)
        ollama_tools = self.translate_tools(tools) if tools else []

        payload: dict[str, Any] = {
            "model": model,
            "messages": ollama_messages,
            "stream": True,
            "options": {"num_predict": max_tokens},
        }
        if ollama_tools:
            payload["tools"] = ollama_tools

        current_index = 0
        text_started = False

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue

                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg = chunk.get("message", {})

                    # Text content
                    content = msg.get("content", "")
                    if content:
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
                            delta={"type": "text_delta", "text": content},
                        )

                    # Tool calls
                    tool_calls = msg.get("tool_calls", [])
                    for i, tc in enumerate(tool_calls):
                        if text_started:
                            yield StreamEvent(type="content_block_stop", index=current_index)
                            current_index += 1
                            text_started = False

                        func = tc.get("function", {})
                        tc_id = f"ollama_call_{current_index + i}"
                        yield StreamEvent(
                            type="content_block_start",
                            index=current_index + i,
                            content_block={
                                "type": "tool_use",
                                "id": tc_id,
                                "name": func.get("name", ""),
                            },
                        )
                        args = func.get("arguments", {})
                        if isinstance(args, dict):
                            args = json.dumps(args)
                        yield StreamEvent(
                            type="content_block_delta",
                            index=current_index + i,
                            delta={"type": "input_json_delta", "partial_json": args},
                        )
                        yield StreamEvent(type="content_block_stop", index=current_index + i)

                    # Done
                    if chunk.get("done"):
                        if text_started:
                            yield StreamEvent(type="content_block_stop", index=current_index)

                        # Usage info
                        prompt_tokens = chunk.get("prompt_eval_count", 0)
                        completion_tokens = chunk.get("eval_count", 0)
                        if prompt_tokens or completion_tokens:
                            yield StreamEvent(
                                type="message_complete",
                                usage={
                                    "input": prompt_tokens,
                                    "output": completion_tokens,
                                },
                            )

    def _convert_messages(self, system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert Anthropic-format messages to Ollama/OpenAI format."""
        ollama_messages: list[dict[str, Any]] = []

        if system:
            ollama_messages.append({"role": "system", "content": system})

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if isinstance(content, str):
                ollama_messages.append({"role": role, "content": content})
            elif isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif block.get("type") == "tool_result":
                            ollama_messages.append({
                                "role": "tool",
                                "content": block.get("content", ""),
                            })
                if text_parts:
                    ollama_messages.append({"role": role, "content": "\n".join(text_parts)})

        return ollama_messages

    async def count_tokens(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
    ) -> int:
        """Rough token estimate for Ollama models."""
        total_chars = len(system) + len(json.dumps(messages)) + len(json.dumps(tools))
        return total_chars // 4
