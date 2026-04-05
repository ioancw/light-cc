"""The agentic tool-use loop — the heart of Light CC.

No DAGs, no state machines. Just:
  1. Call Claude with messages + tools
  2. Execute any tool_use blocks
  3. Feed results back
  4. Repeat until Claude responds with pure text
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Awaitable, Callable

from core.client import get_client
from core.config import settings
from core.context import compress_if_needed
from core.session import current_session_get
from core.telemetry import (
    record_request, record_tokens, record_tool_call,
    observe_agent_loop, async_trace_span, audit_tool_call,
)
from core.usage import record_usage
from tools.registry import execute_tool

logger = logging.getLogger(__name__)


async def run(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system: str,
    on_text: Callable[[str], Awaitable[None]],
    on_tool_start: Callable[[str, dict[str, Any]], Awaitable[Any]],
    on_tool_end: Callable[[Any, str], Awaitable[None]],
    on_permission_check: Callable[[str, dict[str, Any]], Awaitable[bool | str]] | None = None,
    max_turns: int | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Run the agentic loop with streaming.

    Args:
        messages: Conversation history (mutated in place).
        tools: Claude API tool schemas.
        system: System prompt.
        on_text: Called with each text delta for streaming.
        on_tool_start: Called when a tool is about to execute. Returns context for on_tool_end.
        on_tool_end: Called when a tool finishes. Receives context from on_tool_start + result.
        on_permission_check: Called before tool execution. Return True to allow,
            or a string error message to deny.
        max_turns: Max loop iterations (default from config).
        model: Model override (default from config).

    Returns:
        Updated messages list.
    """
    client = get_client()
    remaining = max_turns or settings.max_turns
    active_model = model or settings.model

    # Record request metric
    try:
        from core.session import current_session_get
        _req_uid = current_session_get("user_id") or "default"
        record_request(_req_uid, active_model)
    except Exception as e:
        logger.debug(f"Request metric recording failed: {e}")

    while remaining > 0:
        remaining -= 1
        _turn_start = time.monotonic()

        # Compress context if approaching limit
        messages = await compress_if_needed(messages, system, tools)

        # Stream the response
        assistant_content: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []

        # Accumulate text and tool_use input JSON fragments per block index
        text_buffers: dict[int, list[str]] = {}
        tool_input_buffers: dict[int, list[str]] = {}
        tool_block_map: dict[int, dict[str, Any]] = {}

        # Buffer text deltas — we flush them after we know whether tools follow
        pending_text: list[str] = []
        stream_usage: dict[str, int] | None = None

        import anthropic as _anthropic
        _max_retries = 3
        for _attempt in range(_max_retries):
            try:
                async with client.messages.stream(
                    model=active_model,
                    max_tokens=settings.max_tokens,
                    system=system,
                    messages=messages,
                    tools=tools if tools else [],
                ) as stream:
                    async for event in stream:
                        if event.type == "content_block_start":
                            block = event.content_block
                            idx = event.index
                            if block.type == "text":
                                text_buffers[idx] = []
                            elif block.type == "tool_use":
                                tool_block_map[idx] = {
                                    "id": block.id,
                                    "name": block.name,
                                }
                                tool_input_buffers[idx] = []

                        elif event.type == "content_block_delta":
                            delta = event.delta
                            idx = event.index
                            if delta.type == "text_delta":
                                # Buffer text — don't send to UI yet
                                pending_text.append(delta.text)
                                if idx in text_buffers:
                                    text_buffers[idx].append(delta.text)
                            elif delta.type == "input_json_delta":
                                if idx in tool_input_buffers:
                                    tool_input_buffers[idx].append(delta.partial_json)

                        elif event.type == "content_block_stop":
                            idx = event.index
                            if idx in text_buffers:
                                full_text = "".join(text_buffers[idx])
                                assistant_content.append({"type": "text", "text": full_text})
                            elif idx in tool_block_map:
                                raw = "".join(tool_input_buffers.get(idx, []))
                                try:
                                    parsed_input = json.loads(raw) if raw else {}
                                except json.JSONDecodeError:
                                    parsed_input = {}

                                info = tool_block_map[idx]
                                tool_call = {
                                    "id": info["id"],
                                    "name": info["name"],
                                    "input": parsed_input,
                                }
                                tool_calls.append(tool_call)
                                assistant_content.append(
                                    {
                                        "type": "tool_use",
                                        "id": info["id"],
                                        "name": info["name"],
                                        "input": parsed_input,
                                    }
                                )

                    # Capture usage from the final message (inside async with)
                    try:
                        final_msg = await stream.get_final_message()
                        if final_msg and final_msg.usage:
                            stream_usage = {
                                "input": final_msg.usage.input_tokens,
                                "output": final_msg.usage.output_tokens,
                            }
                    except Exception as e:
                        logger.debug(f"Failed to get final message usage: {e}")
                break  # success — exit retry loop
            except (_anthropic.RateLimitError, _anthropic.InternalServerError) as exc:
                if _attempt < _max_retries - 1:
                    delay = min(1.0 * 2 ** _attempt + random.uniform(0, 0.5), 30.0)
                    logger.warning(f"API error (attempt {_attempt + 1}/{_max_retries}): {exc}. Retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    # Reset buffers for retry
                    assistant_content.clear()
                    tool_calls.clear()
                    text_buffers.clear()
                    tool_input_buffers.clear()
                    tool_block_map.clear()
                    pending_text.clear()
                    stream_usage = None
                else:
                    raise
            except _anthropic.APIStatusError as exc:
                if exc.status_code == 529 and _attempt < _max_retries - 1:
                    delay = min(1.0 * 2 ** _attempt + random.uniform(0, 0.5), 30.0)
                    logger.warning(f"API overloaded (attempt {_attempt + 1}/{_max_retries}): {exc}. Retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    assistant_content.clear()
                    tool_calls.clear()
                    text_buffers.clear()
                    tool_input_buffers.clear()
                    tool_block_map.clear()
                    pending_text.clear()
                    stream_usage = None
                else:
                    raise

        # Record token usage and metrics (outside async with, using captured data)
        if stream_usage:
            record_tokens(active_model, stream_usage["input"], stream_usage["output"])
            try:
                from core.session import current_session_get
                _uid = current_session_get("user_id") or "default"
                _cid = current_session_get("conversation_id")
                await record_usage(
                    user_id=_uid,
                    model=active_model,
                    input_tokens=stream_usage["input"],
                    output_tokens=stream_usage["output"],
                    conversation_id=_cid,
                )
            except Exception as e:
                logger.debug(f"Usage recording failed: {e}")

        # Append assistant message
        messages.append({"role": "assistant", "content": assistant_content})

        # If no tool calls, flush text and we're done
        if not tool_calls:
            for chunk in pending_text:
                await on_text(chunk)
            observe_agent_loop(active_model, time.monotonic() - _turn_start)
            break

        # Tool calls present — execute tools FIRST (Steps appear in UI),
        # then flush any text AFTER so it appears below the Steps.
        async def _run_tool(call: dict[str, Any]) -> dict[str, Any]:
            # Rate limit check for tool calls
            from core.rate_limit import check_rate_limit
            from core.session import current_session_get
            _user_id = current_session_get("user_id") or "default"
            allowed, reason = check_rate_limit(_user_id, "tool_call")
            if not allowed:
                ctx = await on_tool_start(call["name"], call["input"])
                await on_tool_end(ctx, json.dumps({"error": reason}))
                return {
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": json.dumps({"error": reason}),
                    "is_error": True,
                }

            # Permission check before execution
            if on_permission_check:
                allowed = await on_permission_check(call["name"], call["input"])
                if allowed is not True:
                    error_msg = allowed if isinstance(allowed, str) else "User denied this action"
                    ctx = await on_tool_start(call["name"], call["input"])
                    await on_tool_end(ctx, json.dumps({"error": error_msg}))
                    return {
                        "type": "tool_result",
                        "tool_use_id": call["id"],
                        "content": json.dumps({"error": error_msg}),
                        "is_error": True,
                    }

            # Fire PreToolUse hooks (non-zero exit blocks the tool)
            from core.hooks import has_hooks, fire_hooks
            if has_hooks("PreToolUse"):
                hook_results = await fire_hooks(
                    "PreToolUse",
                    {"tool_name": call["name"], "tool_input": call["input"]},
                    tool_name=call["name"],
                )
                for hr in hook_results:
                    if hr.exit_code != 0:
                        error_msg = hr.stdout or hr.stderr or "Blocked by PreToolUse hook"
                        ctx = await on_tool_start(call["name"], call["input"])
                        await on_tool_end(ctx, json.dumps({"error": error_msg}))
                        return {
                            "type": "tool_result",
                            "tool_use_id": call["id"],
                            "content": json.dumps({"error": error_msg}),
                            "is_error": True,
                        }

            ctx = await on_tool_start(call["name"], call["input"])
            _tool_t0 = time.monotonic()
            result = await execute_tool(call["name"], call["input"])
            _tool_dur = time.monotonic() - _tool_t0
            record_tool_call(call["name"], _tool_dur)
            audit_tool_call(
                user_id=current_session_get("user_id") or "unknown",
                tool_name=call["name"],
                tool_input=call["input"],
                success="error" not in result.lower()[:100],
                duration=_tool_dur,
            )
            await on_tool_end(ctx, result)

            # Fire PostToolUse hooks
            if has_hooks("PostToolUse"):
                await fire_hooks(
                    "PostToolUse",
                    {"tool_name": call["name"], "tool_input": call["input"], "result": result},
                    tool_name=call["name"],
                )

            # Detect error results and set is_error so Claude knows the tool failed
            is_error = False
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict) and "error" in parsed:
                    is_error = True
            except (json.JSONDecodeError, TypeError):
                pass

            tool_result: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": call["id"],
                "content": result,
            }
            if is_error:
                tool_result["is_error"] = True
            return tool_result

        tool_results = await asyncio.gather(*[_run_tool(c) for c in tool_calls])

        # Flush buffered text AFTER tools — so Steps appear above text in UI
        for chunk in pending_text:
            await on_text(chunk)

        # Append tool results as user message
        messages.append({"role": "user", "content": list(tool_results)})

        # Record turn duration
        observe_agent_loop(active_model, time.monotonic() - _turn_start)

    return messages
