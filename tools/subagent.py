"""Unified sub-agent tool -- spawn typed agents, run in background, continue/resume.

Merges the former Agent + BackgroundAgent + CheckBackground tools into one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from tools.registry import register_tool, get_all_tool_schemas, get_tool_schemas
from core.permissions import is_blocked, is_risky
from core.agent_types import get_agent_type, EXCLUDED_TOOLS, list_agent_types

logger = logging.getLogger(__name__)

# ── State tracking ──

MAX_AGENTS = 100
AGENT_TTL_SECONDS = 3600


@dataclass
class SubAgentState:
    agent_id: str
    agent_type: str
    status: str  # "running" | "completed" | "failed"
    messages: list[dict[str, Any]] = field(default_factory=list)
    result: str | None = None
    created_at: float = 0.0
    description: str = ""


_active_agents: dict[str, SubAgentState] = {}

# Per-session callbacks for pushing notifications to the UI (set by ws_router.py)
_notify_callbacks: dict[str, Callable[[str, str], Awaitable[None]]] = {}


def set_notification_callback(cb: Callable[[str, str], Awaitable[None]], *, session_id: str | None = None) -> None:
    if session_id:
        _notify_callbacks[session_id] = cb
    else:
        _notify_callbacks["_default"] = cb


def remove_notification_callback(session_id: str) -> None:
    _notify_callbacks.pop(session_id, None)


def _get_notify_callback() -> Callable[[str, str], Awaitable[None]] | None:
    from core.session import _current_session_id
    sid = _current_session_id.get(None)
    if sid and sid in _notify_callbacks:
        return _notify_callbacks[sid]
    return _notify_callbacks.get("_default")


def _cleanup_agents() -> None:
    """Remove completed/failed agents older than TTL."""
    now = time.time()
    expired = [
        aid
        for aid, a in _active_agents.items()
        if a.status in ("completed", "failed")
        and now - a.created_at > AGENT_TTL_SECONDS
    ]
    for aid in expired:
        del _active_agents[aid]


# ── Permission check ──

async def _subagent_permission_check(name: str, tool_input: dict[str, Any]) -> bool | str:
    """Permission check for sub-agents -- risky commands denied (no UI prompt)."""
    if is_blocked(name, tool_input):
        return "BLOCKED: This command is not allowed for safety reasons."
    if is_risky(name, tool_input):
        return "DENIED: Risky commands require user confirmation and cannot run in a sub-agent."
    return True


# ── Tool resolution ──

def _get_tools_for_type(agent_type_name: str, tool_names: list[str] | None = None) -> list[dict[str, Any]]:
    """Get tool schemas for a given agent type, excluding Agent tools."""
    if tool_names:
        # Explicit tool list (e.g. from skill context=fork)
        filtered = [n for n in tool_names if n not in EXCLUDED_TOOLS]
        return get_tool_schemas(filtered)

    agent_type = get_agent_type(agent_type_name)
    if agent_type and agent_type.tool_names:
        return get_tool_schemas(agent_type.tool_names)

    # Default: all tools minus Agent tools
    all_tools = get_all_tool_schemas()
    return [t for t in all_tools if t["name"] not in EXCLUDED_TOOLS]


# ── Core runner ──

async def run_subagent(
    prompt: str,
    system: str | None = None,
    tool_names: list[str] | None = None,
    agent_type: str = "default",
    max_turns: int | None = None,
    max_result_chars: int | None = None,
    model: str | None = None,
    existing_messages: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Run a sub-agent loop and return (text_output, messages).

    Args:
        prompt: The task for the sub-agent.
        system: Optional system prompt override.
        tool_names: Optional explicit tool list (overrides agent_type tools).
        agent_type: Agent type name for tool/prompt selection.
        max_turns: Maximum loop iterations (default from agent type).
        max_result_chars: Truncate result to this many chars (default from agent type).
        model: Model override (default from agent type).
        existing_messages: Resume with preserved messages (for continue/resume).

    Returns:
        Tuple of (text_output, messages_list).
    """
    from core import agent

    at = get_agent_type(agent_type)

    if system is None:
        system = at.system_prompt if at else "Complete the task and return a clear, concise result."

    if max_turns is None:
        max_turns = at.max_turns if at else 20
    if max_result_chars is None:
        max_result_chars = at.max_result_chars if at else 10000
    if model is None and at:
        model = at.model
    timeout = at.timeout_seconds if at else 300

    # Build messages -- either continue existing or start fresh
    if existing_messages is not None:
        messages = existing_messages
        messages.append({"role": "user", "content": prompt})
    else:
        messages = [{"role": "user", "content": prompt}]

    tools = _get_tools_for_type(agent_type, tool_names)

    output_parts: list[str] = []

    async def on_text(text: str) -> None:
        output_parts.append(text)

    async def on_tool_start(name: str, tool_input: dict[str, Any]) -> None:
        return None

    async def on_tool_end(ctx: Any, result: str) -> None:
        pass

    try:
        messages = await asyncio.wait_for(
            agent.run(
                messages=messages,
                tools=tools,
                system=system,
                on_text=on_text,
                on_tool_start=on_tool_start,
                on_tool_end=on_tool_end,
                on_permission_check=_subagent_permission_check,
                max_turns=max_turns,
                model=model,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        result = f"Sub-agent timed out after {timeout}s. Partial output: {''.join(output_parts)[:1000]}"
        return result, messages

    result = "".join(output_parts)[:max_result_chars]
    return result, messages


# ── Background runner ──

async def _run_background_agent(state: SubAgentState, prompt: str, **kwargs: Any) -> None:
    """Run a sub-agent in the background and update state on completion."""
    try:
        result, messages = await run_subagent(prompt=prompt, **kwargs)
        state.status = "completed"
        state.result = result
        state.messages = messages

        _notify_callback = _get_notify_callback()
        if _notify_callback:
            label = state.description or f"Agent ({state.agent_type})"
            await _notify_callback(
                state.agent_id,
                f"**{label} completed**\n\n{result[:2000]}",
            )
    except Exception as e:
        state.status = "failed"
        state.result = str(e)

        _notify_callback = _get_notify_callback()
        if _notify_callback:
            label = state.description or f"Agent ({state.agent_type})"
            await _notify_callback(
                state.agent_id,
                f"**{label} failed**\n\nError: {e}",
            )


# ── Tool handler ──

async def handle_agent(tool_input: dict[str, Any]) -> str:
    """Unified Agent tool handler -- spawn, continue, or run in background."""
    prompt = tool_input.get("prompt", "")
    if not prompt:
        return json.dumps({"error": "No prompt provided"})

    agent_id = tool_input.get("agent_id")
    agent_type = tool_input.get("agent_type", "default")
    run_in_background = tool_input.get("run_in_background", False)
    description = tool_input.get("description", "")

    # Validate agent type
    if not agent_id and agent_type not in [at.name for at in list_agent_types()]:
        return json.dumps({"error": f"Unknown agent type: {agent_type}. Available: {[at.name for at in list_agent_types()]}"})

    _cleanup_agents()

    # ── Continue existing agent ──
    if agent_id:
        state = _active_agents.get(agent_id)
        if not state:
            return json.dumps({"error": f"Agent '{agent_id}' not found"})
        if state.status == "running":
            return json.dumps({"error": f"Agent '{agent_id}' is still running"})

        state.status = "running"

        if run_in_background:
            asyncio.create_task(_run_background_agent(
                state, prompt,
                agent_type=state.agent_type,
                existing_messages=state.messages,
            ))
            return json.dumps({
                "status": "resumed_in_background",
                "agent_id": agent_id,
            })

        result, messages = await run_subagent(
            prompt=prompt,
            agent_type=state.agent_type,
            existing_messages=state.messages,
        )
        state.status = "completed"
        state.result = result
        state.messages = messages
        return json.dumps({"result": result, "agent_id": agent_id})

    # ── Spawn new agent ──
    if len(_active_agents) >= MAX_AGENTS:
        return json.dumps({"error": "Too many active agents. Wait for some to complete."})

    new_id = uuid.uuid4().hex[:8]
    state = SubAgentState(
        agent_id=new_id,
        agent_type=agent_type,
        status="running",
        created_at=time.time(),
        description=description,
    )
    _active_agents[new_id] = state

    if run_in_background:
        asyncio.create_task(_run_background_agent(
            state, prompt,
            agent_type=agent_type,
        ))
        return json.dumps({
            "status": "started",
            "agent_id": new_id,
            "agent_type": agent_type,
            "message": "Agent is running in the background. You will be notified when it completes.",
        })

    # Foreground -- block until complete
    try:
        result, messages = await run_subagent(
            prompt=prompt,
            agent_type=agent_type,
        )
        state.status = "completed"
        state.result = result
        state.messages = messages
        return json.dumps({"result": result, "agent_id": new_id})
    except Exception as e:
        state.status = "failed"
        state.result = str(e)
        return json.dumps({"error": str(e), "agent_id": new_id})


async def handle_agent_status(tool_input: dict[str, Any]) -> str:
    """Check the status of sub-agents."""
    agent_id = tool_input.get("agent_id", "")

    if not agent_id:
        agents = [
            {
                "agent_id": a.agent_id,
                "type": a.agent_type,
                "status": a.status,
                "description": a.description,
            }
            for a in _active_agents.values()
        ]
        return json.dumps({"agents": agents})

    state = _active_agents.get(agent_id)
    if not state:
        return json.dumps({"error": f"Agent '{agent_id}' not found"})

    return json.dumps({
        "agent_id": state.agent_id,
        "type": state.agent_type,
        "status": state.status,
        "description": state.description,
        "result": state.result,
    })


# ── Register tools ──

_type_names = [at.name for at in list_agent_types()]

register_tool(
    name="Agent",
    aliases=["subagent", "BackgroundAgent", "background_agent"],
    description=(
        "Spawn a sub-agent to handle a complex task independently in a separate context. "
        "Use for: parallelizing independent work, isolating large tasks from the main conversation, "
        "or delegating specialized work (research, code exploration, planning). "
        "Typed agents have focused tool sets and system prompts: "
        "explorer (file discovery), planner (architecture), coder (implementation), "
        "researcher (web search + analysis), default (all tools). "
        "Use run_in_background=true to continue working while the agent runs. "
        "Resume a previous agent by passing its agent_id."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The task or follow-up message for the agent",
            },
            "agent_type": {
                "type": "string",
                "enum": _type_names,
                "description": "Type of agent to spawn (default: 'default')",
            },
            "agent_id": {
                "type": "string",
                "description": "Resume a previously spawned agent by ID",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "If true, return immediately and notify on completion",
            },
            "description": {
                "type": "string",
                "description": "Short label for the task (shown in notifications)",
            },
        },
        "required": ["prompt"],
    },
    handler=handle_agent,
)

register_tool(
    name="AgentStatus",
    aliases=["CheckBackground", "check_background"],
    description=(
        "Check the status of sub-agents. "
        "Call with no agent_id to list all active/completed agents, "
        "or with an agent_id to get the result of a specific agent."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "Agent ID to check (omit to list all)",
            },
        },
    },
    handler=handle_agent_status,
)
