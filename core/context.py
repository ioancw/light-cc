"""Context management — token counting + auto-compression of older messages."""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from core.client import get_client
from core.config import settings

logger = logging.getLogger(__name__)

# Snapshots stored before compression for rollback safety.
# Keyed by cid (conversation id). Only the most recent snapshot is kept.
_compression_snapshots: dict[str, list[dict[str, Any]]] = {}


def snapshot_before_compression(cid: str, messages: list[dict[str, Any]]) -> None:
    """Save a deep copy of messages before compression."""
    _compression_snapshots[cid] = copy.deepcopy(messages)


def rollback_compression(cid: str) -> list[dict[str, Any]] | None:
    """Restore messages from the pre-compression snapshot. Returns None if no snapshot."""
    return _compression_snapshots.pop(cid, None)


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
    # Strip non-API fields (timestamp, model, etc.) before sending to the API
    _api_keys = {"role", "content"}
    clean = [{k: v for k, v in m.items() if k in _api_keys} for m in messages]
    try:
        result = await client.messages.count_tokens(
            model=settings.model,
            system=system,
            messages=clean,
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

    # Snapshot before compression for rollback safety
    try:
        from core.session import _current_cid
        cid = _current_cid.get("")
        if cid:
            snapshot_before_compression(cid, messages)
    except Exception:
        pass  # Non-critical -- proceed with compression anyway

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


async def get_context_breakdown(
    messages: list[dict[str, Any]],
    system: str,
    tools: list[dict[str, Any]] | None = None,
    *,
    project_config: str = "",
    rules_text: str = "",
    memory_context: str = "",
    skill_prompt: str = "",
) -> dict[str, Any]:
    """Return a token-count breakdown of each context component.

    Useful for the ``/context`` command so users can see what is consuming
    their context window.
    """
    max_tokens = settings.max_context_tokens

    # Estimate individual component sizes
    system_base_tokens = _estimate_tokens(system)
    project_config_tokens = _estimate_tokens(project_config) if project_config else 0
    rules_tokens = _estimate_tokens(rules_text) if rules_text else 0
    memory_tokens = _estimate_tokens(memory_context) if memory_context else 0
    skill_tokens = _estimate_tokens(skill_prompt) if skill_prompt else 0
    tools_tokens = _estimate_tokens(json.dumps(tools)) if tools else 0
    messages_tokens = _estimate_message_tokens(messages)

    total = await count_message_tokens(messages, system, tools)

    return {
        "system_prompt_tokens": system_base_tokens,
        "project_config_tokens": project_config_tokens,
        "rules_tokens": rules_tokens,
        "memory_tokens": memory_tokens,
        "skill_tokens": skill_tokens,
        "tools_tokens": tools_tokens,
        "messages_tokens": messages_tokens,
        "total_tokens": total,
        "max_tokens": max_tokens,
        "usage_pct": round(total / max_tokens * 100, 1) if max_tokens else 0,
    }


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
