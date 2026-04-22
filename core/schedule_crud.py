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
    user_timezone: str = "UTC",
) -> Schedule:
    """Create a new schedule. Raises ValueError for invalid cron or duplicate name."""
    if not croniter.is_valid(cron_expression):
        raise ValueError(f"Invalid cron expression: {cron_expression}")

    async with get_db() as db:
        existing = await db.execute(
            select(Schedule).where(Schedule.user_id == user_id, Schedule.name == name)
        )
        if existing.first():
            raise ValueError(f"A schedule named '{name}' already exists. Use a different name or delete the existing one first.")

        sched = Schedule(
            user_id=user_id,
            name=name,
            cron_expression=cron_expression,
            prompt=prompt,
            user_timezone=user_timezone,
            enabled=True,
            next_run_at=_compute_next_run(cron_expression, user_tz=user_timezone),
        )
        db.add(sched)
        await db.commit()
        await db.refresh(sched)
        return sched


async def list_schedules(user_id: str) -> list[Schedule]:
    """List all schedules for a user."""
    async with get_db() as db:
        stmt = select(Schedule).where(Schedule.user_id == user_id).order_by(Schedule.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())


async def get_schedule(schedule_id: str, user_id: str) -> Schedule | None:
    """Get a single schedule by ID, scoped to user."""
    async with get_db() as db:
        stmt = select(Schedule).where(Schedule.id == schedule_id, Schedule.user_id == user_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()


async def get_schedule_by_name(name: str, user_id: str) -> Schedule | None:
    """Get a schedule by name, scoped to user."""
    async with get_db() as db:
        stmt = select(Schedule).where(Schedule.name == name, Schedule.user_id == user_id).limit(1)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()


async def resolve_schedule(name_or_id: str, user_id: str) -> Schedule | None:
    """Find a schedule by exact ID, short ID prefix, or name.

    Accepts inputs like "725da54c", "725da54c Morning Brief", or "Morning Brief".
    The first token is always tried as an ID/prefix before falling back to name.
    """
    name_or_id = name_or_id.strip()

    # Extract the first token -- it might be an ID or ID prefix
    first_token = name_or_id.split()[0] if name_or_id else ""

    # Try first token as exact full ID
    if first_token:
        sched = await get_schedule(first_token, user_id)
        if sched:
            return sched

    # Try first token as short hex ID prefix (4+ hex chars)
    if first_token and len(first_token) >= 4 and all(c in "0123456789abcdef" for c in first_token.lower()):
        async with get_db() as db:
            stmt = (
                select(Schedule)
                .where(Schedule.user_id == user_id, Schedule.id.startswith(first_token.lower()))
                .limit(2)
            )
            result = await db.execute(stmt)
            matches = list(result.scalars().all())
            if len(matches) == 1:
                return matches[0]

    # Try full string as exact full ID (in case name looks like a hex string)
    if first_token != name_or_id:
        sched = await get_schedule(name_or_id, user_id)
        if sched:
            return sched

    # Name match -- try full string, then without leading ID token
    sched = await get_schedule_by_name(name_or_id, user_id)
    if sched:
        return sched

    # If first token looked like an ID, try the remainder as a name
    if first_token != name_or_id:
        remainder = name_or_id.split(None, 1)[1] if " " in name_or_id else ""
        if remainder:
            return await get_schedule_by_name(remainder, user_id)

    return None


async def update_schedule(schedule_id: str, user_id: str, **kwargs) -> Schedule | None:
    """Update a schedule. Accepts: name, cron_expression, prompt, enabled."""
    async with get_db() as db:
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
                sched.next_run_at = _compute_next_run(value, user_tz=sched.user_timezone)
            elif hasattr(sched, key):
                setattr(sched, key, value)

        # Recompute next_run_at if timezone changed
        if "user_timezone" in kwargs:
            sched.next_run_at = _compute_next_run(sched.cron_expression, user_tz=sched.user_timezone)

        if "enabled" in kwargs and kwargs["enabled"] and not sched.next_run_at:
            sched.next_run_at = _compute_next_run(sched.cron_expression, user_tz=sched.user_timezone)

        await db.commit()
        await db.refresh(sched)
        return sched


async def delete_schedule(schedule_id: str, user_id: str) -> bool:
    """Delete a schedule and its runs."""
    async with get_db() as db:
        stmt = select(Schedule).where(Schedule.id == schedule_id, Schedule.user_id == user_id)
        result = await db.execute(stmt)
        sched = result.scalar_one_or_none()
        if not sched:
            return False
        await db.delete(sched)
        await db.commit()
        return True


async def get_schedule_runs(schedule_id: str, user_id: str, limit: int = 20) -> list[ScheduleRun]:
    """Get recent runs for a schedule."""
    async with get_db() as db:
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
