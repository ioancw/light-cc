"""Cross-tenant isolation regression tests (R3).

For each user-scoped resource type, user A creates a row, then user B attempts
to read / update / delete it via REST and gets 404 (the uniform "not yours"
response across routes — 403 would also be acceptable but would leak existence).

Coverage: conversations, memory, agents, schedules, files, api_tokens.

This is the single most valuable test file for a security reviewer because it
exercises the end-to-end auth + ownership filter on every resource route in
one place. Adding a new resource type = add a block here.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.auth import hash_password
from core.db_models import Conversation, User


@pytest_asyncio.fixture
async def two_tenants(test_db):
    """Two isolated users A and B sharing the in-memory DB."""
    a = User(email="alice@test", password_hash=hash_password("x"), display_name="Alice")
    b = User(email="bob@test", password_hash=hash_password("x"), display_name="Bob")
    test_db.add_all([a, b])
    await test_db.commit()
    await test_db.refresh(a)
    await test_db.refresh(b)
    return a, b


def _build_app(routers, test_db, current_user_holder: dict):
    """Mount the given routers, overriding auth to return whoever's in the holder."""
    from routes.auth import get_current_user

    app = FastAPI()
    for r in routers:
        app.include_router(r)
    app.dependency_overrides[get_current_user] = lambda: current_user_holder["user"]
    return app


@asynccontextmanager
async def _db_patches(test_db, modules: list[str]):
    """Patch get_db in every listed module to yield the shared test session."""

    @asynccontextmanager
    async def _yield_db():
        yield test_db

    patches = [patch(f"{m}.get_db", _yield_db) for m in modules]
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()


# ── Conversations ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_conversations_isolation(test_db, two_tenants):
    from routes.conversations import router

    a, b = two_tenants
    holder: dict = {"user": a}
    app = _build_app([router], test_db, holder)

    conv = Conversation(user_id=a.id, title="Alice's private notes")
    test_db.add(conv)
    await test_db.commit()
    await test_db.refresh(conv)

    async with _db_patches(test_db, ["routes.conversations", "core.search"]):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            holder["user"] = a
            r = await client.get(f"/api/conversations/{conv.id}")
            assert r.status_code == 200, "owner can read their own conversation"

            holder["user"] = b
            r = await client.get(f"/api/conversations/{conv.id}")
            assert r.status_code == 404, "other user gets 404, not 200"

            r = await client.patch(
                f"/api/conversations/{conv.id}", json={"title": "hijacked"},
            )
            assert r.status_code == 404, "other user cannot rename"

            r = await client.delete(f"/api/conversations/{conv.id}")
            assert r.status_code == 404, "other user cannot delete"

            r = await client.get("/api/conversations")
            assert r.status_code == 200
            assert all(c["id"] != conv.id for c in r.json()), "list omits A's conv for B"

    await test_db.refresh(conv)
    assert conv.title == "Alice's private notes", "Alice's conv was not mutated"


# ── Memory ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_memory_isolation(test_db, two_tenants):
    from memory.manager import save_memory
    from routes.memory import router

    a, b = two_tenants
    holder: dict = {"user": a}
    app = _build_app([router], test_db, holder)

    # memory.manager._get_db is the shared path for both CRUD and routes
    @asynccontextmanager
    async def _yield_db():
        yield test_db

    with patch("memory.manager._get_db", _yield_db), \
         patch("routes.memory.get_db", _yield_db):
        mem_id = await save_memory(a.id, "secret", "top-secret body")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            holder["user"] = a
            r = await client.get(f"/api/memories/{mem_id}")
            assert r.status_code == 200

            holder["user"] = b
            r = await client.get(f"/api/memories/{mem_id}")
            assert r.status_code == 404

            r = await client.patch(
                f"/api/memories/{mem_id}", json={"title": "hijacked"},
            )
            assert r.status_code == 404

            r = await client.delete(f"/api/memories/{mem_id}")
            assert r.status_code == 404

            r = await client.get("/api/memories")
            assert r.status_code == 200
            assert r.json() == [], "B sees no memories; A's is hidden"


