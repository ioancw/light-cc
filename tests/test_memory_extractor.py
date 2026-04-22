"""Tests for the auto-memory extractor (core/memory_extractor.py)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.db_models import AuditEvent, Conversation, Memory, Message, User
from core.memory_extractor import (
    _parse_model_output,
    _validate_item,
    extract_memories_from_conversation,
)


# ── Helpers ────────────────────────────────────────────────────────────

def _fake_response(text: str) -> SimpleNamespace:
    """Build a mock Anthropic Messages response with a single text block."""
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block])


async def _make_conv_with_messages(
    db: AsyncSession,
    user_id: str,
    n: int = 4,
) -> str:
    conv = Conversation(user_id=user_id, title="t")
    db.add(conv)
    await db.flush()
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        db.add(Message(
            conversation_id=conv.id,
            role=role,
            content=json.dumps(f"message {i} from {role}"),
        ))
    await db.commit()
    await db.refresh(conv)
    return conv.id


@pytest_asyncio.fixture
async def extractor_db(test_db: AsyncSession, test_user: User):
    """Patch every get_db in the extractor's call path + save_memory + enqueue."""

    @asynccontextmanager
    async def _get_test_db():
        yield test_db

    with patch("core.memory_extractor.get_db", side_effect=_get_test_db), \
         patch("memory.manager._get_db", side_effect=_get_test_db):
        yield test_db, test_user


# ── Pure helpers ───────────────────────────────────────────────────────

class TestParseModelOutput:
    def test_plain_json_array(self):
        raw = '[{"title": "x", "content": "y"}]'
        assert _parse_model_output(raw) == [{"title": "x", "content": "y"}]

    def test_handles_code_fence(self):
        raw = '```json\n[{"title": "x", "content": "y"}]\n```'
        assert _parse_model_output(raw) == [{"title": "x", "content": "y"}]

    def test_strips_prose_wrapper(self):
        raw = 'Here you go: [{"title": "x", "content": "y"}]. Thanks!'
        assert _parse_model_output(raw) == [{"title": "x", "content": "y"}]

    def test_garbage_returns_empty(self):
        assert _parse_model_output("not json at all") == []

    def test_non_array_returns_empty(self):
        assert _parse_model_output('{"title": "x"}') == []

    def test_filters_non_dict_items(self):
        raw = '[{"title": "x", "content": "y"}, "nope", 42]'
        assert _parse_model_output(raw) == [{"title": "x", "content": "y"}]


class TestValidateItem:
    def test_minimal_ok(self):
        out = _validate_item({"title": "T", "content": "C"})
        assert out == {"title": "T", "content": "C", "memory_type": "note", "tags": []}

    def test_missing_fields_rejected(self):
        assert _validate_item({"title": "", "content": "C"}) is None
        assert _validate_item({"title": "T", "content": ""}) is None

    def test_unknown_type_falls_back(self):
        out = _validate_item({"title": "T", "content": "C", "memory_type": "bogus"})
        assert out["memory_type"] == "note"

    def test_tags_are_cleaned(self):
        out = _validate_item({
            "title": "T", "content": "C",
            "tags": ["a", "", "  ", "b"],
        })
        assert out["tags"] == ["a", "b"]


# ── End-to-end job ─────────────────────────────────────────────────────

