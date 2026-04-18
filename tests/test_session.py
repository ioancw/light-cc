"""Tests for session state management (core/session.py)."""

from __future__ import annotations

import pytest
import pytest_asyncio

from core.session import (
    _connections,
    _conn_convs,
    _conv_sessions,
    _current_cid,
    _current_session_id,
    connection_get,
    connection_set,
    conv_session_get,
    conv_session_set,
    create_connection,
    create_conv_session,
    current_session_get,
    current_session_set,
    destroy_connection,
    destroy_conv_session,
    get_connection,
    get_connection_cids,
    get_conv_session,
    get_or_create_conv_session,
    set_current_cid,
    set_current_session,
)


class TestConnectionLifecycle:
    def test_create_connection(self, clean_sessions):
        conn = create_connection("s1", user_id="user1")
        assert conn["user_id"] == "user1"
        assert conn["permission_mode"] == "default"
        assert get_connection("s1") is conn

    def test_create_connection_default_user(self, clean_sessions):
        conn = create_connection("s1")
        assert conn["user_id"] == "default"

    def test_get_nonexistent_connection(self, clean_sessions):
        assert get_connection("nonexistent") is None

    def test_connection_get_set(self, clean_sessions):
        create_connection("s1", user_id="u1")
        assert connection_get("s1", "user_id") == "u1"
        connection_set("s1", "permission_mode", "auto")
        assert connection_get("s1", "permission_mode") == "auto"

    def test_connection_get_nonexistent(self, clean_sessions):
        assert connection_get("missing", "user_id") is None

    def test_connection_set_nonexistent_noop(self, clean_sessions):
        connection_set("missing", "foo", "bar")  # should not raise

    def test_destroy_connection(self, clean_sessions):
        create_connection("s1", user_id="u1")
        cid = "c1"
        create_conv_session(cid, "s1")
        assert get_conv_session(cid) is not None

        destroy_connection("s1")
        assert get_connection("s1") is None
        # Conversation sub-sessions intentionally outlive the connection so
        # in-flight agent tasks survive WS reconnects. Explicit
        # destroy_conv_session is required to evict them.
        assert get_conv_session(cid) is not None
        assert "s1" not in _conn_convs


class TestConversationSessionLifecycle:
    def test_create_conv_session(self, clean_sessions):
        create_connection("s1", user_id="u1")
        conv = create_conv_session("c1", "s1")
        assert conv["messages"] == []
        assert conv["datasets"] == {}
        assert conv["_conn_id"] == "s1"
        assert "c1" in _conn_convs["s1"]

    def test_get_or_create_existing(self, clean_sessions):
        create_connection("s1")
        conv1 = create_conv_session("c1", "s1")
        conv2 = get_or_create_conv_session("c1", "s1")
        assert conv1 is conv2

    def test_get_or_create_new(self, clean_sessions):
        create_connection("s1")
        conv = get_or_create_conv_session("c1", "s1")
        assert conv is not None
        assert conv["messages"] == []

    def test_conv_session_get_set(self, clean_sessions):
        create_connection("s1")
        create_conv_session("c1", "s1")
        conv_session_set("c1", "active_model", "claude-opus-4-6")
        assert conv_session_get("c1", "active_model") == "claude-opus-4-6"

    def test_conv_session_get_nonexistent(self, clean_sessions):
        assert conv_session_get("missing", "messages") is None

    def test_destroy_conv_session(self, clean_sessions):
        create_connection("s1")
        create_conv_session("c1", "s1")
        assert "c1" in _conn_convs["s1"]

        destroy_conv_session("c1")
        assert get_conv_session("c1") is None
        assert "c1" not in _conn_convs.get("s1", set())

    def test_multiple_conversations_per_connection(self, clean_sessions):
        create_connection("s1")
        create_conv_session("c1", "s1")
        create_conv_session("c2", "s1")
        create_conv_session("c3", "s1")

        cids = get_connection_cids("s1")
        assert cids == {"c1", "c2", "c3"}

    def test_conv_defaults_are_independent(self, clean_sessions):
        """Each conv session should get its own copy of defaults (no shared references)."""
        create_connection("s1")
        conv1 = create_conv_session("c1", "s1")
        conv2 = create_conv_session("c2", "s1")

        conv1["messages"].append({"role": "user", "content": "hi"})
        assert len(conv2["messages"]) == 0  # should not be affected


class TestContextVars:
    def test_current_session_get_from_connection(self, clean_sessions):
        create_connection("s1", user_id="u1")
        set_current_session("s1")
        set_current_cid("")

        assert current_session_get("user_id") == "u1"

    def test_current_session_get_conv_overrides_connection(self, clean_sessions):
        create_connection("s1", user_id="u1")
        create_conv_session("c1", "s1")
        conv_session_set("c1", "active_model", "custom-model")

        set_current_session("s1")
        set_current_cid("c1")

        assert current_session_get("active_model") == "custom-model"

    def test_current_session_get_falls_back_to_connection(self, clean_sessions):
        create_connection("s1", user_id="u1")
        create_conv_session("c1", "s1")

        set_current_session("s1")
        set_current_cid("c1")

        # user_id is on connection, not conv
        assert current_session_get("user_id") == "u1"

    def test_current_session_set_targets_conv(self, clean_sessions):
        create_connection("s1")
        create_conv_session("c1", "s1")

        set_current_session("s1")
        set_current_cid("c1")

        current_session_set("messages", [{"role": "user", "content": "test"}])
        assert len(conv_session_get("c1", "messages")) == 1

    def test_current_session_set_targets_connection(self, clean_sessions):
        create_connection("s1", user_id="u1")

        set_current_session("s1")
        set_current_cid("")

        current_session_set("permission_mode", "auto")
        assert connection_get("s1", "permission_mode") == "auto"

    def test_current_session_get_returns_none_for_unknown_key(self, clean_sessions):
        create_connection("s1")
        set_current_session("s1")
        set_current_cid("")
        assert current_session_get("nonexistent_key") is None


