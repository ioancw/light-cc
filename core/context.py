"""Context management — token counting + auto-compression of older messages."""

from __future__ import annotations

import json
import logging
from typing import Any

from core.client import get_client
from core.config import settings

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate fallback: ~4 chars per token."""
    return len(text) // 4


def _estimate_message_tokens(messages: list[dict[str, Any]], system: str = "") -> int:
    """Estimate total tokens using char count fallback."""
    total = _estimate_tokens(system)
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += _estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += _estimate_tokens(json.dumps(block))
    return total


async def count_message_tokens(
    messages: list[dict[str, Any]],
    system: str,
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Count tokens using the Anthropic SDK, with fallback to estimation."""
    client = get_client()
    try:
        result = await client.messages.count_tokens(
            model=settings.model,
            system=system,
            messages=messages,
            tools=tools or [],
        )
        return result.input_tokens
    except Exception as e:
        logger.debug(f"SDK token counting failed, using estimate: {e}")
        return _estimate_message_tokens(messages, system)


async def compress_if_needed(
    messages: list[dict[str, Any]],
    system: str,
    tools: list[dict[str, Any]] | None = None,
    keep_recent: int = 4,
) -> list[dict[str, Any]]:
    """Compress older messages if approaching the context limit.

    Args:
        messages: Full conversation history.
        system: System prompt (counted but not modified).
        tools: Tool schemas (counted for token budget).
        keep_recent: Number of recent turns to keep intact.

    Returns:
        Potentially compressed messages list.
    """
    max_tokens = settings.max_context_tokens
    threshold = int(max_tokens * settings.compression_threshold)
    current = await count_message_tokens(messages, system, tools)

    if current < threshold:
        return messages

    logger.info(f"Context at {current} tokens (threshold {threshold}), compressing...")

    # Split: old messages to compress + recent messages to keep
    keep_count = keep_recent * 2
    if len(messages) <= keep_count:
        return messages

    old_messages = messages[:-keep_count]
    recent_messages = messages[-keep_count:]

    summary_text = _format_for_summary(old_messages)

    client = get_client()
    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Summarize this conversation context concisely in 2-3 paragraphs. "
                        "Preserve key decisions, file paths mentioned, data context, "
                        "and any important facts. Be factual and specific.\n\n"
                        f"{summary_text}"
                    ),
                }
            ],
        )
        summary = response.content[0].text
    except Exception as e:
        logger.error(f"Compression failed: {e}")
        return messages

    compressed = [
        {
            "role": "user",
            "content": f"[Prior conversation summary]: {summary}",
        },
        {
            "role": "assistant",
            "content": "Understood. I have the context from our prior conversation.",
        },
    ] + recent_messages

    new_count = await count_message_tokens(compressed, system, tools)
    logger.info(f"Compressed {current} -> {new_count} tokens")
    return compressed


def _format_for_summary(messages: list[dict[str, Any]]) -> str:
    """Format messages into readable text for summarization."""
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(f"{role}: {content[:500]}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(f"{role}: {block['text'][:500]}")
                    elif block.get("type") == "tool_use":
                        parts.append(f"{role}: [called tool {block.get('name')}]")
                    elif block.get("type") == "tool_result":
                        parts.append(f"{role}: [tool result: {str(block.get('content', ''))[:200]}]")
    return "\n".join(parts[:100])
