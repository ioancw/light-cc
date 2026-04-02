"""Schedule CRUD operations -- used by /schedule command and REST API."""

from __future__ import annotations

from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select, update, delete

from core.database import get_db
from core.db_models import Schedule, ScheduleRun
from core.scheduler import _compute_next_run


async def create_schedule(
    user_id: str, name: str, cron_expression: str, prompt: str,
) -> Schedule:
    """Create a new schedule. Raises ValueError for invalid cron."""
    if not croniter.is_valid(cron_expression):
        raise ValueError(f"Invalid cron expression: {cron_expression}")

    db = await get_db()
    try:
        sched = Schedule(
            user_id=user_id,
            name=name,
            cron_expression=cron_expression,
            prompt=prompt,
            enabled=True,
            next_run_at=_compute_next_run(cron_expression),
        )
        db.add(sched)
        await db.commit()
        await db.refresh(sched)
        return sched
    finally:
        await db.close()


async def list_schedules(user_id: str) -> list[Schedule]:
    """List all schedules for a user."""
    db = await get_db()
    try:
        stmt = select(Schedule).where(Schedule.user_id == user_id).order_by(Schedule.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())
    finally:
        await db.close()


async def get_schedule(schedule_id: str, user_id: str) -> Schedule | None:
    """Get a single schedule by ID, scoped to user."""
    db = await get_db()
    try:
        stmt = select(Schedule).where(Schedule.id == schedule_id, Schedule.user_id == user_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    finally:
        await db.close()


async def get_schedule_by_name(name: str, user_id: str) -> Schedule | None:
    """Get a schedule by name, scoped to user."""
    db = await get_db()
    try:
        stmt = select(Schedule).where(Schedule.name == name, Schedule.user_id == user_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    finally:
        await db.close()


async def resolve_schedule(name_or_id: str, user_id: str) -> Schedule | None:
    """Find a schedule by name or ID."""
    sched = await get_schedule(name_or_id, user_id)
    if sched:
        return sched
    return await get_schedule_by_name(name_or_id, user_id)


async def update_schedule(schedule_id: str, user_id: str, **kwargs) -> Schedule | None:
    """Update a schedule. Accepts: name, cron_expression, prompt, enabled."""
    db = await get_db()
    try:
        stmt = select(Schedule).where(Schedule.id == schedule_id, Schedule.user_id == user_id)
        result = await db.execute(stmt)
        sched = result.scalar_one_or_none()
        if not sched:
            return None

        for key, value in kwargs.items():
            if key == "cron_expression":
                if not croniter.is_valid(value):
                    raise ValueError(f"Invalid cron expression: {value}")
                sched.cron_expression = value
                sched.next_run_at = _compute_next_run(value)
            elif hasattr(sched, key):
                setattr(sched, key, value)

        if "enabled" in kwargs and kwargs["enabled"] and not sched.next_run_at:
            sched.next_run_at = _compute_next_run(sched.cron_expression)

        await db.commit()
        await db.refresh(sched)
        return sched
    finally:
        await db.close()


async def delete_schedule(schedule_id: str, user_id: str) -> bool:
    """Delete a schedule and its runs."""
    db = await get_db()
    try:
        stmt = select(Schedule).where(Schedule.id == schedule_id, Schedule.user_id == user_id)
        result = await db.execute(stmt)
        sched = result.scalar_one_or_none()
        if not sched:
            return False
        await db.delete(sched)
        await db.commit()
        return True
    finally:
        await db.close()


async def get_schedule_runs(schedule_id: str, user_id: str, limit: int = 20) -> list[ScheduleRun]:
    """Get recent runs for a schedule."""
    db = await get_db()
    try:
        # Verify ownership
        sched_stmt = select(Schedule.id).where(Schedule.id == schedule_id, Schedule.user_id == user_id)
        sched_result = await db.execute(sched_stmt)
        if not sched_result.scalar_one_or_none():
            return []

        stmt = (
            select(ScheduleRun)
            .where(ScheduleRun.schedule_id == schedule_id)
            .order_by(ScheduleRun.started_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())
    finally:
        await db.close()


async def trigger_schedule_now(schedule_id: str, user_id: str) -> bool:
    """Manually trigger a schedule to run immediately."""
    sched = await get_schedule(schedule_id, user_id)
    if not sched:
        return False

    from core.job_queue import enqueue
    await enqueue(
        "run_scheduled_agent",
        schedule_id=sched.id,
        user_id=sched.user_id,
        name=sched.name,
        prompt=sched.prompt,
        cron_expression=sched.cron_expression,
    )
    return True