class TestExtractJob:
    @pytest.mark.asyncio
    async def test_happy_path_saves_memories(self, extractor_db):
        db, user = extractor_db
        # Opt the user in
        await db.execute(
            update(User).where(User.id == user.id).values(
                auto_extract_enabled=True,
                auto_extract_min_messages=2,
            )
        )
        await db.commit()

        conv_id = await _make_conv_with_messages(db, user.id, n=4)

        payload = json.dumps([
            {"title": "User likes dark mode", "content": "UI preference",
             "memory_type": "preference", "tags": ["ui"]},
            {"title": "Project uses pytest", "content": "Test framework",
             "memory_type": "fact", "tags": []},
        ])
        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(return_value=_fake_response(payload))

        with patch("core.memory_extractor.get_client", return_value=fake_client):
            saved = await extract_memories_from_conversation(conv_id, user.id)

        assert saved == 2
        rows = list((await db.execute(
            select(Memory).where(Memory.user_id == user.id)
        )).scalars().all())
        assert {m.title for m in rows} == {
            "User likes dark mode", "Project uses pytest",
        }
        assert all(m.source == "auto" for m in rows)
        assert all(m.source_conversation_id == conv_id for m in rows)

        # AuditEvent recorded
        events = list((await db.execute(
            select(AuditEvent).where(AuditEvent.user_id == user.id)
        )).scalars().all())
        assert any(e.tool_name == "auto_memory_extract" for e in events)

    @pytest.mark.asyncio
    async def test_disabled_user_is_noop(self, extractor_db):
        db, user = extractor_db
        # user.auto_extract_enabled defaults to False
        conv_id = await _make_conv_with_messages(db, user.id, n=10)

        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock()

        with patch("core.memory_extractor.get_client", return_value=fake_client):
            saved = await extract_memories_from_conversation(conv_id, user.id)

        assert saved == 0
        fake_client.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_below_min_messages_is_skipped(self, extractor_db):
        db, user = extractor_db
        await db.execute(
            update(User).where(User.id == user.id).values(
                auto_extract_enabled=True,
                auto_extract_min_messages=8,
            )
        )
        await db.commit()

        conv_id = await _make_conv_with_messages(db, user.id, n=3)

        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock()

        with patch("core.memory_extractor.get_client", return_value=fake_client):
            saved = await extract_memories_from_conversation(conv_id, user.id)

        assert saved == 0
        fake_client.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_against_existing_titles(self, extractor_db):
        db, user = extractor_db
        await db.execute(
            update(User).where(User.id == user.id).values(
                auto_extract_enabled=True,
                auto_extract_min_messages=2,
            )
        )
        await db.commit()

        # Pre-existing memory with a title the model will try to re-emit
        db.add(Memory(
            user_id=user.id, title="User likes dark mode",
            content="existing", memory_type="preference",
        ))
        await db.commit()

        conv_id = await _make_conv_with_messages(db, user.id, n=4)

        payload = json.dumps([
            {"title": "User likes dark mode", "content": "dup",
             "memory_type": "preference"},
            {"title": "Fresh insight", "content": "new",
             "memory_type": "note"},
        ])
        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(return_value=_fake_response(payload))

        with patch("core.memory_extractor.get_client", return_value=fake_client):
            saved = await extract_memories_from_conversation(conv_id, user.id)

        assert saved == 1
        # Exactly one "dark mode" row — the pre-existing one, not a new auto copy
        dark = list((await db.execute(
            select(Memory).where(
                Memory.user_id == user.id,
                Memory.title == "User likes dark mode",
            )
        )).scalars().all())
        assert len(dark) == 1
        assert dark[0].source == "user"  # unchanged

    @pytest.mark.asyncio
    async def test_malformed_model_output_is_silent(self, extractor_db):
        db, user = extractor_db
        await db.execute(
            update(User).where(User.id == user.id).values(
                auto_extract_enabled=True,
                auto_extract_min_messages=2,
            )
        )
        await db.commit()

        conv_id = await _make_conv_with_messages(db, user.id, n=4)

        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(
            return_value=_fake_response("I'm just chatting, no JSON for you.")
        )

        with patch("core.memory_extractor.get_client", return_value=fake_client):
            saved = await extract_memories_from_conversation(conv_id, user.id)

        assert saved == 0
        rows = list((await db.execute(
            select(Memory).where(Memory.user_id == user.id)
        )).scalars().all())
        assert rows == []

    @pytest.mark.asyncio
    async def test_model_exception_is_swallowed(self, extractor_db):
        db, user = extractor_db
        await db.execute(
            update(User).where(User.id == user.id).values(
                auto_extract_enabled=True,
                auto_extract_min_messages=2,
            )
        )
        await db.commit()

        conv_id = await _make_conv_with_messages(db, user.id, n=4)

        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("core.memory_extractor.get_client", return_value=fake_client):
            saved = await extract_memories_from_conversation(conv_id, user.id)

        assert saved == 0

    @pytest.mark.asyncio
    async def test_unknown_conversation_is_noop(self, extractor_db):
        db, user = extractor_db
        await db.execute(
            update(User).where(User.id == user.id).values(auto_extract_enabled=True)
        )
        await db.commit()

        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock()

        with patch("core.memory_extractor.get_client", return_value=fake_client):
            saved = await extract_memories_from_conversation("does-not-exist", user.id)

        assert saved == 0
        fake_client.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_caps_at_five_items(self, extractor_db):
        db, user = extractor_db
        await db.execute(
            update(User).where(User.id == user.id).values(
                auto_extract_enabled=True,
                auto_extract_min_messages=2,
            )
        )
        await db.commit()

        conv_id = await _make_conv_with_messages(db, user.id, n=4)

        payload = json.dumps([
            {"title": f"Title {i}", "content": "c"} for i in range(10)
        ])
        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(return_value=_fake_response(payload))

        with patch("core.memory_extractor.get_client", return_value=fake_client):
            saved = await extract_memories_from_conversation(conv_id, user.id)

        assert saved == 5
