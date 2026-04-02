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


def _compute_next_run(cron_expression: str, base: datetime | None = None) -> datetime:
    """Compute the next fire time from a cron expression."""
    base = base or datetime.now(timezone.utc)
    return croniter(cron_expression, base).get_next(datetime)


async def _execute_schedule(
    schedule_id: str, user_id: str, name: str, prompt: str, cron_expression: str,
    **_kwargs: Any,
) -> None:
    """Run a single scheduled agent task and record the result."""
    from core import agent
    from tools.registry import get_all_tool_schemas
    from core.permissions import is_blocked, is_risky

    db = await get_db()
    try:
        run = ScheduleRun(schedule_id=schedule_id, status="running")
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id = run.id
    except Exception as e:
        logger.error(f"Failed to create schedule run record: {e}")
        await db.close()
        return

    async def perm_check(tool_name: str, tool_input: dict[str, Any]) -> bool | str:
        if is_blocked(tool_name, tool_input):
            return "BLOCKED: This command is not allowed."
        if is_risky(tool_name, tool_input):
            return "DENIED: Risky commands cannot run in scheduled tasks."
        return True

    # Resolve skill/command references in prompt (e.g. "/analyze AAPL")
    resolved_prompt = prompt
    system_extra = ""
    tool_filter = None

    if prompt.strip().startswith("/"):
        from skills.registry import match_skill_by_name
        from commands.registry import get_command

        tokens = prompt.strip().split(None, 1)
        slash_name = tokens[0][1:]  # strip leading /
        slash_args = tokens[1] if len(tokens) > 1 else ""

        skill = match_skill_by_name(slash_name)
        if skill:
            resolved_prompt = skill.resolve_arguments(slash_args)
            if skill.prompt:
                system_extra = f"\n\n## Active Skill: {skill.name}\n{skill.prompt}"
            if skill.tools:
                tool_filter = skill.tools
        else:
            cmd = get_command(slash_name)
            if cmd:
                resolved_prompt = cmd.resolve_arguments(slash_args)

    system = (
        f"You are a scheduled agent running the task '{name}'. "
        "Complete the task thoroughly and return a clear, concise result."
        f"{system_extra}"
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": resolved_prompt}]

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
        result_text = "".join(output_parts)[:10000]
        status = "completed"
        error = None
    except Exception as e:
        result_text = None
        status = "failed"
        error = str(e)
        logger.error(f"Scheduled task '{name}' failed: {e}")

    now = datetime.now(timezone.utc)
    conv_id = None

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

        # Store the prompt as a user message and the result as assistant message
        db.add(DbMessage(
            conversation_id=conv_id,
            role="user",
            content=resolved_prompt,
        ))
        if result_text:
            db.add(DbMessage(
                conversation_id=conv_id,
                role="assistant",
                content=result_text,
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
            .values(last_run_at=now, next_run_at=_compute_next_run(cron_expression, now))
        )
        await db.commit()
    except Exception as e:
        logger.error(f"Failed to update schedule run: {e}")
    finally:
        await db.close()

    # Notify user with a link to the conversation
    label = "completed" if status == "completed" else "failed"
    await _notify_user_schedule_result(
        user_id, name, label, conv_id,
    )


async def _scheduler_loop() -> None:
    """Main loop: check for due schedules every CHECK_INTERVAL seconds."""
    while True:
        try:
            db = await get_db()
            now = datetime.now(timezone.utc)
            stmt = select(Schedule).where(
                Schedule.enabled == True,  # noqa: E712
                Schedule.next_run_at <= now,
            )
            result = await db.execute(stmt)
            due = result.scalars().all()

            for sched in due:
                # Advance next_run_at immediately to prevent double-fire
                sched.next_run_at = _compute_next_run(sched.cron_expression, now)
                await db.commit()

                logger.info(f"Firing scheduled task: {sched.name} (id={sched.id})")

                from core.job_queue import enqueue
                await enqueue(
                    "run_scheduled_agent",
                    schedule_id=sched.id,
                    user_id=sched.user_id,
                    name=sched.name,
                    prompt=sched.prompt,
                    cron_expression=sched.cron_expression,
                )

            await db.close()
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


async def start_scheduler() -> None:
    """Start the scheduler background task."""
    global _scheduler_task

    # Initialise next_run_at for any enabled schedules that don't have it
    db = await get_db()
    try:
        stmt = select(Schedule).where(
            Schedule.enabled == True,  # noqa: E712
            Schedule.next_run_at == None,  # noqa: E711
        )
        result = await db.execute(stmt)
        for sched in result.scalars().all():
            sched.next_run_at = _compute_next_run(sched.cron_expression)
        await db.commit()
    finally:
        await db.close()

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