# ── Agents ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agents_isolation(test_db, two_tenants):
    from core.agent_crud import create_agent
    from routes.agents import router

    a, b = two_tenants
    holder: dict = {"user": a}
    app = _build_app([router], test_db, holder)

    @asynccontextmanager
    async def _yield_db():
        yield test_db

    with patch("core.agent_crud.get_db", _yield_db):
        agent_a = await create_agent(
            user_id=a.id,
            name="alice-agent",
            description="Alice's agent",
            system_prompt="You are Alice's assistant.",
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            holder["user"] = a
            r = await client.get(f"/api/agents/{agent_a.id}")
            assert r.status_code == 200

            holder["user"] = b
            r = await client.get(f"/api/agents/{agent_a.id}")
            assert r.status_code == 404

            r = await client.patch(
                f"/api/agents/{agent_a.id}", json={"description": "hijacked"},
            )
            assert r.status_code == 404

            r = await client.delete(f"/api/agents/{agent_a.id}")
            assert r.status_code == 404

            r = await client.get("/api/agents")
            assert r.status_code == 200
            assert all(ag["id"] != agent_a.id for ag in r.json())


# ── Schedules ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedules_isolation(test_db, two_tenants):
    from core.schedule_crud import create_schedule
    from routes.schedules import router

    a, b = two_tenants
    holder: dict = {"user": a}
    app = _build_app([router], test_db, holder)

    @asynccontextmanager
    async def _yield_db():
        yield test_db

    with patch("core.schedule_crud.get_db", _yield_db):
        sched = await create_schedule(
            user_id=a.id,
            name="alice-daily",
            cron_expression="0 9 * * *",
            prompt="Summarize yesterday",
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            holder["user"] = a
            r = await client.get(f"/api/schedules/{sched.id}")
            assert r.status_code == 200

            holder["user"] = b
            r = await client.get(f"/api/schedules/{sched.id}")
            assert r.status_code == 404

            r = await client.patch(
                f"/api/schedules/{sched.id}", json={"prompt": "hijacked"},
            )
            assert r.status_code == 404

            r = await client.delete(f"/api/schedules/{sched.id}")
            assert r.status_code == 404

            r = await client.get("/api/schedules")
            assert r.status_code == 200
            assert all(s["id"] != sched.id for s in r.json())


# ── API tokens ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_tokens_isolation(test_db, two_tenants):
    from core.api_tokens import create_api_token
    from routes.api_tokens import router

    a, b = two_tenants
    holder: dict = {"user": a}
    app = _build_app([router], test_db, holder)

    @asynccontextmanager
    async def _yield_db():
        yield test_db

    with patch("core.api_tokens.get_db", _yield_db):
        row, _plaintext = await create_api_token(user_id=a.id, name="alice-token")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            holder["user"] = a
            r = await client.get("/api/tokens")
            assert r.status_code == 200
            assert any(t["id"] == row.id for t in r.json())

            holder["user"] = b
            r = await client.get("/api/tokens")
            assert r.status_code == 200
            assert all(t["id"] != row.id for t in r.json()), "B's list does not leak A's tokens"

            r = await client.delete(f"/api/tokens/{row.id}")
            assert r.status_code == 404, "B cannot revoke A's token"

            holder["user"] = a
            r = await client.get("/api/tokens")
            assert any(t["id"] == row.id and t["revoked_at"] is None for t in r.json()), (
                "A's token is still active after B's failed revoke"
            )


# ── Files (workspace) ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_files_workspace_isolation(tmp_path, test_db, two_tenants):
    """Workspace directories are keyed by user_id; B's path resolution cannot
    cross into A's workspace even if B passes a relative path containing A's id.
    """
    from core.sandbox import get_workspace
    from routes.files import _resolve_user_path

    a, b = two_tenants

    with patch("core.sandbox._DATA_ROOT", tmp_path):
        ws_a = get_workspace(a.id)
        ws_b = get_workspace(b.id)
        (ws_a.root / "alice-secret.txt").write_text("alice's secret")

        # Direct attempt: B tries to resolve a path inside A's workspace from their
        # own ws root. The relative path component must resolve within ws_b, never
        # ws_a. The hardened _resolve_user_path uses Path.relative_to so a sibling
        # path like "../alice/secret" raises 403.
        from fastapi import HTTPException

        # 1. Relative path stays inside B's workspace — path is resolved against B.
        ws_root, target = _resolve_user_path(b, "my-note.txt")
        assert target.is_relative_to(ws_b.root.resolve())
        assert not target.is_relative_to(ws_a.root.resolve())

        # 2. Traversal attempt escaping B's root must raise 403.
        with pytest.raises(HTTPException) as exc:
            _resolve_user_path(b, f"../{a.id}/workspace/alice-secret.txt")
        assert exc.value.status_code == 403

        # 3. Absolute path pointing at A's workspace is rejected.
        with pytest.raises(HTTPException) as exc:
            _resolve_user_path(b, str(ws_a.root / "alice-secret.txt"))
        assert exc.value.status_code == 403


# ── Meta: list endpoints leak nothing across tenants ─────────────────────

@pytest.mark.asyncio
async def test_list_endpoints_do_not_leak(test_db, two_tenants):
    """One sweep: each list endpoint returns only the caller's rows."""
    from core.agent_crud import create_agent
    from core.api_tokens import create_api_token
    from core.schedule_crud import create_schedule
    from memory.manager import save_memory
    from routes.agents import router as agents_router
    from routes.api_tokens import router as tokens_router
    from routes.conversations import router as conv_router
    from routes.memory import router as memory_router
    from routes.schedules import router as sched_router

    a, b = two_tenants
    holder: dict = {"user": a}
    app = _build_app(
        [conv_router, memory_router, agents_router, sched_router, tokens_router],
        test_db, holder,
    )

    @asynccontextmanager
    async def _yield_db():
        yield test_db

    patches = [
        patch("routes.conversations.get_db", _yield_db),
        patch("core.search.get_db", _yield_db),
        patch("routes.memory.get_db", _yield_db),
        patch("memory.manager._get_db", _yield_db),
        patch("core.agent_crud.get_db", _yield_db),
        patch("core.schedule_crud.get_db", _yield_db),
        patch("core.api_tokens.get_db", _yield_db),
    ]
    for p in patches:
        p.start()
    try:
        # Seed A's kitchen-sink.
        test_db.add(Conversation(user_id=a.id, title="A-conv"))
        await test_db.commit()
        await save_memory(a.id, "A-memory", "content")
        await create_agent(a.id, "A-agent", "desc", "prompt")
        await create_schedule(a.id, "A-sched", "0 0 * * *", "prompt")
        await create_api_token(a.id, "A-token")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            holder["user"] = b

            for path in [
                "/api/conversations",
                "/api/memories",
                "/api/agents",
                "/api/schedules",
                "/api/tokens",
            ]:
                r = await client.get(path)
                assert r.status_code == 200, f"{path} returned {r.status_code}"
                assert r.json() == [], f"{path} leaked A's rows to B: {r.json()!r}"
    finally:
        for p in patches:
            p.stop()
