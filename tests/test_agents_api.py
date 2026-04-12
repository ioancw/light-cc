"""Tests for the /api/agents REST endpoints (routes/agents.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.agent_crud import create_agent
from core.db_models import AgentDefinition, AgentRun
from routes.agents import router as agents_router
from routes.auth import get_current_user


@pytest_asyncio.fixture
async def api_client(test_db: AsyncSession, test_user):
    """Build a minimal FastAPI app wired with the agents router.

    - Overrides `get_current_user` to return the test user (skips real JWT).
    - Patches `core.agent_crud.get_db` and `core.agent_runner.get_db` so every
      endpoint uses the shared in-memory test session.
    - Patches the job-queue `enqueue` so `POST /{id}/run` does not actually
      execute an agent (we just verify the AgentRun row is created).
    """

    async def _get_test_db():
        return test_db

    app = FastAPI()
    app.include_router(agents_router)
    app.dependency_overrides[get_current_user] = lambda: test_user

    with patch("core.agent_crud.get_db", side_effect=_get_test_db), \
         patch("core.agent_runner.get_db", side_effect=_get_test_db), \
         patch("core.agent_runner.enqueue", new=AsyncMock()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, test_db, test_user


# ── list / get ─────────────────────────────────────────────────────────

class TestListAndGet:
    @pytest.mark.asyncio
    async def test_list_empty(self, api_client):
        client, _, _ = api_client
        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_returns_own_agents(self, api_client):
        client, _, user = api_client
        await create_agent(user_id=user.id, name="a1", description="d", system_prompt="p")
        await create_agent(user_id=user.id, name="a2", description="d", system_prompt="p")

        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        names = {a["name"] for a in data}
        assert names == {"a1", "a2"}

    @pytest.mark.asyncio
    async def test_get_single_agent(self, api_client):
        client, _, user = api_client
        agent = await create_agent(
            user_id=user.id, name="solo", description="d", system_prompt="p",
        )

        resp = await client.get(f"/api/agents/{agent.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == agent.id
        assert data["name"] == "solo"
        assert data["source"] == "user"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_404(self, api_client):
        client, _, _ = api_client
        resp = await client.get("/api/agents/does-not-exist")
        assert resp.status_code == 404


# ── create ─────────────────────────────────────────────────────────────

class TestCreate:
    @pytest.mark.asyncio
    async def test_create_minimal(self, api_client):
        client, db, user = api_client
        resp = await client.post("/api/agents", json={
            "name": "new-agent",
            "description": "Brand new",
            "system_prompt": "You are new.",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "new-agent"
        assert data["trigger"] == "manual"
        assert data["enabled"] is True
        assert data["source"] == "user"

        # Confirm row exists in the DB
        row = (await db.execute(
            select(AgentDefinition).where(AgentDefinition.name == "new-agent"),
        )).scalar_one()
        assert row.user_id == user.id

    @pytest.mark.asyncio
    async def test_create_with_tools(self, api_client):
        client, _, _ = api_client
        resp = await client.post("/api/agents", json={
            "name": "tooled",
            "description": "d",
            "system_prompt": "p",
            "tools": ["WebSearch", "WebFetch"],
        })
        assert resp.status_code == 201
        assert resp.json()["tools"] == ["WebSearch", "WebFetch"]

    @pytest.mark.asyncio
    async def test_create_cron_agent(self, api_client):
        client, _, _ = api_client
        resp = await client.post("/api/agents", json={
            "name": "cron1",
            "description": "d",
            "system_prompt": "p",
            "trigger": "cron",
            "cron_expression": "0 * * * *",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["trigger"] == "cron"
        assert data["cron_expression"] == "0 * * * *"
        assert data["next_run_at"] is not None

    @pytest.mark.asyncio
    async def test_create_invalid_cron_returns_400(self, api_client):
        client, _, _ = api_client
        resp = await client.post("/api/agents", json={
            "name": "badcron",
            "description": "d",
            "system_prompt": "p",
            "trigger": "cron",
            "cron_expression": "not a cron",
        })
        assert resp.status_code == 400
        assert "cron" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_create_cron_without_expression_returns_400(self, api_client):
        client, _, _ = api_client
        resp = await client.post("/api/agents", json={
            "name": "nocron",
            "description": "d",
            "system_prompt": "p",
            "trigger": "cron",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_duplicate_name_returns_400(self, api_client):
        client, _, user = api_client
        await create_agent(user_id=user.id, name="dup", description="d", system_prompt="p")

        resp = await client.post("/api/agents", json={
            "name": "dup",
            "description": "d2",
            "system_prompt": "p2",
        })
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_bad_trigger_returns_400(self, api_client):
        client, _, _ = api_client
        resp = await client.post("/api/agents", json={
            "name": "bt",
            "description": "d",
            "system_prompt": "p",
            "trigger": "nonsense",
        })
        assert resp.status_code == 400


# ── update ─────────────────────────────────────────────────────────────

class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_description(self, api_client):
        client, _, user = api_client
        agent = await create_agent(
            user_id=user.id, name="u1", description="old", system_prompt="p",
        )

        resp = await client.patch(f"/api/agents/{agent.id}", json={
            "description": "new",
        })
        assert resp.status_code == 200
        assert resp.json()["description"] == "new"

    @pytest.mark.asyncio
    async def test_update_empty_body_returns_400(self, api_client):
        client, _, user = api_client
        agent = await create_agent(
            user_id=user.id, name="u2", description="d", system_prompt="p",
        )
        resp = await client.patch(f"/api/agents/{agent.id}", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_404(self, api_client):
        client, _, _ = api_client
        resp = await client.patch("/api/agents/nope", json={"description": "x"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_to_cron(self, api_client):
        client, _, user = api_client
        agent = await create_agent(
            user_id=user.id, name="u3", description="d", system_prompt="p",
        )

        resp = await client.patch(f"/api/agents/{agent.id}", json={
            "trigger": "cron",
            "cron_expression": "0 * * * *",
        })
        assert resp.status_code == 200
        assert resp.json()["next_run_at"] is not None

    @pytest.mark.asyncio
    async def test_update_bad_cron_returns_400(self, api_client):
        client, _, user = api_client
        agent = await create_agent(
            user_id=user.id, name="u4", description="d", system_prompt="p",
            trigger="cron", cron_expression="0 * * * *",
        )
        resp = await client.patch(f"/api/agents/{agent.id}", json={
            "cron_expression": "garbage",
        })
        assert resp.status_code == 400


# ── delete ─────────────────────────────────────────────────────────────

class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_returns_204(self, api_client):
        client, db, user = api_client
        agent = await create_agent(
            user_id=user.id, name="d1", description="d", system_prompt="p",
        )
        resp = await client.delete(f"/api/agents/{agent.id}")
        assert resp.status_code == 204

        # Confirm gone
        rows = (await db.execute(
            select(AgentDefinition).where(AgentDefinition.id == agent.id),
        )).scalars().all()
        assert rows == []

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, api_client):
        client, _, _ = api_client
        resp = await client.delete("/api/agents/nope")
        assert resp.status_code == 404


# ── runs ───────────────────────────────────────────────────────────────

class TestRuns:
    @pytest.mark.asyncio
    async def test_list_runs_empty(self, api_client):
        client, _, user = api_client
        agent = await create_agent(
            user_id=user.id, name="r1", description="d", system_prompt="p",
        )
        resp = await client.get(f"/api/agents/{agent.id}/runs")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_runs_returns_rows(self, api_client):
        client, db, user = api_client
        agent = await create_agent(
            user_id=user.id, name="r2", description="d", system_prompt="p",
        )
        run = AgentRun(
            agent_id=agent.id, user_id=user.id,
            status="completed", trigger_type="manual",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)

        resp = await client.get(f"/api/agents/{agent.id}/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == run.id
        assert data[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_list_runs_for_missing_agent_returns_404(self, api_client):
        client, _, _ = api_client
        resp = await client.get("/api/agents/nope/runs")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_single_run(self, api_client):
        client, db, user = api_client
        agent = await create_agent(
            user_id=user.id, name="r3", description="d", system_prompt="p",
        )
        run = AgentRun(
            agent_id=agent.id, user_id=user.id,
            status="running", trigger_type="manual",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)

        resp = await client.get(f"/api/agents/{agent.id}/runs/{run.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == run.id

    @pytest.mark.asyncio
    async def test_get_single_run_missing_returns_404(self, api_client):
        client, _, user = api_client
        agent = await create_agent(
            user_id=user.id, name="r4", description="d", system_prompt="p",
        )
        resp = await client.get(f"/api/agents/{agent.id}/runs/nope")
        assert resp.status_code == 404


# ── trigger run ────────────────────────────────────────────────────────

class TestTriggerRun:
    @pytest.mark.asyncio
    async def test_trigger_enabled_agent_returns_202(self, api_client):
        client, db, user = api_client
        agent = await create_agent(
            user_id=user.id, name="fire1", description="d", system_prompt="p",
        )

        resp = await client.post(f"/api/agents/{agent.id}/run")
        assert resp.status_code == 202
        data = resp.json()
        assert data["agent_id"] == agent.id
        assert data["status"] == "running"
        assert data["trigger_type"] == "manual"

        # A run row was created in the DB
        rows = (await db.execute(
            select(AgentRun).where(AgentRun.agent_id == agent.id),
        )).scalars().all()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_trigger_disabled_agent_returns_400(self, api_client):
        client, _, user = api_client
        agent = await create_agent(
            user_id=user.id, name="fire2", description="d", system_prompt="p",
        )
        # Disable it via the CRUD layer
        from core.agent_crud import update_agent
        await update_agent(agent.id, user.id, enabled=False)

        resp = await client.post(f"/api/agents/{agent.id}/run")
        assert resp.status_code == 400
        assert "disabled" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_trigger_nonexistent_returns_404(self, api_client):
        client, _, _ = api_client
        resp = await client.post("/api/agents/nope/run")
        assert resp.status_code == 404


# ── trigger by name (programmatic) ─────────────────────────────────────

class TestTriggerRunByName:
    @pytest.mark.asyncio
    async def test_run_by_name_returns_202(self, api_client):
        client, db, user = api_client
        agent = await create_agent(
            user_id=user.id, name="by-name", description="d", system_prompt="p",
        )

        resp = await client.post("/api/agents/run", json={"name": "by-name"})
        assert resp.status_code == 202
        data = resp.json()
        assert data["agent_id"] == agent.id
        assert data["trigger_type"] == "api"

        rows = (await db.execute(
            select(AgentRun).where(AgentRun.agent_id == agent.id),
        )).scalars().all()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_run_by_name_unknown_returns_404(self, api_client):
        client, _, _ = api_client
        resp = await client.post("/api/agents/run", json={"name": "no-such-agent"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_run_by_name_disabled_returns_400(self, api_client):
        client, _, user = api_client
        agent = await create_agent(
            user_id=user.id, name="off-by-name", description="d", system_prompt="p",
        )
        from core.agent_crud import update_agent
        await update_agent(agent.id, user.id, enabled=False)

        resp = await client.post("/api/agents/run", json={"name": "off-by-name"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_run_by_name_ignores_trigger_type_in_body(self, api_client):
        """trigger_type is always 'api' for this endpoint — extra fields are ignored."""
        client, _, user = api_client
        await create_agent(
            user_id=user.id, name="trig-ignore", description="d", system_prompt="p",
        )
        resp = await client.post(
            "/api/agents/run",
            json={"name": "trig-ignore", "trigger_type": "manual"},
        )
        assert resp.status_code == 202
        assert resp.json()["trigger_type"] == "api"


# ── auth ───────────────────────────────────────────────────────────────

class TestAuth:
    @pytest.mark.asyncio
    async def test_missing_auth_returns_401(self, test_db):
        """Without the get_current_user override, the bearer dependency rejects."""
        app = FastAPI()
        app.include_router(agents_router)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/agents")

        # FastAPI's HTTPBearer returns 403 when no Authorization header is sent;
        # 401/403 both indicate auth rejection.
        assert resp.status_code in (401, 403)
