"""Scheduler -- runs cron-based agent tasks from database definitions.

Background asyncio loop checks the DB every CHECK_INTERVAL seconds for
schedules whose next_run_at has passed, fires an agent for each, and
records the result.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select, update

from core.database import get_db
from core.db_models import Schedule, ScheduleRun
from core.job_queue import register_job

logger = logging.getLogger(__name__)

_scheduler_task: asyncio.Task | None = None

# user_id -> set of async callables that send WS events
_user_senders: dict[str, set[Callable]] = {}

CHECK_INTERVAL = 30  # seconds


def register_user_sender(user_id: str, send_event: Callable) -> None:
    """Register a WebSocket send_event for a user (called on WS connect)."""
    _user_senders.setdefault(user_id, set()).add(send_event)


def unregister_user_sender(user_id: str, send_event: Callable) -> None:
    """Unregister a WebSocket send_event for a user (called on WS disconnect)."""
    senders = _user_senders.get(user_id)
    if senders:
        senders.discard(send_event)
        if not senders:
            del _user_senders[user_id]


async def _notify_user(user_id: str, schedule_name: str, message: str) -> None:
    """Push a notification to all active sessions for a user."""
    senders = _user_senders.get(user_id, set())
    for send_event in list(senders):
        try:
            await send_event("notification", {
                "message": message,
                "source": "scheduler",
                "schedule_name": schedule_name,
            })
        except Exception:
            pass  # connection may have closed


async def _notify_user_schedule_result(
    user_id: str, schedule_name: str, status: str, conversation_id: str | None,
) -> None:
    """Push a schedule_result event so the frontend can show and link to the conversation."""
    senders = _user_senders.get(user_id, set())
    for send_event in list(senders):
        try:
            await send_event("schedule_result", {
                "schedule_name": schedule_name,
                "status": status,
                "conversation_id": conversation_id,
            })
        except Exception:
            pass


def _compute_next_run(
    cron_expression: str,
    base: datetime | None = None,
    user_tz: str = "UTC",
) -> datetime:
    """Compute the next fire time from a cron expression.

    Evaluates the cron in the user's timezone so that '0 9 * * *' means
    9 AM local time, then converts the result back to UTC for storage.
    """
    tz = ZoneInfo(user_tz) if user_tz else timezone.utc
    base_utc = base or datetime.now(timezone.utc)
    # Convert base to user's local time so croniter evaluates in local time
    base_local = base_utc.astimezone(tz)
    next_local = croniter(cron_expression, base_local).get_next(datetime)
    # Convert back to UTC for DB storage
    return next_local.astimezone(timezone.utc)


async def _execute_schedule(
    schedule_id: str, user_id: str, name: str, prompt: str, cron_expression: str,
    user_timezone: str = "UTC",
    **_kwargs: Any,
) -> None:
    """Run a single scheduled agent task and record the result."""
    from core import agent
    from tools.registry import get_all_tool_schemas
    from core.permissions import is_blocked, is_risky

    # Set session context so tools (sandbox, etc.) can resolve the user
    from core.session import set_current_session, current_session_set, connection_set
    session_id = f"sched-{schedule_id}"
    set_current_session(session_id)
    current_session_set("user_id", user_id)
    connection_set(session_id, "user_id", user_id)

    async with get_db() as _init_db:
        try:
            run = ScheduleRun(schedule_id=schedule_id, status="running")
            _init_db.add(run)
            await _init_db.commit()
            await _init_db.refresh(run)
            run_id = run.id
        except Exception as e:
            logger.error(f"Failed to create schedule run record: {e}")
            return

    async def perm_check(tool_name: str, tool_input: dict[str, Any]) -> bool | str:
        if is_blocked(tool_name, tool_input):
            return "BLOCKED: This command is not allowed."
        if is_risky(tool_name, tool_input):
            return "DENIED: Risky commands cannot run in scheduled tasks."
        return True

    # Resolve skill/command/agent references in prompt.
    # Surface matches CC + chat: ``/foo`` is skill-or-command; ``@agent-foo`` is
    # agent. No cross-category fallback -- a Schedule whose prompt starts with
    # ``/`` will never escalate to an agent of the same name (use the explicit
    # ``@agent-`` form for agent dispatch).
    user_text = prompt
    skill_prompt: str | None = None
    tool_filter = None
    delegated_agent = None  # if set, we run this agent instead of the generic loop
    delegated_prompt = ""

    prompt_stripped = prompt.strip()

    # Module-level regex import kept local to scheduler to avoid widening the
    # cold-start import graph for code paths that never hit the @agent- form.
    from handlers.agent_handler import AGENT_MENTION_RE
    sched_mention = AGENT_MENTION_RE.match(prompt_stripped)
    if sched_mention:
        from core.agent_crud import get_agent_by_name
        agent_name = sched_mention.group(1)
        agent_args = (sched_mention.group(2) or "").strip()
        maybe_agent = await get_agent_by_name(agent_name, user_id)
        if maybe_agent and maybe_agent.enabled:
            delegated_agent = maybe_agent
            delegated_prompt = agent_args or f"Execute your task ({maybe_agent.name})."
    elif prompt_stripped.startswith("/"):
        # Unified resolver: real skills and legacy commands both live in the
        # skills registry now (commands wrapped as ``kind="legacy-command"``
        # SkillDefs). Plain ``/foo`` matches whichever exists, with no
        # cross-category fallback to agents.
        from skills.registry import match_skill_by_name
        from core.models import resolve_dynamic_content

        tokens = prompt_stripped.split(None, 1)
        slash_name = tokens[0][1:]  # strip leading /
        slash_args = tokens[1] if len(tokens) > 1 else ""

        skill = match_skill_by_name(slash_name)
        if skill:
            skill_prompt = skill.resolve_arguments(slash_args)
            skill_prompt = await resolve_dynamic_content(skill_prompt)
            user_text = slash_args or skill.description
            if skill.tools:
                tool_filter = skill.tools

    # If this schedule fires an agent, skip the generic loop entirely --
    # run_agent_once owns execution + AgentRun persistence.
    if delegated_agent is not None:
        from core.agent_runner import run_agent_once
        try:
            result = await run_agent_once(
                delegated_agent, delegated_prompt,
                trigger_type="cron",
                persist_conversation=True,
            )
            status = result.status
            error = result.error
            result_text = result.result_text
            conv_id = result.conversation_id
            now = datetime.now(timezone.utc)
        except Exception as e:
            status = "failed"
            error = str(e)
            result_text = None
            conv_id = None
            now = datetime.now(timezone.utc)
            logger.error(f"Scheduled agent '{delegated_agent.name}' failed: {e}")

        async with get_db() as db2:
            try:
                await db2.execute(
                    update(ScheduleRun)
                    .where(ScheduleRun.id == run_id)
                    .values(
                        status=status, result=result_text, error=error,
                        finished_at=now, conversation_id=conv_id,
                    )
                )
                await db2.execute(
                    update(Schedule)
                    .where(Schedule.id == schedule_id)
                    .values(last_run_at=now, next_run_at=_compute_next_run(cron_expression, now, user_timezone))
                )
                await db2.commit()
            except Exception as e:
                logger.error(f"Failed to update schedule run (agent path): {e}")

        label = "completed" if status == "completed" else "failed"
        await _notify_user_schedule_result(user_id, name, label, conv_id)
        return

    # Use the same system prompt builder as normal chat so Claude gets
    # full context (tool guidelines, output dirs, no-emoji rules, etc.)
    from core.system_prompt import build_system_prompt
    from core.sandbox import get_workspace
    user_workspace = get_workspace(user_id)
    system = build_system_prompt(
        skill_prompt=skill_prompt,
        outputs_dir=str(user_workspace.outputs),
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_text}]

    if tool_filter:
        from tools.registry import get_tool_schemas
        tools = get_tool_schemas(tool_filter)
    else:
        tools = get_all_tool_schemas()
    output_parts: list[str] = []

    async def on_text(text: str) -> None:
        output_parts.append(text)

    async def on_tool_start(n: str, inp: dict[str, Any]) -> None:
        return None

    async def on_tool_end(ctx: Any, result: str) -> None:
        pass

    try:
        await agent.run(
            messages=messages,
            tools=tools,
            system=system,
            on_text=on_text,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
            on_permission_check=perm_check,
            max_turns=20,
        )
        full_text = "".join(output_parts)
        result_text = full_text[:10000]  # truncated summary for schedule_runs table
        status = "completed"
        error = None
    except Exception as e:
        full_text = None
        result_text = None
        status = "failed"
        error = str(e)
        logger.error(f"Scheduled task '{name}' failed: {e}")

    now = datetime.now(timezone.utc)
    conv_id = None

    async with get_db() as db:
        try:
            # Save the agent run as a conversation so the user can view and continue it
            from core.db_models import Conversation, Message as DbMessage
            import json as _json

            conv = Conversation(
                user_id=user_id,
                title=f"[Scheduled] {name}",
                model=None,
            )
            db.add(conv)
            await db.flush()
            conv_id = conv.id

            # Store full (untruncated) output as the conversation message
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
                    content=f"Scheduled task failed: {error}",
                ))

            # Update run record
            await db.execute(
                update(ScheduleRun)
                .where(ScheduleRun.id == run_id)
                .values(
                    status=status, result=result_text, error=error,
                    finished_at=now, conversation_id=conv_id,
                )
            )
            # Update schedule timestamps
            await db.execute(
                update(Schedule)
                .where(Schedule.id == schedule_id)
                .values(last_run_at=now, next_run_at=_compute_next_run(cron_expression, now, user_timezone))
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Failed to update schedule run: {e}")

    # Notify user with a link to the conversation
    label = "completed" if status == "completed" else "failed"
    await _notify_user_schedule_result(
        user_id, name, label, conv_id,
    )


async def _scheduler_loop() -> None:
    """Main loop: check for due schedules and cron-triggered agents every CHECK_INTERVAL seconds."""
    while True:
        try:
            async with get_db() as db:
                now = datetime.now(timezone.utc)
                stmt = select(Schedule).where(
                    Schedule.enabled == True,  # noqa: E712
                    Schedule.next_run_at <= now,
                )
                result = await db.execute(stmt)
                due = result.scalars().all()

                for sched in due:
                    logger.info(f"Firing scheduled task: {sched.name} (id={sched.id})")

                    try:
                        from core.job_queue import enqueue
                        await enqueue(
                            "run_scheduled_agent",
                            schedule_id=sched.id,
                            user_id=sched.user_id,
                            name=sched.name,
                            prompt=sched.prompt,
                            cron_expression=sched.cron_expression,
                            user_timezone=sched.user_timezone or "UTC",
                        )
                    except Exception as e:
                        logger.error(f"Failed to enqueue scheduled task '{sched.name}': {e}")
                        continue

                    # Advance next_run_at only after successful enqueue
                    sched.next_run_at = _compute_next_run(sched.cron_expression, now, sched.user_timezone)
                    await db.commit()
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


async def start_scheduler() -> None:
    """Start the scheduler background task."""
    global _scheduler_task

    # Initialise next_run_at for any enabled schedules that don't have it
    async with get_db() as db:
        stmt = select(Schedule).where(
            Schedule.enabled == True,  # noqa: E712
            Schedule.next_run_at == None,  # noqa: E711
        )
        result = await db.execute(stmt)
        for sched in result.scalars().all():
            sched.next_run_at = _compute_next_run(sched.cron_expression, user_tz=sched.user_timezone)

        await db.commit()

    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("Scheduler started (check interval: %ds)", CHECK_INTERVAL)


async def stop_scheduler() -> None:
    """Stop the scheduler background task."""
    global _scheduler_task
    if _scheduler_task:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        _scheduler_task = None
        logger.info("Scheduler stopped")


# Register as a distributable job for arq/asyncio fallback
register_job("run_scheduled_agent", _execute_schedule)
