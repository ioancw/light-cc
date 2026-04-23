"""Agent tool -- in-conversation subagent delegation, matching Claude Code's model.

The main agent emits an ``Agent`` tool call with ``{subagent_type, prompt,
description}``. A fresh agent loop runs in isolated context with the subagent's
own system prompt + tool filter, and returns a single message back to the
caller as the tool result. The parent conversation never sees the subagent's
intermediate tool calls.

Lookup order for ``subagent_type``:
  1. User-owned ``AgentDefinition`` (scoped by session user_id). Routes through
     ``run_agent_once`` so each delegation creates a tracked ``AgentRun`` row.
  2. Builtin ``AgentType`` (explorer, planner, coder, researcher, default).
     Runs ephemerally -- no DB record.

Nested delegation is allowed but capped at depth 2 via ``Agent`` being excluded
from the subagent's own tool set (see ``EXCLUDED_TOOLS``).

``Task`` is preserved as an alias of ``Agent`` for back-compat with CC <2.1.63.
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
from core.agent_types import AgentType, get_agent_type, EXCLUDED_TOOLS, list_agent_types

logger = logging.getLogger(__name__)


# ── Background-run state ────────────────────────────────────────────────

MAX_AGENTS = 100
AGENT_TTL_SECONDS = 3600


@dataclass
class SubAgentState:
    agent_id: str
    subagent_type: str
    status: str  # "running" | "completed" | "failed"
    result: str | None = None
    created_at: float = 0.0
    description: str = ""
    # ── Resumption state (R2: Agent teams + SendMessage) ──────────────
    # Stored when the initial Agent dispatch runs so SendMessage can
    # continue the same sub-conversation. Tenant isolation: SendMessage
    # only resumes if the calling user_id + parent_session_id match.
    user_id: str | None = None
    parent_session_id: str | None = None
    parent_conversation_id: str | None = None
    agent_def_id: str | None = None  # set when the dispatch resolved a user agent
    builtin_type_name: str | None = None  # set when the dispatch resolved a builtin
    messages: list[dict[str, Any]] = field(default_factory=list)
    model_override: str | None = None
    # ``done_event`` resolves when the initial dispatch finishes (relevant
    # for background runs). ``lock`` serialises concurrent SendMessage
    # calls against the same sub-conversation -- the messages list is
    # not safe for concurrent mutation.
    done_event: asyncio.Event | None = None
    lock: asyncio.Lock | None = None


_active_agents: dict[str, SubAgentState] = {}
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
    now = time.time()
    expired = [
        aid for aid, a in _active_agents.items()
        if a.status in ("completed", "failed") and now - a.created_at > AGENT_TTL_SECONDS
    ]
    for aid in expired:
        del _active_agents[aid]


# ── Permission check (stricter for subagents) ───────────────────────────

async def _subagent_permission_check(name: str, tool_input: dict[str, Any]) -> bool | str:
    if is_blocked(name, tool_input):
        return "BLOCKED: This command is not allowed for safety reasons."
    if is_risky(name, tool_input):
        return "DENIED: Risky commands require user confirmation and cannot run in a sub-agent."
    return True


# ── Resolution: AgentDefinition first, then builtin AgentType ────────────

async def _resolve_agent_definition(name: str, user_id: str | None):
    """Return the user's ``AgentDefinition`` for ``name`` if one exists and is
    enabled. Returns ``None`` when absent so the caller can fall back to
    builtin ``AgentType``. Never raises for DB errors -- just logs and falls back.
    """
    if not user_id:
        return None
    try:
        from core.agent_crud import get_agent_by_name
        agent = await get_agent_by_name(name, user_id)
        if agent and agent.enabled:
            return agent
    except Exception as e:
        logger.warning(f"Failed to resolve user agent '{name}': {e}")
    return None


def _get_tools_for_agent_type(at: AgentType | None) -> list[dict[str, Any]]:
    """Pick the tool schema set for a builtin AgentType, minus excluded tools."""
    if at and at.tool_names:
        return get_tool_schemas(at.tool_names)
    return [t for t in get_all_tool_schemas() if t["name"] not in EXCLUDED_TOOLS]


# ── Execution paths ─────────────────────────────────────────────────────

async def _run_via_definition(
    agent_def,
    prompt: str,
    parent_session_id: str | None,
    *,
    prior_messages: list[dict[str, Any]] | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Run a user-defined AgentDefinition. Returns ``(result_text, run_id, messages)``.

    Delegates to ``run_agent_once`` so each Agent invocation produces an
    ``AgentRun`` row for observability. The parent conversation is already the
    record of the delegation, so we don't persist a child conversation.

    ``prior_messages`` lets SendMessage continue an existing sub-conversation
    by appending the new prompt to the captured history; ``capture_messages``
    is always on here so the caller can stash the updated list back into
    ``SubAgentState`` for the next continuation.
    """
    from core.agent_runner import run_agent_once
    result = await run_agent_once(
        agent_def, prompt,
        trigger_type="subagent",
        parent_session_id=parent_session_id,
        persist_conversation=False,
        prior_messages=prior_messages,
        capture_messages=True,
    )
    captured = result.messages or []
    if result.status == "failed":
        return (f"Subagent failed: {result.error or 'unknown error'}", result.run_id, captured)
    return (result.result_text or "(no output)", result.run_id, captured)