class TestGetConnectionCids:
    def test_empty_when_no_convs(self, clean_sessions):
        create_connection("s1")
        assert get_connection_cids("s1") == set()

    def test_returns_copy(self, clean_sessions):
        create_connection("s1")
        create_conv_session("c1", "s1")
        cids = get_connection_cids("s1")
        cids.add("c999")
        assert "c999" not in get_connection_cids("s1")

    def test_nonexistent_session(self, clean_sessions):
        assert get_connection_cids("missing") == set()


# ── Auto-memory extraction enqueue hook ───────────────────────────────

class TestMaybeEnqueueExtract:
    """Verifies the opt-in debouncing logic in ``_maybe_enqueue_extract``.

    The function should:
    - Do nothing when the user has ``auto_extract_enabled=False``.
    - Do nothing until the conversation has ``auto_extract_min_messages`` total
      messages (threshold check).
    - Do nothing again until another ``auto_extract_min_messages`` messages
      have been appended since the previous enqueue (debounce).
    - Enqueue the job otherwise, with the conversation_id and user_id.
    """

    @pytest.mark.asyncio
    async def test_skip_when_disabled(self, test_db, test_user, monkeypatch):
        from core import session as sess

        calls = []

        async def _fake_enqueue(name, **kwargs):
            calls.append((name, kwargs))

        async def _fake_get_db():
            return test_db

        monkeypatch.setattr("core.job_queue.enqueue", _fake_enqueue)
        monkeypatch.setattr("core.database.get_db", _fake_get_db)

        # User defaults to auto_extract_enabled=False
        cs = {"messages": [{"role": "user", "content": "a"}] * 10}
        await sess._maybe_enqueue_extract(cs, test_user.id, "conv1")
        assert calls == []

    @pytest.mark.asyncio
    async def test_skip_below_threshold(self, test_db, test_user, monkeypatch):
        from core import session as sess

        test_user.auto_extract_enabled = True
        test_user.auto_extract_min_messages = 4
        test_db.add(test_user)
        await test_db.commit()

        calls = []

        async def _fake_enqueue(name, **kwargs):
            calls.append((name, kwargs))

        async def _fake_get_db():
            return test_db

        monkeypatch.setattr("core.job_queue.enqueue", _fake_enqueue)
        monkeypatch.setattr("core.database.get_db", _fake_get_db)

        cs = {"messages": [{"role": "user", "content": "a"}] * 3}
        await sess._maybe_enqueue_extract(cs, test_user.id, "conv1")
        assert calls == []

    @pytest.mark.asyncio
    async def test_enqueues_when_enabled_and_above_threshold(
        self, test_db, test_user, monkeypatch,
    ):
        from core import session as sess

        test_user.auto_extract_enabled = True
        test_user.auto_extract_min_messages = 4
        test_db.add(test_user)
        await test_db.commit()

        calls = []

        async def _fake_enqueue(name, **kwargs):
            calls.append((name, kwargs))

        async def _fake_get_db():
            return test_db

        monkeypatch.setattr("core.job_queue.enqueue", _fake_enqueue)
        monkeypatch.setattr("core.database.get_db", _fake_get_db)

        cs = {"messages": [{"role": "user", "content": "a"}] * 6}
        await sess._maybe_enqueue_extract(cs, test_user.id, "conv-x")

        assert len(calls) == 1
        name, kwargs = calls[0]
        assert name == "extract_memories_from_conversation"
        assert kwargs == {"conversation_id": "conv-x", "user_id": test_user.id}
        # Debounce state recorded on the conv session
        assert cs["_last_extract_msg_count"] == 6

    @pytest.mark.asyncio
    async def test_debounce_waits_for_next_batch(
        self, test_db, test_user, monkeypatch,
    ):
        from core import session as sess

        test_user.auto_extract_enabled = True
        test_user.auto_extract_min_messages = 4
        test_db.add(test_user)
        await test_db.commit()

        calls = []

        async def _fake_enqueue(name, **kwargs):
            calls.append((name, kwargs))

        async def _fake_get_db():
            return test_db

        monkeypatch.setattr("core.job_queue.enqueue", _fake_enqueue)
        monkeypatch.setattr("core.database.get_db", _fake_get_db)

        # First pass: 6 messages → enqueue once
        cs = {"messages": [{"role": "user", "content": "a"}] * 6}
        await sess._maybe_enqueue_extract(cs, test_user.id, "c1")
        assert len(calls) == 1

        # Add 3 more messages (below the 4-threshold gap) → still only 1 call
        cs["messages"].extend([{"role": "user", "content": "b"}] * 3)
        await sess._maybe_enqueue_extract(cs, test_user.id, "c1")
        assert len(calls) == 1

        # Add 1 more (total 10, 4 since last enqueue) → second call
        cs["messages"].append({"role": "user", "content": "c"})
        await sess._maybe_enqueue_extract(cs, test_user.id, "c1")
        assert len(calls) == 2
        assert cs["_last_extract_msg_count"] == 10
