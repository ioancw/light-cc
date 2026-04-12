"""Tests for the scheduler (core/scheduler.py)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.scheduler import (
    _compute_next_run,
    _scheduler_loop,
    _user_senders,
    register_user_sender,
    start_scheduler,
    stop_scheduler,
    unregister_user_sender,
)


class TestComputeNextRun:
    def test_every_minute(self):
        base = datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)
        next_run = _compute_next_run("* * * * *", base)
        assert next_run == datetime(2026, 4, 7, 12, 1, 0, tzinfo=timezone.utc)

    def test_hourly(self):
        base = datetime(2026, 4, 7, 12, 30, 0, tzinfo=timezone.utc)
        next_run = _compute_next_run("0 * * * *", base)
        assert next_run == datetime(2026, 4, 7, 13, 0, 0, tzinfo=timezone.utc)

    def test_daily_at_noon(self):
        base = datetime(2026, 4, 7, 13, 0, 0, tzinfo=timezone.utc)
        next_run = _compute_next_run("0 12 * * *", base)
        assert next_run == datetime(2026, 4, 8, 12, 0, 0, tzinfo=timezone.utc)

    def test_weekly_monday(self):
        # 2026-04-07 is a Tuesday
        base = datetime(2026, 4, 7, 0, 0, 0, tzinfo=timezone.utc)
        next_run = _compute_next_run("0 0 * * 1", base)  # Monday
        assert next_run.weekday() == 0  # Monday
        assert next_run > base

    def test_uses_utc_now_when_no_base(self):
        next_run = _compute_next_run("* * * * *")
        assert next_run.tzinfo is not None
        assert next_run > datetime.now(timezone.utc) - timedelta(seconds=5)

    def test_complex_expression(self):
        base = datetime(2026, 4, 7, 0, 0, 0, tzinfo=timezone.utc)
        next_run = _compute_next_run("30 9 * * 1-5", base)  # 9:30 weekdays
        assert next_run.hour == 9
        assert next_run.minute == 30
        assert next_run.weekday() < 5  # weekday


class TestUserSenderRegistry:
    def test_register_and_unregister(self):
        send_fn = AsyncMock()
        user_id = "test-user-sched"

        register_user_sender(user_id, send_fn)
        assert send_fn in _user_senders.get(user_id, set())

        unregister_user_sender(user_id, send_fn)
        assert user_id not in _user_senders or send_fn not in _user_senders[user_id]

    def test_unregister_nonexistent(self):
        unregister_user_sender("nobody", AsyncMock())  # should not raise

    def test_multiple_senders_per_user(self):
        user_id = "multi-sender-user"
        fn1 = AsyncMock()
        fn2 = AsyncMock()

        register_user_sender(user_id, fn1)
        register_user_sender(user_id, fn2)
        assert len(_user_senders.get(user_id, set())) == 2

        unregister_user_sender(user_id, fn1)
        assert fn2 in _user_senders.get(user_id, set())

        # Cleanup
        unregister_user_sender(user_id, fn2)


class TestComputeNextRunTimezone:
    def test_timezone_aware(self):
        """Next run should respect user timezone."""
        base = datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)
        # 9:00 AM in any timezone should produce a valid next_run
        next_run = _compute_next_run("0 9 * * *", base, user_tz="US/Eastern")
        assert next_run > base
        assert next_run.tzinfo is not None

    def test_invalid_cron_raises(self):
        """Invalid cron expression should raise ValueError."""
        with pytest.raises((ValueError, KeyError)):
            _compute_next_run("invalid cron expression")

    def test_cron_with_day_of_week(self):
        """Weekday-only cron should skip weekends."""
        # 2026-04-11 is a Saturday
        base = datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc)
        next_run = _compute_next_run("0 9 * * 1-5", base)  # weekdays only
        assert next_run.weekday() < 5  # must be a weekday


class TestScheduleExecution:
    @pytest.mark.asyncio
    async def test_permission_check_blocks_risky(self):
        """Scheduled tasks should deny risky commands."""
        from core.permissions import is_blocked, is_risky

        # Risky commands should be denied in scheduled context
        assert is_risky("Bash", {"command": "rm -rf ./build"})

    @pytest.mark.asyncio
    async def test_permission_check_allows_safe(self):
        from core.permissions import is_blocked, is_risky

        assert not is_blocked("Bash", {"command": "echo hello"})
        assert not is_risky("Bash", {"command": "echo hello"})


class TestNotifyUser:
    @pytest.mark.asyncio
    async def test_notify_user_calls_senders(self):
        from core.scheduler import _notify_user

        send_fn = AsyncMock()
        user_id = "notify-test-user"
        register_user_sender(user_id, send_fn)

        await _notify_user(user_id, "test-schedule", "Task completed")

        send_fn.assert_called_once()
        call_args = send_fn.call_args
        assert call_args[0][0] == "notification"
        assert call_args[0][1]["schedule_name"] == "test-schedule"

        # Cleanup
        unregister_user_sender(user_id, send_fn)

    @pytest.mark.asyncio
    async def test_notify_user_no_senders(self):
        from core.scheduler import _notify_user

        # Should not raise even with no senders
        await _notify_user("nobody", "test", "message")

    @pytest.mark.asyncio
    async def test_notify_schedule_result(self):
        from core.scheduler import _notify_user_schedule_result

        send_fn = AsyncMock()
        user_id = "result-test-user"
        register_user_sender(user_id, send_fn)

        await _notify_user_schedule_result(user_id, "my-schedule", "completed", "conv-123")

        send_fn.assert_called_once()
        call_args = send_fn.call_args
        assert call_args[0][0] == "schedule_result"
        assert call_args[0][1]["status"] == "completed"
        assert call_args[0][1]["conversation_id"] == "conv-123"

        # Cleanup
        unregister_user_sender(user_id, send_fn)


# ── Cron-triggered AgentDefinition branch ────────────────────────────────

@pytest_asyncio.fixture
async def scheduler_db(test_db: AsyncSession, test_user):
    """Patch core.scheduler.get_db (and agent_crud/agent_runner) to use test_db."""

    async def _get_test_db():
        return test_db

    with patch("core.scheduler.get_db", side_effect=_get_test_db), \
         patch("core.agent_crud.get_db", side_effect=_get_test_db), \
         patch("core.agent_runner.get_db", side_effect=_get_test_db):
        yield test_db, test_user


class _StopLoop(Exception):
    """Sentinel used to break out of _scheduler_loop after one iteration."""


async def _run_one_iteration():
    """Drive _scheduler_loop through exactly one pass, then abort.

    We replace asyncio.sleep (inside the loop module) with a coroutine that
    raises _StopLoop after the first call. The loop catches generic Exception
    around the body but not around sleep, so _StopLoop escapes cleanly.
    """
    async def _raise_stop(_seconds):
        raise _StopLoop()

    with patch("core.scheduler.asyncio.sleep", side_effect=_raise_stop):
        try:
            await _scheduler_loop()
        except _StopLoop:
            pass


class TestCronAgentBranch:
    @pytest.mark.asyncio
    async def test_due_cron_agent_is_triggered(self, scheduler_db):
        """Enabled cron agents with next_run_at in the past should fire."""
        from core.agent_crud import create_agent
        from core.db_models import AgentDefinition
        from sqlalchemy import update as sa_update

        db, user = scheduler_db
        agent = await create_agent(
            user_id=user.id, name="cron-fire", description="d", system_prompt="p",
            trigger="cron", cron_expression="0 * * * *",
        )
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        await db.execute(
            sa_update(AgentDefinition)
            .where(AgentDefinition.id == agent.id)
            .values(next_run_at=past)
        )
        await db.commit()

        trigger_mock = AsyncMock()
        with patch("core.agent_runner.trigger_agent_run", new=trigger_mock):
            await _run_one_iteration()

        trigger_mock.assert_awaited_once()
        call_args = trigger_mock.await_args
        fired_agent = call_args.args[0]
        assert fired_agent.id == agent.id
        assert call_args.kwargs.get("trigger_type") == "cron"

        refreshed = (await db.execute(
            select(AgentDefinition).where(AgentDefinition.id == agent.id),
        )).scalar_one()
        stored = refreshed.next_run_at
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=timezone.utc)
        assert stored > past

    @pytest.mark.asyncio
    async def test_disabled_cron_agent_is_skipped(self, scheduler_db):
        from core.agent_crud import create_agent
        from core.db_models import AgentDefinition
        from sqlalchemy import update as sa_update

        db, user = scheduler_db
        agent = await create_agent(
            user_id=user.id, name="cron-off", description="d", system_prompt="p",
            trigger="cron", cron_expression="0 * * * *",
        )
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        await db.execute(
            sa_update(AgentDefinition)
            .where(AgentDefinition.id == agent.id)
            .values(next_run_at=past, enabled=False)
        )
        await db.commit()

        trigger_mock = AsyncMock()
        with patch("core.agent_runner.trigger_agent_run", new=trigger_mock):
            await _run_one_iteration()

        trigger_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_future_cron_agent_is_skipped(self, scheduler_db):
        from core.agent_crud import create_agent
        from core.db_models import AgentDefinition
        from sqlalchemy import update as sa_update

        db, user = scheduler_db
        agent = await create_agent(
            user_id=user.id, name="cron-later", description="d", system_prompt="p",
            trigger="cron", cron_expression="0 * * * *",
        )
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        await db.execute(
            sa_update(AgentDefinition)
            .where(AgentDefinition.id == agent.id)
            .values(next_run_at=future)
        )
        await db.commit()

        trigger_mock = AsyncMock()
        with patch("core.agent_runner.trigger_agent_run", new=trigger_mock):
            await _run_one_iteration()

        trigger_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_manual_agent_is_skipped(self, scheduler_db):
        """Manual agents must not be picked up by the cron branch."""
        from core.agent_crud import create_agent
        from core.db_models import AgentDefinition
        from sqlalchemy import update as sa_update

        db, user = scheduler_db
        agent = await create_agent(
            user_id=user.id, name="manual1", description="d", system_prompt="p",
        )
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        await db.execute(
            sa_update(AgentDefinition)
            .where(AgentDefinition.id == agent.id)
            .values(next_run_at=past)
        )
        await db.commit()

        trigger_mock = AsyncMock()
        with patch("core.agent_runner.trigger_agent_run", new=trigger_mock):
            await _run_one_iteration()

        trigger_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_trigger_failure_does_not_advance_next_run(self, scheduler_db):
        """If trigger_agent_run raises, next_run_at should NOT advance."""
        from core.agent_crud import create_agent
        from core.db_models import AgentDefinition
        from sqlalchemy import update as sa_update

        db, user = scheduler_db
        agent = await create_agent(
            user_id=user.id, name="cron-bust", description="d", system_prompt="p",
            trigger="cron", cron_expression="0 * * * *",
        )
        past_floor = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(microsecond=0)
        await db.execute(
            sa_update(AgentDefinition)
            .where(AgentDefinition.id == agent.id)
            .values(next_run_at=past_floor)
        )
        await db.commit()

        trigger_mock = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("core.agent_runner.trigger_agent_run", new=trigger_mock):
            await _run_one_iteration()

        trigger_mock.assert_awaited_once()
        refreshed = (await db.execute(
            select(AgentDefinition).where(AgentDefinition.id == agent.id),
        )).scalar_one()
        # next_run_at still in the past — advancement happens only on success
        stored = refreshed.next_run_at
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=timezone.utc)
        assert stored == past_floor


class TestStartSchedulerInit:
    @pytest.mark.asyncio
    async def test_initialises_next_run_at_for_cron_agent(self, scheduler_db):
        """start_scheduler fills in next_run_at for enabled cron agents without one."""
        from core.agent_crud import create_agent
        from core.db_models import AgentDefinition
        from sqlalchemy import update as sa_update

        db, user = scheduler_db
        agent = await create_agent(
            user_id=user.id, name="cron-init", description="d", system_prompt="p",
            trigger="cron", cron_expression="0 * * * *",
        )
        await db.execute(
            sa_update(AgentDefinition)
            .where(AgentDefinition.id == agent.id)
            .values(next_run_at=None)
        )
        await db.commit()

        # Prevent the background loop task from actually starting
        def _swallow(coro):
            coro.close()
            return MagicMock()
        with patch("core.scheduler.asyncio.create_task", new=_swallow):
            await start_scheduler()

        refreshed = (await db.execute(
            select(AgentDefinition).where(AgentDefinition.id == agent.id),
        )).scalar_one()
        assert refreshed.next_run_at is not None

    @pytest.mark.asyncio
    async def test_does_not_touch_manual_agents(self, scheduler_db):
        from core.agent_crud import create_agent
        from core.db_models import AgentDefinition

        db, user = scheduler_db
        agent = await create_agent(
            user_id=user.id, name="manual-init", description="d", system_prompt="p",
        )
        assert agent.next_run_at is None

        def _swallow(coro):
            coro.close()
            return MagicMock()
        with patch("core.scheduler.asyncio.create_task", new=_swallow):
            await start_scheduler()

        refreshed = (await db.execute(
            select(AgentDefinition).where(AgentDefinition.id == agent.id),
        )).scalar_one()
        assert refreshed.next_run_at is None