async def run_subagent(
    *,
    prompt: str,
    system: str,
    tool_names: list[str] | None = None,
    model: str | None = None,
    max_turns: int = 20,
    timeout: int = 300,
) -> tuple[str, list[dict[str, Any]]]:
    """Low-level helper for non-Agent call sites that want an isolated agent run
    with a custom system prompt (skill context=fork, eval_optimize).

    Returns ``(result_text, messages)`` where ``messages`` is the final
    conversation list. Kept distinct from the ``Agent`` tool because these
    callers drive system/tools directly rather than resolving an AgentDefinition.
    """
    from core import agent

    tools = get_tool_schemas(tool_names) if tool_names else [
        t for t in get_all_tool_schemas() if t["name"] not in EXCLUDED_TOOLS
    ]
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    output_parts: list[str] = []

    async def on_text(text: str) -> None:
        output_parts.append(text)

    async def on_tool_start(n: str, i: dict[str, Any]) -> None:
        return None

    async def on_tool_end(ctx: Any, result: str) -> None:
        pass

    try:
        final_messages = await asyncio.wait_for(
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
        return f"Sub-agent timed out after {timeout}s. Partial output: {''.join(output_parts)[:1000]}", messages

    return "".join(output_parts), (final_messages or messages)


async def _run_via_builtin(
    at: AgentType | None,
    prompt: str,
    model_override: str | None,
    *,
    prior_messages: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Run a builtin AgentType ephemerally (no DB record).

    Returns ``(result_text, messages)``. ``prior_messages`` lets SendMessage
    continue an existing sub-conversation by seeding the loop with the
    captured history before appending the new prompt.
    """
    from core import agent

    system = at.system_prompt if at else (
        "You are a sub-agent working on a specific task. "
        "Complete the task and return a clear, concise result."
    )
    max_turns = at.max_turns if at else 20
    max_result_chars = at.max_result_chars if at else 10000
    model = model_override or (at.model if at else None)
    timeout = at.timeout_seconds if at else 300
    tools = _get_tools_for_agent_type(at)

    if prior_messages is not None:
        messages: list[dict[str, Any]] = [
            *prior_messages, {"role": "user", "content": prompt},
        ]
    else:
        messages = [{"role": "user", "content": prompt}]
    output_parts: list[str] = []

    async def on_text(text: str) -> None:
        output_parts.append(text)

    async def on_tool_start(n: str, i: dict[str, Any]) -> None:
        return None

    async def on_tool_end(ctx: Any, result: str) -> None:
        pass

    # ``agent.run`` rebinds its local ``messages`` after repair_tool_use_pairs,
    # so we have to capture its return value -- the caller's list reference
    # alone won't see the appended assistant turns.
    try:
        final_messages = await asyncio.wait_for(
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
        return (
            f"Sub-agent timed out after {timeout}s. "
            f"Partial output: {''.join(output_parts)[:1000]}",
            messages,
        )

    return "".join(output_parts)[:max_result_chars], (final_messages or messages)


# ── Tool handler ────────────────────────────────────────────────────────

async def handle_task(tool_input: dict[str, Any]) -> str:
    """Agent tool handler -- CC-compliant delegation entry point.

    Schema: ``{subagent_type, prompt, description?, run_in_background?, model?}``.
    ``description`` is optional (CC compat); missing / empty → "Subagent run".
    """
    subagent_type = tool_input.get("subagent_type") or tool_input.get("agent_type") or "default"
    prompt = tool_input.get("prompt", "")
    description = tool_input.get("description") or "Subagent run"
    run_in_background = bool(tool_input.get("run_in_background", False))
    model_override = tool_input.get("model")

    if not prompt:
        return json.dumps({"error": "No prompt provided"})

    try:
        from core.session import current_session_get, _current_session_id, _current_cid
        user_id = current_session_get("user_id")
        parent_session_id = _current_session_id.get(None)
        parent_conversation_id = _current_cid.get(None) or None
    except Exception:
        user_id = None
        parent_session_id = None
        parent_conversation_id = None

    # Resolve: user agent first, then builtin type.
    agent_def = await _resolve_agent_definition(subagent_type, user_id)
    at = get_agent_type(subagent_type) if agent_def is None else None

    if agent_def is None and at is None:
        builtins = [x.name for x in list_agent_types()]
        return json.dumps({
            "error": (
                f"Unknown subagent_type: '{subagent_type}'. "
                f"Checked your custom agents and builtins ({builtins})."
            )
        })

    _cleanup_agents()
    new_id = uuid.uuid4().hex[:8]
    state = SubAgentState(
        agent_id=new_id,
        subagent_type=subagent_type,
        status="running",
        created_at=time.time(),
        description=description,
        user_id=user_id,
        parent_session_id=parent_session_id,
        parent_conversation_id=parent_conversation_id,
        agent_def_id=agent_def.id if agent_def is not None else None,
        builtin_type_name=(at.name if at is not None else None),
        model_override=model_override,
        done_event=asyncio.Event(),
        lock=asyncio.Lock(),
    )
    _active_agents[new_id] = state

    async def _run_foreground() -> str:
        try:
            if agent_def is not None:
                result, run_id, captured = await _run_via_definition(
                    agent_def, prompt, parent_session_id,
                )
                state.messages = captured
                state.status = "completed"
                state.result = result
                return json.dumps({
                    "result": result, "agent_id": new_id, "run_id": run_id,
                    "subagent_type": subagent_type,
                })
            result, captured = await _run_via_builtin(at, prompt, model_override)
            state.messages = captured
            state.status = "completed"
            state.result = result
            return json.dumps({
                "result": result, "agent_id": new_id,
                "subagent_type": subagent_type,
            })
        except Exception as e:
            state.status = "failed"
            state.result = str(e)
            return json.dumps({"error": str(e), "agent_id": new_id})
        finally:
            if state.done_event is not None:
                state.done_event.set()

    if run_in_background:
        async def _bg() -> None:
            payload_json = await _run_foreground()
            cb = _get_notify_callback()
            if cb:
                label = description or f"Subagent ({subagent_type})"
                payload = json.loads(payload_json)
                summary = payload.get("result") or payload.get("error") or ""
                status = state.status
                await cb(new_id, f"**{label} {status}**\n\n{summary[:2000]}")

        asyncio.create_task(_bg())
        return json.dumps({
            "status": "started",
            "agent_id": new_id,
            "subagent_type": subagent_type,
            "message": "Subagent running in background. You'll be notified when it completes.",
        })

    return await _run_foreground()


async def handle_send_message(tool_input: dict[str, Any]) -> str:
    """SendMessage tool handler -- continue a sub-conversation started by Agent.

    Schema: ``{to: <agent_id>, message: <str>}``. Parameter names match
    Claude Code's SendMessage exactly so a CC user's muscle memory carries
    over.

    Tenant isolation: the resume state was tagged with the dispatching
    user_id + parent_session_id when Agent ran. SendMessage rejects calls
    where either differs from the current session -- an agent_id is scoped
    to the conversation and user that created it, never reusable across
    accounts or sibling chats.
    """
    target_id = (
        tool_input.get("to")
        or tool_input.get("agent_id")
        or ""
    ).strip()
    new_message = tool_input.get("message", "")

    if not target_id:
        return json.dumps({
            "error": "SendMessage requires `to` (the agent_id from a prior Agent dispatch)."
        })
    if not new_message:
        return json.dumps({"error": "SendMessage requires a non-empty `message`."})

    state = _active_agents.get(target_id)
    if state is None:
        return json.dumps({
            "error": (
                f"Unknown agent_id '{target_id}'. The id may have expired or "
                f"belongs to a different conversation. Spawn a fresh Agent."
            )
        })

    # Tenant isolation -- the calling session must match the dispatcher.
    try:
        from core.session import current_session_get, _current_session_id, _current_cid
        caller_user_id = current_session_get("user_id")
        caller_session_id = _current_session_id.get(None)
        caller_cid = _current_cid.get(None) or None
    except Exception:
        caller_user_id = None
        caller_session_id = None
        caller_cid = None

    if state.user_id and caller_user_id and state.user_id != caller_user_id:
        return json.dumps({
            "error": "agent_id belongs to a different user; access denied."
        })
    if (
        state.parent_conversation_id
        and caller_cid
        and state.parent_conversation_id != caller_cid
    ):
        return json.dumps({
            "error": (
                "agent_id was created in a different conversation; spawn "
                "a fresh Agent in this conversation to continue."
            )
        })

    # If the initial dispatch hasn't finished (background path), wait it out.
    if state.done_event is not None and not state.done_event.is_set():
        try:
            await asyncio.wait_for(state.done_event.wait(), timeout=300.0)
        except asyncio.TimeoutError:
            return json.dumps({
                "error": "Initial agent dispatch did not finish in time."
            })

    if state.status == "failed":
        return json.dumps({
            "error": (
                f"Cannot SendMessage to agent_id '{target_id}': initial "
                f"dispatch failed ({state.result or 'unknown error'})."
            )
        })

    # Resolve the agent (user-defined or builtin) the same way the initial
    # dispatch did, so SendMessage uses the same system prompt + tools.
    agent_def = None
    at = None
    if state.agent_def_id:
        try:
            from sqlalchemy import select
            from core.database import get_db
            from core.db_models import AgentDefinition
            async with get_db() as db:
                res = await db.execute(
                    select(AgentDefinition).where(
                        AgentDefinition.id == state.agent_def_id,
                    )
                )
                agent_def = res.scalar_one_or_none()
        except Exception as e:
            logger.error(f"SendMessage: failed to re-resolve agent_def: {e}")
            return json.dumps({"error": f"Failed to resume agent: {e}"})
        if agent_def is None or not agent_def.enabled:
            return json.dumps({
                "error": "Underlying agent has been deleted or disabled."
            })
    elif state.builtin_type_name:
        at = get_agent_type(state.builtin_type_name)
        if at is None:
            return json.dumps({
                "error": (
                    f"Builtin '{state.builtin_type_name}' no longer registered."
                )
            })

    lock = state.lock or asyncio.Lock()
    async with lock:
        try:
            if agent_def is not None:
                result_text, _run_id, updated = await _run_via_definition(
                    agent_def, new_message, state.parent_session_id,
                    prior_messages=state.messages,
                )
            else:
                result_text, updated = await _run_via_builtin(
                    at, new_message, state.model_override,
                    prior_messages=state.messages,
                )
            state.messages = updated
            state.result = result_text
        except Exception as e:
            logger.error(f"SendMessage continuation failed: {e}")
            return json.dumps({"error": str(e), "agent_id": target_id})

    return json.dumps({
        "result": result_text,
        "agent_id": target_id,
        "subagent_type": state.subagent_type,
    })


async def handle_agent_status(tool_input: dict[str, Any]) -> str:
    """Check the status of background subagents."""
    agent_id = tool_input.get("agent_id", "")

    if not agent_id:
        agents = [
            {
                "agent_id": a.agent_id,
                "subagent_type": a.subagent_type,
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
        "subagent_type": state.subagent_type,
        "status": state.status,
        "description": state.description,
        "result": state.result,
    })


# ── Register tools ──────────────────────────────────────────────────────

register_tool(
    name="Agent",
    aliases=["Task", "subagent", "BackgroundAgent", "background_agent"],
    description=(
        "Delegate a task to a specialized subagent. The subagent runs in an isolated "
        "context with its own system prompt and tool filter, and returns a single "
        "message as the result. Use for: parallelising independent work (fire "
        "multiple Agent calls in one turn), isolating large investigations from the "
        "main conversation, or delegating to a domain-specialist persona. "
        "Pass `subagent_type` to select the agent -- either one of your custom "
        "agents (resolved by name for your user) or a builtin: explorer (file "
        "discovery), planner (architecture), coder (implementation), researcher "
        "(web + analysis), default (all tools). Custom agents shadow builtins of "
        "the same name. Use `run_in_background=true` to continue working while "
        "the subagent runs -- you'll be notified when it completes."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "subagent_type": {
                "type": "string",
                "description": (
                    "Name of the subagent -- either one of your custom agents "
                    "or a builtin (explorer/planner/coder/researcher/default). "
                    "Default: 'default'."
                ),
            },
            "prompt": {
                "type": "string",
                "description": "The task for the subagent. Must be self-contained -- the subagent has no view of the parent conversation.",
            },
            "description": {
                "type": "string",
                "description": (
                    "Short (3-5 word) description of the task, shown in UI and "
                    "notifications. Optional -- defaults to 'Subagent run'."
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": "If true, return immediately and notify on completion. Defaults to false (blocking).",
            },
            "model": {
                "type": "string",
                "description": "Optional model override for this delegation (e.g. 'claude-haiku-4-5-20251001').",
            },
        },
        "required": ["subagent_type", "prompt"],
    },
    handler=handle_task,
)

register_tool(
    name="SendMessage",
    aliases=["send_message"],
    description=(
        "Continue a sub-conversation with a previously spawned subagent. "
        "Use this AFTER an Agent(...) call returned an `agent_id` -- "
        "instead of spawning a fresh agent for a follow-up question, pass "
        "the same `agent_id` as `to` and the next user-style message as "
        "`message`. The subagent picks up where it left off (same system "
        "prompt, tools, and full sub-conversation history) and returns its "
        "next reply. This is the Agent-teams pattern: spawn specialists in "
        "parallel with `Agent`, then iterate with each via `SendMessage`, "
        "and synthesise their outputs at the end."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": (
                    "The `agent_id` returned by a prior `Agent(...)` call in "
                    "this conversation. Cross-conversation and cross-user "
                    "ids are rejected."
                ),
            },
            "message": {
                "type": "string",
                "description": (
                    "The next user-style message for the subagent. Treat "
                    "this like a fresh user turn -- the subagent already "
                    "remembers the prior exchange."
                ),
            },
        },
        "required": ["to", "message"],
    },
    handler=handle_send_message,
)

register_tool(
    name="AgentStatus",
    aliases=["CheckBackground", "check_background"],
    description=(
        "Check the status of background subagents. "
        "Call with no agent_id to list all active/completed background agents, "
        "or with an agent_id to get the result of a specific one."
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
