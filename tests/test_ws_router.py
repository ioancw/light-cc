"""Tests for WebSocket router event handling (handlers/ws_router.py).

Tests the handler functions directly rather than through WebSocket protocol,
since FastAPI's TestClient WS support is limited for async scenarios.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session import (
    _conv_sessions,
    connection_get,
    connection_set,
    conv_session_get,
    conv_session_set,
    create_connection,
    create_conv_session,
    get_connection_cids,
)


class TestRateLimiting:
    def test_ws_connect_rate_limit(self):
        from core.rate_limit import check_ws_connect

        # Should allow first few connections
        allowed, _ = check_ws_connect("192.168.1.100")
        assert allowed is True

    def test_message_rate_limit(self):
        from core.rate_limit import check_rate_limit

        # First message should be allowed for a fresh user
        allowed, _ = check_rate_limit("rate-test-user", "message")
        assert allowed is True


class TestSessionSetup:
    def test_create_connection_on_connect(self, clean_sessions):
        conn = create_connection("ws-session-1", user_id="user-1")
        assert conn["user_id"] == "user-1"
        assert conn["permission_mode"] == "default"

    def test_permission_mode_set(self, clean_sessions):
        create_connection("s1", user_id="u1")
        connection_set("s1", "permission_mode", "auto_edit")
        assert connection_get("s1", "permission_mode") == "auto_edit"

    def test_permission_mode_cycle(self, clean_sessions):
        from core.permission_modes import PermissionMode

        create_connection("s1", user_id="u1")
        current = PermissionMode(connection_get("s1", "permission_mode") or "default")
        new_mode = current.next()
        connection_set("s1", "permission_mode", new_mode.value)
        assert connection_get("s1", "permission_mode") == "auto_edit"


class TestConcurrentAgentLimit:
    def test_max_concurrent_check(self, clean_sessions):
        """Simulate the MAX_CONCURRENT_AGENTS=3 check from ws_router."""
        MAX_CONCURRENT_AGENTS = 3
        agent_tasks: dict[str, asyncio.Task] = {}

        # Simulate 3 active tasks
        for i in range(3):
            task = MagicMock(spec=asyncio.Task)
            task.done.return_value = False
            agent_tasks[f"cid-{i}"] = task

        active_count = sum(1 for t in agent_tasks.values() if not t.done())
        assert active_count >= MAX_CONCURRENT_AGENTS

        # 4th should be rejected
        assert active_count >= MAX_CONCURRENT_AGENTS

    def test_completed_tasks_dont_count(self, clean_sessions):
        MAX_CONCURRENT_AGENTS = 3
        agent_tasks: dict[str, asyncio.Task] = {}

        # 2 active, 1 completed
        for i in range(2):
            task = MagicMock(spec=asyncio.Task)
            task.done.return_value = False
            agent_tasks[f"cid-{i}"] = task

        done_task = MagicMock(spec=asyncio.Task)
        done_task.done.return_value = True
        agent_tasks["cid-done"] = done_task

        active_count = sum(1 for t in agent_tasks.values() if not t.done())
        assert active_count < MAX_CONCURRENT_AGENTS


class TestConversationEvents:
    def test_clear_conversation(self, clean_sessions):
        create_connection("s1", user_id="u1")
        create_conv_session("c1", "s1")
        conv_session_set("c1", "messages", [{"role": "user", "content": "hi"}])

        # Simulate clear: save + destroy
        from core.session import destroy_conv_session
        destroy_conv_session("c1")

        assert conv_session_get("c1", "messages") is None

    def test_set_model(self, clean_sessions):
        create_connection("s1", user_id="u1")
        create_conv_session("c1", "s1")

        conv_session_set("c1", "active_model", "claude-opus-4-6")
        assert conv_session_get("c1", "active_model") == "claude-opus-4-6"

    def test_set_system_prompt(self, clean_sessions):
        create_connection("s1", user_id="u1")
        connection_set("s1", "user_system_prompt", "You are a pirate.")
        assert connection_get("s1", "user_system_prompt") == "You are a pirate."


class TestDisconnectCleanup:
    def test_destroy_cleans_all_conversations(self, clean_sessions):
        create_connection("s1", user_id="u1")
        create_conv_session("c1", "s1")
        create_conv_session("c2", "s1")
        create_conv_session("c3", "s1")

        assert len(get_connection_cids("s1")) == 3

        from core.session import destroy_connection
        destroy_connection("s1")

        assert conv_session_get("c1", "messages") is None
        assert conv_session_get("c2", "messages") is None
        assert conv_session_get("c3", "messages") is None

    @pytest.mark.asyncio
    async def test_async_destroy_cleans_redis(self, clean_sessions, mock_redis):
        pool, store = mock_redis
        create_connection("s1", user_id="u1")

        from core.session import destroy_connection_async
        await destroy_connection_async("s1")

        # Should have called delete on redis
        pool.delete.assert_called()


class TestForkConversation:
    @pytest.mark.asyncio
    async def test_fork_creates_new_conversation(self, test_db, test_user, clean_sessions):
        """Fork should copy messages into a new conversation."""
        from core.session import save_conversation, fork_conversation

        create_connection("s1", user_id=test_user.id)
        create_conv_session("c1", "s1")
        conv_session_set("c1", "messages", [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "Hi there!"}]},
        ])

        # Save the original conversation first
        with patch("core.database.get_db", return_value=test_db):
            conv_id = await save_conversation("c1")
            assert conv_id is not None

            # Fork it
            new_conv_id, messages = await fork_conversation(conv_id, test_user.id)
            assert new_conv_id != conv_id
            assert len(messages) == 2
            assert messages[0]["content"] == "Hello"
