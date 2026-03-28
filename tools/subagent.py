"""Subagent tool — spawn a nested agent loop with permission checks."""

from __future__ import annotations

import json
from typing import Any

from tools.registry import register_tool, get_all_tool_schemas, get_tool_schemas
from core.permissions import is_blocked, is_risky


async def _subagent_permission_check(name: str, tool_input: dict[str, Any]) -> bool | str:
    """Permission check for subagents — risky commands are denied (no UI prompt)."""
    if is_blocked(name, tool_input):
        return "BLOCKED: This command is not allowed for safety reasons."
    if is_risky(name, tool_input):
        return "DENIED: Risky commands require user confirmation and cannot run in a subagent."
    return True


async def handle_subagent(tool_input: dict[str, Any]) -> str:
    """Spawn a sub-agent with its own context."""
    prompt = tool_input.get("prompt", "")
    if not prompt:
        return json.dumps({"error": "No prompt provided"})

    # Import here to avoid circular imports
    from core import agent

    system = (
        "You are a sub-agent working on a specific task. "
        "Complete the task and return a clear, concise result. "
        "You have access to the same tools as the parent agent."
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    # Respect parent's skill tool filter
    from core.session import get_active_tool_filter
    tool_filter = get_active_tool_filter()
    tools = get_tool_schemas(tool_filter) if tool_filter else get_all_tool_schemas()

    # Collect text output
    output_parts: list[str] = []

    async def on_text(text: str) -> None:
        output_parts.append(text)

    async def on_tool_start(name: str, tool_input: dict[str, Any]) -> None:
        return None

    async def on_tool_end(ctx: Any, result: str) -> None:
        pass

    await agent.run(
        messages=messages,
        tools=tools,
        system=system,
        on_text=on_text,
        on_tool_start=on_tool_start,
        on_tool_end=on_tool_end,
        on_permission_check=_subagent_permission_check,
        max_turns=20,  # Lower limit for sub-agents
    )

    result = "".join(output_parts)
    return json.dumps({"result": result[:10000]})


register_tool(
    name="Agent",
    aliases=["subagent"],
    description="Spawn a sub-agent to handle a specific sub-task independently. Useful for decomposing complex tasks.",
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The task for the sub-agent to complete",
            },
        },
        "required": ["prompt"],
    },
    handler=handle_subagent,
)
