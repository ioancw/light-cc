"""Tests for conversation persistence (core/session.py DB operations)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import pytest_asyncio

from core.session import (
    _derive_title,
    conv_session_set,
    create_connection,
    create_conv_session,
    load_conversation,
    save_conversation,
    fork_conversation,
)


class TestDeriveTitle:
    def test_simple_text(self):
        messages = [{"role": "user", "content": "Hello, how are you?"}]
        assert _derive_title(messages) == "Hello, how are you?"

    def test_long_text_truncated(self):
        long_text = "x" * 200
        messages = [{"role": "user", "content": long_text}]
        title = _derive_title(messages)
        assert len(title) <= 84  # 80 + "..."
        assert title.endswith("...")

    def test_block_content(self):
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "Block content here"},
        ]}]
        assert _derive_title(messages) == "Block content here"

    def test_no_user_message(self):
        messages = [{"role": "assistant", "content": "I said something"}]
        assert _derive_title(messages) == "New conversation"

    def test_empty_messages(self):
        assert _derive_title([]) == "New conversation"

    def test_skips_empty_user_content(self):
        messages = [
            {"role": "user", "content": ""},
            {"role": "user", "content": "Second message"},
        ]
        assert _derive_title(messages) == "Second message"


class TestSaveConversation:
    @pytest.mark.asyncio
    async def test_save_new_conversation(self, test_db, test_user, clean_sessions):
        create_connection("s1", user_id=test_user.id)
        create_conv_session("c1", "s1")
        conv_session_set("c1", "messages", [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "Hi!"}]},
        ])

        with patch("core.database.get_db", return_value=test_db):
            conv_id = await save_conversation("c1")

        assert conv_id is not None

    @pytest.mark.asyncio
    async def test_save_empty_messages_returns_none(self, clean_sessions):
        create_connection("s1")
        create_conv_session("c1", "s1")
        # messages is empty by default

        result = await save_conversation("c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_save_nonexistent_cid_returns_none(self, clean_sessions):
        result = await save_conversation("nonexistent")
        assert result is None


class TestLoadConversation:
    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self, test_db, test_user, clean_sessions):
        create_connection("s1", user_id=test_user.id)
        create_conv_session("c1", "s1")

        original_messages = [
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": [{"type": "text", "text": "4"}]},
        ]
        conv_session_set("c1", "messages", original_messages)

        with patch("core.database.get_db", return_value=test_db):
            conv_id = await save_conversation("c1")
            loaded = await load_conversation(conv_id)

        assert len(loaded) == 2
        assert loaded[0]["role"] == "user"
        assert loaded[0]["content"] == "What is 2+2?"
        assert loaded[1]["role"] == "assistant"
        # Content was JSON-encoded list, should be deserialized back
        assert isinstance(loaded[1]["content"], list)

    @pytest.mark.asyncio
    async def test_load_nonexistent_conversation(self, test_db):
        with patch("core.database.get_db", return_value=test_db):
            loaded = await load_conversation("nonexistent-id")
        assert loaded == []


class TestForkConversation:
    @pytest.mark.asyncio
    async def test_fork_creates_new_with_messages(self, test_db, test_user, clean_sessions):
        create_connection("s1", user_id=test_user.id)
        create_conv_session("c1", "s1")
        conv_session_set("c1", "messages", [
            {"role": "user", "content": "Original message"},
            {"role": "assistant", "content": [{"type": "text", "text": "Response"}]},
        ])

        with patch("core.database.get_db", return_value=test_db):
            conv_id = await save_conversation("c1")
            new_conv_id, messages = await fork_conversation(conv_id, test_user.id)

        assert new_conv_id is not None
        assert new_conv_id != conv_id
        assert len(messages) == 2
        assert messages[0]["content"] == "Original message"

    @pytest.mark.asyncio
    async def test_fork_empty_conversation_raises(self, test_db, test_user):
        with patch("core.database.get_db", return_value=test_db):
            with pytest.raises(ValueError, match="No messages found"):
                await fork_conversation("nonexistent-id", test_user.id)

    @pytest.mark.asyncio
    async def test_fork_other_users_conversation_raises(self, test_db, test_user, clean_sessions):
        """A user cannot fork another user's conversation even if they know the ID."""
        from core.auth import hash_password
        from core.db_models import User

        create_connection("s1", user_id=test_user.id)
        create_conv_session("c1", "s1")
        conv_session_set("c1", "messages", [
            {"role": "user", "content": "Confidential message"},
        ])

        with patch("core.database.get_db", return_value=test_db):
            conv_id = await save_conversation("c1")

            intruder = User(
                email="intruder@x.com",
                password_hash=hash_password("x"),
                display_name="Intruder",
            )
            test_db.add(intruder)
            await test_db.commit()
            await test_db.refresh(intruder)

            with pytest.raises(ValueError, match="No messages found"):
                await fork_conversation(conv_id, intruder.id)

    @pytest.mark.asyncio
    async def test_load_conversation_scoped_by_user(self, test_db, test_user, clean_sessions):
        """load_conversation(..., user_id=...) must return [] for non-owners."""
        from core.auth import hash_password
        from core.db_models import User

        create_connection("s1", user_id=test_user.id)
        create_conv_session("c1", "s1")
        conv_session_set("c1", "messages", [
            {"role": "user", "content": "Secret"},
        ])

        with patch("core.database.get_db", return_value=test_db):
            conv_id = await save_conversation("c1")

            intruder = User(
                email="intruder2@x.com",
                password_hash=hash_password("x"),
                display_name="Intruder",
            )
            test_db.add(intruder)
            await test_db.commit()
            await test_db.refresh(intruder)

            # Owner sees messages; intruder sees nothing.
            owner_msgs = await load_conversation(conv_id, user_id=test_user.id)
            intruder_msgs = await load_conversation(conv_id, user_id=intruder.id)

        assert len(owner_msgs) == 1
        assert intruder_msgs == []

    @pytest.mark.asyncio
    async def test_fork_title_has_suffix(self, test_db, test_user, clean_sessions):
        """Forked conversation title should include (fork) suffix."""
        from core.db_models import Conversation
        from sqlalchemy import select

        create_connection("s1", user_id=test_user.id)
        create_conv_session("c1", "s1")
        conv_session_set("c1", "messages", [
            {"role": "user", "content": "Test fork title"},
        ])

        with patch("core.database.get_db", return_value=test_db):
            conv_id = await save_conversation("c1")
            new_conv_id, _ = await fork_conversation(conv_id, test_user.id)

            # Check the title in the DB
            result = await test_db.execute(
                select(Conversation.title).where(Conversation.id == new_conv_id)
            )
            title = result.scalar_one()
            assert "(fork)" in title


class TestSaveConversationIdempotent:
    @pytest.mark.asyncio
    async def test_save_twice_uses_same_conv_id(self, test_db, test_user, clean_sessions):
        """Saving the same cid twice should update, not create a new conversation."""
        create_connection("s1", user_id=test_user.id)
        create_conv_session("c1", "s1")
        conv_session_set("c1", "messages", [
            {"role": "user", "content": "First"},
        ])

        with patch("core.database.get_db", return_value=test_db):
            conv_id_1 = await save_conversation("c1")

            # Add a message and save again
            from core.session import conv_session_get
            msgs = conv_session_get("c1", "messages")
            msgs.append({"role": "assistant", "content": [{"type": "text", "text": "Reply"}]})

            conv_id_2 = await save_conversation("c1")

        assert conv_id_1 == conv_id_2

        # Verify messages were updated
        with patch("core.database.get_db", return_value=test_db):
            loaded = await load_conversation(conv_id_1)
        assert len(loaded) == 2
