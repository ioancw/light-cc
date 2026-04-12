"""Tests for the DB-backed memory system (memory/manager.py)."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from core.db_models import Memory
from memory.manager import (
    save_memory,
    load_memory,
    read_memory,
    search_memory,
    list_memories,
    delete_memory,
    update_memory,
)


@pytest_asyncio.fixture
async def memory_db(test_db: AsyncSession, test_user):
    """Patch _get_db to return the test database session."""
    from unittest.mock import AsyncMock, patch

    async def _get_test_db():
        return test_db

    with patch("memory.manager._get_db", side_effect=_get_test_db):
        yield test_db, test_user


# ── Save + Read ────────────────────────────────────────────────────────

class TestSaveAndRead:
    @pytest.mark.asyncio
    async def test_save_returns_id(self, memory_db):
        db, user = memory_db
        mem_id = await save_memory(user.id, "Test Note", "This is content")
        assert mem_id  # non-empty string
        assert isinstance(mem_id, str)

    @pytest.mark.asyncio
    async def test_read_by_id(self, memory_db):
        db, user = memory_db
        mem_id = await save_memory(user.id, "Read Test", "Content to read")
        content = await read_memory(user.id, mem_id)
        assert content == "Content to read"

    @pytest.mark.asyncio
    async def test_read_nonexistent_returns_none(self, memory_db):
        _, user = memory_db
        content = await read_memory(user.id, "nonexistent_id_xyz")
        assert content is None

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, memory_db):
        _, user = memory_db
        assert await read_memory(user.id, "../../../etc/passwd") is None
        assert await read_memory(user.id, "..\\windows\\system32") is None


# ── Search ─────────────────────────────────────────────────────────────

class TestSearch:
    @pytest.mark.asyncio
    async def test_search_by_title(self, memory_db):
        _, user = memory_db
        await save_memory(user.id, "Python preferences", "Use type hints")
        await save_memory(user.id, "Git workflow", "Always rebase")

        results = await search_memory(user.id, "Python")
        assert len(results) >= 1
        assert any("Python" in r["title"] for r in results)

    @pytest.mark.asyncio
    async def test_search_by_content(self, memory_db):
        _, user = memory_db
        await save_memory(user.id, "Coding style", "Always use type hints in function signatures")

        results = await search_memory(user.id, "type hints")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_no_results(self, memory_db):
        _, user = memory_db
        results = await search_memory(user.id, "xyznonexistenttermxyz")
        assert len(results) == 0


# ── List ───────────────────────────────────────────────────────────────

class TestList:
    @pytest.mark.asyncio
    async def test_list_memories(self, memory_db):
        _, user = memory_db
        await save_memory(user.id, "Note A", "Content A")
        await save_memory(user.id, "Note B", "Content B")

        entries = await list_memories(user.id)
        assert len(entries) >= 2
        titles = [e["title"] for e in entries]
        assert "Note A" in titles
        assert "Note B" in titles

    @pytest.mark.asyncio
    async def test_list_empty(self, memory_db):
        _, user = memory_db
        entries = await list_memories(user.id)
        # May be empty or have entries from other tests (fixture scope)
        assert isinstance(entries, list)


# ── Delete ─────────────────────────────────────────────────────────────

class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self, memory_db):
        _, user = memory_db
        mem_id = await save_memory(user.id, "To Delete", "Will be removed")
        assert await delete_memory(user.id, mem_id) is True
        assert await read_memory(user.id, mem_id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, memory_db):
        _, user = memory_db
        assert await delete_memory(user.id, "nonexistent_id") is False


# ── User isolation ─────────────────────────────────────────────────────

class TestUserIsolation:
    @pytest.mark.asyncio
    async def test_memories_scoped_to_user(self, memory_db):
        db, user = memory_db
        await save_memory(user.id, "User1 Note", "Private content")

        # A different user should not see this memory
        results = await search_memory("other_user_id_999", "Private content")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_delete_other_users_memory_fails(self, memory_db):
        _, user = memory_db
        mem_id = await save_memory(user.id, "Protected", "Sensitive")
        # Another user trying to delete should fail
        assert await delete_memory("other_user_id_999", mem_id) is False
        # Original user can still read it
        assert await read_memory(user.id, mem_id) is not None


# ── System prompt loading ──────────────────────────────────────────────

class TestLoadMemory:
    @pytest.mark.asyncio
    async def test_load_returns_listing(self, memory_db):
        _, user = memory_db
        await save_memory(user.id, "My Note", "Some content here")

        listing = await load_memory(user.id)
        assert "My Note" in listing
        assert "Available memories" in listing

    @pytest.mark.asyncio
    async def test_load_empty_returns_empty_string(self, memory_db):
        _, user = memory_db
        # Fresh user with a unique ID to ensure no memories exist
        listing = await load_memory("fresh_user_no_memories_999")
        assert listing == ""


# ── Tags + memory_type (S1) ────────────────────────────────────────────

class TestTagsAndTypes:
    @pytest.mark.asyncio
    async def test_save_with_tags(self, memory_db):
        _, user = memory_db
        mem_id = await save_memory(
            user.id, "Tagged", "content",
            tags=["finance", "options"],
        )

        entries = await list_memories(user.id)
        hit = next(e for e in entries if e["id"] == mem_id)
        assert set(hit["tags"]) == {"finance", "options"}

    @pytest.mark.asyncio
    async def test_save_with_type(self, memory_db):
        _, user = memory_db
        mem_id = await save_memory(
            user.id, "Preference", "content", memory_type="preference",
        )
        entries = await list_memories(user.id)
        hit = next(e for e in entries if e["id"] == mem_id)
        assert hit["type"] == "preference"

    @pytest.mark.asyncio
    async def test_unknown_type_falls_back_to_note(self, memory_db):
        _, user = memory_db
        mem_id = await save_memory(
            user.id, "Odd", "content", memory_type="made-up",
        )
        entries = await list_memories(user.id)
        hit = next(e for e in entries if e["id"] == mem_id)
        assert hit["type"] == "note"

    @pytest.mark.asyncio
    async def test_empty_tags_are_stripped(self, memory_db):
        _, user = memory_db
        mem_id = await save_memory(
            user.id, "CleanTags", "c", tags=["", "   ", "real"],
        )
        entries = await list_memories(user.id)
        hit = next(e for e in entries if e["id"] == mem_id)
        assert hit["tags"] == ["real"]


class TestFilteredSearch:
    @pytest.mark.asyncio
    async def test_filter_by_type(self, memory_db):
        _, user = memory_db
        await save_memory(user.id, "a-note", "c", memory_type="note")
        await save_memory(user.id, "a-fact", "c", memory_type="fact")

        results = await search_memory(user.id, "", memory_type="fact")
        assert len(results) == 1
        assert results[0]["title"] == "a-fact"

    @pytest.mark.asyncio
    async def test_filter_by_tags_requires_all(self, memory_db):
        _, user = memory_db
        await save_memory(user.id, "fin-opt", "c", tags=["finance", "options"])
        await save_memory(user.id, "fin-only", "c", tags=["finance"])
        await save_memory(user.id, "misc", "c", tags=["misc"])

        # Require both tags → only fin-opt matches
        results = await search_memory(user.id, "", tags=["finance", "options"])
        titles = [r["title"] for r in results]
        assert titles == ["fin-opt"]

        # Require just 'finance' → two matches
        results = await search_memory(user.id, "", tags=["finance"])
        titles = {r["title"] for r in results}
        assert titles == {"fin-opt", "fin-only"}

    @pytest.mark.asyncio
    async def test_query_plus_filter(self, memory_db):
        _, user = memory_db
        await save_memory(user.id, "alpha-fact", "c", memory_type="fact")
        await save_memory(user.id, "alpha-pref", "c", memory_type="preference")
        await save_memory(user.id, "beta-fact", "c", memory_type="fact")

        results = await search_memory(user.id, "alpha", memory_type="fact")
        titles = [r["title"] for r in results]
        assert titles == ["alpha-fact"]


class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_title(self, memory_db):
        _, user = memory_db
        mem_id = await save_memory(user.id, "Old", "c")
        assert await update_memory(user.id, mem_id, title="New") is True

        entries = await list_memories(user.id)
        hit = next(e for e in entries if e["id"] == mem_id)
        assert hit["title"] == "New"

    @pytest.mark.asyncio
    async def test_update_tags_replaces(self, memory_db):
        _, user = memory_db
        mem_id = await save_memory(user.id, "t", "c", tags=["a", "b"])
        assert await update_memory(user.id, mem_id, tags=["c"]) is True

        entries = await list_memories(user.id)
        hit = next(e for e in entries if e["id"] == mem_id)
        assert hit["tags"] == ["c"]

    @pytest.mark.asyncio
    async def test_update_type(self, memory_db):
        _, user = memory_db
        mem_id = await save_memory(user.id, "t", "c")
        assert await update_memory(user.id, mem_id, memory_type="fact") is True

        entries = await list_memories(user.id)
        hit = next(e for e in entries if e["id"] == mem_id)
        assert hit["type"] == "fact"

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_false(self, memory_db):
        _, user = memory_db
        assert await update_memory(user.id, "nope", title="x") is False

    @pytest.mark.asyncio
    async def test_update_other_users_memory_fails(self, memory_db):
        _, user = memory_db
        mem_id = await save_memory(user.id, "Mine", "c")
        assert await update_memory("other-user", mem_id, title="Yours") is False
