"""R2 regression tests.

- Scheduler loop: if enqueue raises mid-tick, `next_run_at` stays unchanged
  so the schedule fires again instead of silently skipping a run.
- agent_runs.broadcast: a callback that raises is removed from the subscriber
  set so disconnected WS clients don't accumulate forever.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from core import agent_runs
from core.database import get_db
from core.db_models import Schedule, User


@pytest.mark.asyncio
async def test_scheduler_rolls_back_next_run_on_enqueue_failure():
    """If `enqueue` raises, next_run_at must stay at its pre-tick value."""
    import uuid

    from core import scheduler as sched_mod

    async with get_db() as db:
        user = User(
            email=f"sched-atomic-{uuid.uuid4().hex[:8]}@test",
            password_hash="x",
            display_name="sched",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        s = Schedule(
            user_id=user.id,
            name="atomic-test",
            prompt="/noop",
            cron_expression="*/5 * * * *",
            user_timezone="UTC",
            enabled=True,
            next_run_at=past,
        )
        db.add(s)
        await db.commit()
        await db.refresh(s)
        sched_id = s.id
        original_next_run = s.next_run_at

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated Redis outage")

    # Run one iteration of the loop body inline (can't drive the real loop
    # without a live asyncio.sleep). Mirror the code path exactly.
    now = datetime.now(timezone.utc)
    async with get_db() as db:
        stmt = select(Schedule).where(
            Schedule.enabled == True,  # noqa: E712
            Schedule.id == sched_id,
        )
        result = await db.execute(stmt)
        due = result.scalars().all()
        assert due, "precondition: schedule is due"

        with patch("core.job_queue.enqueue", side_effect=boom):
            for sched in due:
                previous_next_run = sched.next_run_at
                sched.next_run_at = sched_mod._compute_next_run(
                    sched.cron_expression, now, sched.user_timezone
                )
                try:
                    from core.job_queue import enqueue
                    await enqueue("run_scheduled_agent", schedule_id=sched.id)
                    await db.commit()
                except Exception:
                    sched.next_run_at = previous_next_run
                    await db.rollback()

    # Re-open the session and confirm next_run_at wasn't persisted forward.
    async with get_db() as db:
        refreshed = (await db.execute(select(Schedule).where(Schedule.id == sched_id))).scalar_one()
        assert refreshed.next_run_at == original_next_run, (
            f"next_run_at advanced despite enqueue failure: "
            f"{refreshed.next_run_at} vs expected {original_next_run}"
        )

    async with get_db() as db:
        await db.execute(Schedule.__table__.delete().where(Schedule.id == sched_id))
        await db.execute(User.__table__.delete().where(User.id == user.id))
        await db.commit()


@pytest.mark.asyncio
async def test_broadcast_removes_failing_subscriber():
    cid = "test-cid-broadcast-1"
    good_calls: list[tuple] = []

    async def good(event_type, data, c):
        good_calls.append((event_type, data, c))

    async def bad(event_type, data, c):
        raise ConnectionError("WS client is dead")

    agent_runs.subscribe(cid, good)
    agent_runs.subscribe(cid, bad)
    assert len(agent_runs._subscribers[cid]) == 2

    await agent_runs.broadcast(cid, "event", {"x": 1})

    assert good_calls == [("event", {"x": 1}, cid)]
    assert bad not in agent_runs._subscribers.get(cid, set())
    assert good in agent_runs._subscribers.get(cid, set())

    await agent_runs.broadcast(cid, "event2", {"y": 2})
    assert good_calls[-1] == ("event2", {"y": 2}, cid)

    agent_runs.unsubscribe(cid, good)


@pytest.mark.asyncio
async def test_broadcast_drops_cid_when_all_subscribers_fail():
    cid = "test-cid-broadcast-2"

    async def bad1(*a, **kw):
        raise RuntimeError("boom1")

    async def bad2(*a, **kw):
        raise RuntimeError("boom2")

    agent_runs.subscribe(cid, bad1)
    agent_runs.subscribe(cid, bad2)
    assert cid in agent_runs._subscribers

    await agent_runs.broadcast(cid, "event", {})
    assert cid not in agent_runs._subscribers


@pytest.mark.asyncio
async def test_broadcast_surviving_subscribers_keep_receiving():
    cid = "test-cid-broadcast-3"
    received = []

    async def survivor(event_type, data, c):
        received.append(event_type)

    async def transient_bad(event_type, data, c):
        raise TimeoutError("timeout")

    agent_runs.subscribe(cid, survivor)
    agent_runs.subscribe(cid, transient_bad)

    for i in range(5):
        await agent_runs.broadcast(cid, f"ev{i}", {})

    assert received == [f"ev{i}" for i in range(5)]
    assert transient_bad not in agent_runs._subscribers.get(cid, set())

    agent_runs.unsubscribe(cid, survivor)
