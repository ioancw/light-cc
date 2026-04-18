"""Tests for the AgentDefinition CRUD layer (core/agent_crud.py)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from core.agent_crud import (
    create_agent,
    delete_agent,
    get_agent,
    get_agent_run,
    get_agent_runs,
    list_agents,
    update_agent,
)
from core.db_models import AgentDefinition, AgentRun


@pytest_asyncio.fixture
async def agent_db(test_db: AsyncSession, test_user):
    """Patch core.agent_crud.get_db (and agent_runner.get_db) to return test session."""

    async def _get_test_db():
        return test_db

    with patch("core.agent_crud.get_db", side_effect=_get_test_db):
        yield test_db, test_user


class TestCreateAgent:
    @pytest.mark.asyncio
    async def test_create_minimal(self, agent_db):
        _, user = agent_db
        agent = await create_agent(
            user_id=user.id,
            name="test-agent",
            description="A test agent",
            system_prompt="You are a test agent.",
        )
        assert agent.id
        assert agent.name == "test-agent"
        assert agent.enabled is True
        assert agent.source == "user"
        assert agent.max_turns == 20

    @pytest.mark.asyncio
    async def test_create_with_tools(self, agent_db):
        _, user = agent_db
        agent = await create_agent(
            user_id=user.id,
            name="tooled",
            description="d",
            system_prompt="p",
            tools=["WebSearch", "WebFetch"],
        )
        assert agent.tools_list == ["WebSearch", "WebFetch"]

    @pytest.mark.asyncio
    async def test_create_rejects_duplicate_name(self, agent_db):
        _, user = agent_db
        await create_agent(
            user_id=user.id, name="dup", description="d", system_prompt="p",
        )
        with pytest.raises(ValueError, match="already exists"):
            await create_agent(
                user_id=user.id, name="dup", description="d", system_prompt="p",
            )

    @pytest.mark.asyncio
    async def test_create_rejects_bad_memory_scope(self, agent_db):
        _, user = agent_db
        with pytest.raises(ValueError, match="Invalid memory_scope"):
            await create_agent(
                user_id=user.id, name="bm", description="d", system_prompt="p",
                memory_scope="unknown",
            )


class TestListAndGet:
    @pytest.mark.asyncio
    async def test_list_empty(self, agent_db):
        _, user = agent_db
        agents = await list_agents(user.id)
        assert agents == []

    @pytest.mark.asyncio
    async def test_list_returns_own_agents(self, agent_db):
        _, user = agent_db
        await create_agent(user_id=user.id, name="a1", description="d", system_prompt="p")
        await create_agent(user_id=user.id, name="a2", description="d", system_prompt="p")
        agents = await list_agents(user.id)
        names = {a.name for a in agents}
        assert names == {"a1", "a2"}

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, agent_db):
        _, user = agent_db
        result = await get_agent("does-not-exist", user.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_wrong_user_returns_none(self, agent_db):
        _, user = agent_db
        agent = await create_agent(
            user_id=user.id, name="mine", description="d", system_prompt="p",
        )
        result = await get_agent(agent.id, "other-user-id")
        assert result is None


class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_description(self, agent_db):
        _, user = agent_db
        agent = await create_agent(
            user_id=user.id, name="u1", description="old", system_prompt="p",
        )
        updated = await update_agent(agent.id, user.id, description="new")
        assert updated.description == "new"

    @pytest.mark.asyncio
    async def test_update_tools(self, agent_db):
        _, user = agent_db
        agent = await create_agent(
            user_id=user.id, name="u2", description="d", system_prompt="p",
        )
        updated = await update_agent(agent.id, user.id, tools=["Read", "Write"])
        assert updated.tools_list == ["Read", "Write"]

    @pytest.mark.asyncio
    async def test_update_disable(self, agent_db):
        _, user = agent_db
        agent = await create_agent(
            user_id=user.id, name="u3", description="d", system_prompt="p",
        )
        assert agent.enabled is True
        updated = await update_agent(agent.id, user.id, enabled=False)
        assert updated.enabled is False

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_none(self, agent_db):
        _, user = agent_db
        result = await update_agent("nope", user.id, description="x")
        assert result is None


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_returns_true(self, agent_db):
        _, user = agent_db
        agent = await create_agent(
            user_id=user.id, name="d1", description="d", system_prompt="p",
        )
        deleted = await delete_agent(agent.id, user.id)
        assert deleted is True
        assert await get_agent(agent.id, user.id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self, agent_db):
        _, user = agent_db
        assert await delete_agent("nope", user.id) is False

    @pytest.mark.asyncio
    async def test_delete_wrong_user(self, agent_db):
        _, user = agent_db
        agent = await create_agent(
            user_id=user.id, name="d2", description="d", system_prompt="p",
        )
        assert await delete_agent(agent.id, "other-user") is False
        assert await get_agent(agent.id, user.id) is not None


class TestRuns:
    @pytest.mark.asyncio
    async def test_get_runs_empty(self, agent_db):
        _, user = agent_db
        agent = await create_agent(
            user_id=user.id, name="r1", description="d", system_prompt="p",
        )
        runs = await get_agent_runs(agent.id, user.id)
        assert runs == []

    @pytest.mark.asyncio
    async def test_get_runs_returns_owned(self, agent_db):
        db, user = agent_db
        agent = await create_agent(
            user_id=user.id, name="r2", description="d", system_prompt="p",
        )
        run = AgentRun(agent_id=agent.id, user_id=user.id, status="completed", trigger_type="manual")
        db.add(run)
        await db.commit()
        await db.refresh(run)

        runs = await get_agent_runs(agent.id, user.id)
        assert len(runs) == 1
        assert runs[0].id == run.id

    @pytest.mark.asyncio
    async def test_get_runs_wrong_user(self, agent_db):
        db, user = agent_db
        agent = await create_agent(
            user_id=user.id, name="r3", description="d", system_prompt="p",
        )
        db.add(AgentRun(agent_id=agent.id, user_id=user.id, status="completed", trigger_type="manual"))
        await db.commit()

        runs = await get_agent_runs(agent.id, "other-user")
        assert runs == []

    @pytest.mark.asyncio
    async def test_get_single_run(self, agent_db):
        db, user = agent_db
        agent = await create_agent(
            user_id=user.id, name="r4", description="d", system_prompt="p",
        )
        run = AgentRun(agent_id=agent.id, user_id=user.id, status="running", trigger_type="manual")
        db.add(run)
        await db.commit()
        await db.refresh(run)

        fetched = await get_agent_run(agent.id, run.id, user.id)
        assert fetched is not None
        assert fetched.status == "running"

    @pytest.mark.asyncio
    async def test_get_single_run_wrong_user(self, agent_db):
        db, user = agent_db
        agent = await create_agent(
            user_id=user.id, name="r5", description="d", system_prompt="p",
        )
        run = AgentRun(agent_id=agent.id, user_id=user.id, status="running", trigger_type="manual")
        db.add(run)
        await db.commit()
        await db.refresh(run)

        fetched = await get_agent_run(agent.id, run.id, "other-user")
        assert fetched is None
