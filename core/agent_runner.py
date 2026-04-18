"""Agent execution -- the callable-subagent engine.

Provides the central primitive ``run_agent_once`` plus two thin wrappers:

- ``trigger_agent_run`` -- enqueues an async background run (returns immediately).
- ``_execute_agent_run`` -- the arq job handler that dequeues and calls
  ``run_agent_once`` with persistence + webhook semantics.

All three entry points (Task tool, headless POST /api/agents/run, scheduler
via Schedule rows that reference an agent name) ultimately call
``run_agent_once`` -- keeping the hot path in one place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from core.database import get_db
from core.db_models import AgentDefinition, AgentRun, Conversation, Message as DbMessage
from core.job_queue import register_job, enqueue

logger = logging.getLogger(__name__)


@dataclass
class AgentRunResult:
    """Outcome of a single agent run. Returned by ``run_agent_once``."""

    run_id: str
    status: str  # "completed" | "failed"
    result_text: str | None
    error: str | None
    tokens_used: int
    conversation_id: str | None = None


async def trigger_agent_run(
    agent_def: AgentDefinition,
    trigger_type: str = "manual",
) -> AgentRun:
    """Create an AgentRun row (status=running) and enqueue background execution.

    Returns the pre-run record so callers can poll or return 202 Accepted.
    """
    db = await get_db()
    try:
        run = AgentRun(
            agent_id=agent_def.id,
            user_id=agent_def.user_id,
            status="running",
            trigger_type=trigger_type,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id = run.id
        run_copy = AgentRun(
            id=run.id,
            agent_id=run.agent_id,
            user_id=run.user_id,
            status=run.status,
            trigger_type=run.trigger_type,
            started_at=run.started_at,
            tokens_used=0,
        )
    finally:
        await db.close()

    await enqueue(
        "execute_agent_run",
        agent_id=agent_def.id,
        run_id=run_id,
        trigger_type=trigger_type,
    )
    return run_copy


async def run_agent_once(
    agent_def: AgentDefinition,
    prompt: str,
    *,
    trigger_type: str = "manual",
    run_id: str | None = None,
    parent_session_id: str | None = None,
    persist_conversation: bool = False,
    tool_filter: list[str] | None = None,
) -> AgentRunResult:
    """Execute an agent once with the given prompt.

    This is the single hot path for all agent execution. Creates (or updates)
    an ``AgentRun`` row, runs the isolated agent loop with the agent's own
    system prompt + tool filter, and returns ``AgentRunResult``.

    ``tool_filter`` optionally narrows the agent's own ``tools_list`` further
    (never widens it). Used by the ``Task`` tool when a caller wants to
    restrict a subagent to a subset of its permitted tools.

    Side effects are opt-in:
      - ``persist_conversation=True`` saves the run's output as a Conversation
        row so it appears in the user's sidebar. Useful for scheduled/webhook
        runs; NOT wanted for in-conversation Task tool calls (the parent
        conversation is already the record).

    Webhooks used to fire here when AgentDefinition.webhook_url was set.
    That field has been dropped -- if we re-add webhook semantics they should
    live on the Schedule row, not the agent persona.
    """
    from core import agent as agent_module
    from core.permissions import is_blocked, is_risky
    from core.sandbox import get_workspace
    from core.session import set_current_session, current_session_set, connection_set
    from core.system_prompt import build_system_prompt
    from tools.registry import get_all_tool_schemas, get_tool_schemas

    # Snapshot agent-def fields we need (avoid detached-instance problems).
    user_id = agent_def.user_id
    a_name = agent_def.name
    a_system_prompt = agent_def.system_prompt
    a_tools = agent_def.tools_list
    a_skills = agent_def.skills_list
    a_model = agent_def.model
    a_max_turns = agent_def.max_turns

    # Ensure we have an AgentRun row: create one if the caller didn't.
    db = await get_db()
    try:
        if run_id is None:
            run = AgentRun(
                agent_id=agent_def.id,
                user_id=user_id,
                status="running",
                trigger_type=trigger_type,
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)
            run_id = run.id
    finally:
        await db.close()

    # Session context: tools that call current_session_get need a user.
    # Use a parent-derived id when delegating (Task tool passes one through),
    # otherwise synthesise a fresh ``agent-<run_id>`` id.
    session_id = parent_session_id or f"agent-{run_id}"
    set_current_session(session_id)
    current_session_set("user_id", user_id)
    connection_set(session_id, "user_id", user_id)

    async def perm_check(tool_name: str, tool_input: dict[str, Any]) -> bool | str:
        if is_blocked(tool_name, tool_input):
            return "BLOCKED: This command is not allowed."
        if is_risky(tool_name, tool_input):
            return "DENIED: Risky commands cannot run in agent executions."
        return True

    user_workspace = get_workspace(user_id)
    system = build_system_prompt(
        skill_prompt=a_system_prompt,
        outputs_dir=str(user_workspace.outputs),
        allowed_skills=a_skills,
    )

    # Tool filter resolution: agent's own list, optionally narrowed.
    if tool_filter is not None and a_tools is not None:
        effective = [t for t in tool_filter if t in a_tools]
    elif tool_filter is not None:
        effective = list(tool_filter)
    else:
        effective = a_tools
    # If the agent composes skills and has a narrowed tool list, auto-include
    # the ``Skill`` tool -- without it the agent can't actually invoke the
    # skills it declared.
    if a_skills and effective is not None and "Skill" not in effective:
        effective = [*effective, "Skill"]
    tools = get_tool_schemas(effective) if effective else get_all_tool_schemas()

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    # Collect only the final assistant text -- drop mid-process narration
    # ("Let me search...", "Now fetching..."). Each tool_start marks a new
    # assistant turn, so we clear the buffer then and keep only text produced
    # after the last tool use.
    output_parts: list[str] = []

    async def on_text(text: str) -> None:
        output_parts.append(text)

    async def on_tool_start(n: str, inp: dict[str, Any]) -> None:
        output_parts.clear()

    async def on_tool_end(ctx: Any, result: str) -> None:
        pass

    tokens_used = 0

    async def on_usage(input_tokens: int, output_tokens: int) -> None:
        nonlocal tokens_used
        tokens_used += input_tokens + output_tokens

    status = "running"
    error: str | None = None

    try:
        await agent_module.run(
            messages=messages,
            tools=tools,
            system=system,
            on_text=on_text,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
            on_permission_check=perm_check,
            on_usage=on_usage,
            max_turns=a_max_turns,
            model=a_model,
        )
        status = "completed"
    except Exception as e:
        status = "failed"
        error = str(e)
        logger.error(f"Agent '{a_name}' run {run_id} failed: {e}")

    full_text = "".join(output_parts)
    result_text = full_text[:10000] if full_text else None
    now = datetime.now(timezone.utc)
    conv_id: str | None = None

    db = await get_db()
    try:
        if persist_conversation:
            conv = Conversation(
                user_id=user_id,
                title=f"[Agent] {a_name}",
                model=a_model,
            )
            db.add(conv)
            await db.flush()
            conv_id = conv.id

            db.add(DbMessage(
                conversation_id=conv_id,
                role="user",
                content=prompt,
            ))
            if full_text:
                db.add(DbMessage(
                    conversation_id=conv_id,
                    role="assistant",
                    content=full_text,
                ))
            elif error:
                db.add(DbMessage(
                    conversation_id=conv_id,
                    role="assistant",
                    content=f"Agent run failed: {error}",
                ))

        await db.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id)
            .values(
                status=status,
                result=result_text,
                error=error,
                finished_at=now,
                tokens_used=tokens_used,
                conversation_id=conv_id,
            )
        )
        await db.execute(
            update(AgentDefinition)
            .where(AgentDefinition.id == agent_def.id)
            .values(last_run_at=now)
        )
        await db.commit()
    except Exception as e:
        logger.error(f"Failed to persist agent run {run_id}: {e}")
    finally:
        await db.close()

    return AgentRunResult(
        run_id=run_id,
        status=status,
        result_text=result_text,
        error=error,
        tokens_used=tokens_used,
        conversation_id=conv_id,
    )


async def _execute_agent_run(
    agent_id: str,
    run_id: str,
    trigger_type: str = "manual",
    prompt: str | None = None,
    **_kwargs: Any,
) -> None:
    """Background job: looks up the agent + existing run row, runs it,
    persists output as a Conversation, fires webhook, notifies connected WS clients.
    """
    db = await get_db()
    try:
        res = await db.execute(select(AgentDefinition).where(AgentDefinition.id == agent_id))
        agent_def = res.scalar_one_or_none()
    finally:
        await db.close()

    if not agent_def:
        logger.error(f"Agent {agent_id} not found; aborting run {run_id}")
        return

    effective_prompt = prompt or f"Execute your task ({agent_def.name})."
    result = await run_agent_once(
        agent_def,
        effective_prompt,
        trigger_type=trigger_type,
        run_id=run_id,
        persist_conversation=True,
    )

    try:
        await _notify_user_agent_result(
            user_id=agent_def.user_id,
            agent_name=agent_def.name,
            run_id=result.run_id,
            status=result.status,
            conversation_id=result.conversation_id,
        )
    except Exception as e:
        logger.debug(f"agent_result notify failed for run {result.run_id}: {e}")


async def _notify_user_agent_result(
    user_id: str,
    agent_name: str,
    run_id: str,
    status: str,
    conversation_id: str | None,
) -> None:
    """Push an ``agent_result`` WS event to connected sessions for the user."""
    from core.scheduler import _user_senders

    senders = _user_senders.get(user_id, set())
    for send_event in list(senders):
        try:
            await send_event("agent_result", {
                "agent_name": agent_name,
                "run_id": run_id,
                "status": status,
                "conversation_id": conversation_id,
            })
        except Exception:
            pass


register_job("execute_agent_run", _execute_agent_run)
