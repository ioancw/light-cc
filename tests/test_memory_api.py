"""Tests for /api/memories and /api/users/me/settings (routes/memory.py)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db_models import Memory
from memory.manager import save_memory
from routes.auth import get_current_user
from routes.memory import router as memory_router


@pytest_asyncio.fixture
async def api_client(test_db: AsyncSession, test_user):
    """FastAPI app wired with the memory router and test DB session."""

    @asynccontextmanager
    async def _get_test_db():
        yield test_db

    app = FastAPI()
    app.include_router(memory_router)
    app.dependency_overrides[get_current_user] = lambda: test_user

    # Both the manager (memory.manager._get_db) and the route's direct
    # get_db calls must hit the same in-memory session.
    with patch("memory.manager._get_db", side_effect=_get_test_db), \
         patch("routes.memory.get_db", side_effect=_get_test_db):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, test_db, test_user


# ── list / get ─────────────────────────────────────────────────────────

class TestListAndGet:
    @pytest.mark.asyncio
    async def test_list_empty(self, api_client):
        client, _, _ = api_client
        resp = await client.get("/api/memories")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_returns_own_rows(self, api_client):
        client, _, user = api_client
        await save_memory(user.id, "m1", "content-1")
        await save_memory(user.id, "m2", "content-2", tags=["x", "y"])

        resp = await client.get("/api/memories")
        assert resp.status_code == 200
        data = resp.json()
        titles = {m["title"] for m in data}
        assert titles == {"m1", "m2"}
        by_title = {m["title"]: m for m in data}
        assert set(by_title["m2"]["tags"]) == {"x", "y"}
        # No row has content in the list response (list is slim)
        assert all("content" not in m for m in data)

    @pytest.mark.asyncio
    async def test_list_filters_by_type(self, api_client):
        client, _, user = api_client
        await save_memory(user.id, "a", "c1", memory_type="note")
        await save_memory(user.id, "b", "c2", memory_type="preference")

        resp = await client.get("/api/memories?memory_type=preference")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "b"

    @pytest.mark.asyncio
    async def test_list_filters_by_source(self, api_client):
        client, _, user = api_client
        await save_memory(user.id, "by-user", "c1", source="user")
        await save_memory(user.id, "by-auto", "c2", source="auto")

        resp = await client.get("/api/memories?source=auto")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "by-auto"
        assert data[0]["source"] == "auto"

    @pytest.mark.asyncio
    async def test_get_single(self, api_client):
        client, _, user = api_client
        mem_id = await save_memory(user.id, "solo", "body", tags=["t1"])

        resp = await client.get(f"/api/memories/{mem_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == mem_id
        assert data["title"] == "solo"
        assert data["content"] == "body"
        assert data["tags"] == ["t1"]
        assert data["source"] == "user"
        assert data["created_at"] is not None

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_404(self, api_client):
        client, _, _ = api_client
        resp = await client.get("/api/memories/does-not-exist")
        assert resp.status_code == 404


# ── create ─────────────────────────────────────────────────────────────

class TestCreate:
    @pytest.mark.asyncio
    async def test_create_minimal(self, api_client):
        client, db, user = api_client
        resp = await client.post("/api/memories", json={
            "title": "new",
            "content": "hello world",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "new"
        assert data["content"] == "hello world"
        assert data["memory_type"] == "note"
        assert data["source"] == "user"

        row = (await db.execute(
            select(Memory).where(Memory.id == data["id"]),
        )).scalar_one()
        assert row.user_id == user.id

    @pytest.mark.asyncio
    async def test_create_with_tags_and_type(self, api_client):
        client, _, _ = api_client
        resp = await client.post("/api/memories", json={
            "title": "T",
            "content": "c",
            "memory_type": "preference",
            "tags": ["foo", "bar"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["memory_type"] == "preference"
        assert set(data["tags"]) == {"foo", "bar"}

    @pytest.mark.asyncio
    async def test_create_invalid_type_returns_400(self, api_client):
        client, _, _ = api_client
        resp = await client.post("/api/memories", json={
            "title": "T",
            "content": "c",
            "memory_type": "not-a-type",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_empty_content_returns_400(self, api_client):
        client, _, _ = api_client
        resp = await client.post("/api/memories", json={
            "title": "T",
            "content": "   ",
        })
        assert resp.status_code == 400


# ── update ─────────────────────────────────────────────────────────────

class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_title_and_content(self, api_client):
        client, _, user = api_client
        mem_id = await save_memory(user.id, "old", "old-body")

        resp = await client.patch(f"/api/memories/{mem_id}", json={
            "title": "new",
            "content": "new-body",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "new"
        assert data["content"] == "new-body"

    @pytest.mark.asyncio
    async def test_update_tags(self, api_client):
        client, _, user = api_client
        mem_id = await save_memory(user.id, "t", "c", tags=["old"])

        resp = await client.patch(f"/api/memories/{mem_id}", json={
            "tags": ["a", "b"],
        })
        assert resp.status_code == 200
        assert set(resp.json()["tags"]) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_update_empty_body_returns_400(self, api_client):
        client, _, user = api_client
        mem_id = await save_memory(user.id, "t", "c")
        resp = await client.patch(f"/api/memories/{mem_id}", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_update_invalid_type_returns_400(self, api_client):
        client, _, user = api_client
        mem_id = await save_memory(user.id, "t", "c")
        resp = await client.patch(f"/api/memories/{mem_id}", json={
            "memory_type": "nonsense",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_404(self, api_client):
        client, _, _ = api_client
        resp = await client.patch("/api/memories/nope", json={
            "title": "x",
        })
        assert resp.status_code == 404


# ── delete ─────────────────────────────────────────────────────────────

class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_returns_204(self, api_client):
        client, db, user = api_client
        mem_id = await save_memory(user.id, "d", "c")

        resp = await client.delete(f"/api/memories/{mem_id}")
        assert resp.status_code == 204

        rows = (await db.execute(
            select(Memory).where(Memory.id == mem_id),
        )).scalars().all()
        assert rows == []

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, api_client):
        client, _, _ = api_client
        resp = await client.delete("/api/memories/nope")
        assert resp.status_code == 404


# ── user settings ──────────────────────────────────────────────────────

class TestSettings:
    @pytest.mark.asyncio
    async def test_get_settings_defaults(self, api_client):
        client, _, _ = api_client
        resp = await client.get("/api/users/me/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["auto_extract_enabled"] is False
        assert data["auto_extract_min_messages"] == 4
        assert "haiku" in data["auto_extract_model"]

    @pytest.mark.asyncio
    async def test_update_settings(self, api_client):
        client, db, user = api_client
        resp = await client.patch("/api/users/me/settings", json={
            "auto_extract_enabled": True,
            "auto_extract_min_messages": 10,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["auto_extract_enabled"] is True
        assert data["auto_extract_min_messages"] == 10

    @pytest.mark.asyncio
    async def test_update_settings_empty_body_returns_400(self, api_client):
        client, _, _ = api_client
        resp = await client.patch("/api/users/me/settings", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_update_settings_rejects_out_of_range_min_messages(self, api_client):
        client, _, _ = api_client
        resp = await client.patch("/api/users/me/settings", json={
            "auto_extract_min_messages": 0,
        })
        assert resp.status_code == 422  # Pydantic validation
