"""Tests for the scheduler (core/scheduler.py)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.scheduler import (
    _compute_next_run,
    _user_senders,
    register_user_sender,
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
