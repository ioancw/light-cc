"""Tests for the agent execution engine (core/agent_runner.py)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.agent_crud import create_agent
from core.agent_runner import trigger_agent_run, _execute_agent_run
from core.db_models import AgentDefinition, AgentRun, Conversation, Message as DbMessage

from tests.conftest import _build_text_events


@pytest_asyncio.fixture
async def runner_db(test_db: AsyncSession, test_user):
    """Patch get_db in agent_crud + agent_runner to return the shared test session."""

    @asynccontextmanager
    async def _get_test_db():
        yield test_db

    with patch("core.agent_crud.get_db", side_effect=_get_test_db), \
         patch("core.agent_runner.get_db", side_effect=_get_test_db):
        yield test_db, test_user


@pytest.fixture
def mock_webhook():
    """Patch deliver_webhook so it doesn't try to hit the network."""
    with patch("core.webhooks.deliver_webhook", new=AsyncMock(return_value=True)) as m:
        yield m


class TestTriggerAgentRun:
    @pytest.mark.asyncio
    async def test_creates_run_record(self, runner_db, mock_webhook):
        _, user = runner_db
        agent = await create_agent(
            user_id=user.id, name="trig1", description="d", system_prompt="p",
        )

        with patch("core.agent_runner.enqueue", new=AsyncMock()):
            run = await trigger_agent_run(agent, trigger_type="manual")

        assert run.agent_id == agent.id
        assert run.user_id == user.id
        assert run.status == "running"
        assert run.trigger_type == "manual"

    @pytest.mark.asyncio
    async def test_enqueues_execution(self, runner_db, mock_webhook):
        _, user = runner_db
        agent = await create_agent(
            user_id=user.id, name="trig2", description="d", system_prompt="p",
        )

        with patch("core.agent_runner.enqueue", new=AsyncMock()) as mock_enqueue:
            run = await trigger_agent_run(agent, trigger_type="manual")

        mock_enqueue.assert_awaited_once()
        call_kwargs = mock_enqueue.await_args.kwargs
        assert call_kwargs["agent_id"] == agent.id
        assert call_kwargs["run_id"] == run.id
        assert call_kwargs["trigger_type"] == "manual"

    @pytest.mark.asyncio
    async def test_trigger_type_recorded(self, runner_db, mock_webhook):
        _, user = runner_db
        agent = await create_agent(
            user_id=user.id, name="trig3", description="d", system_prompt="p",
        )

        with patch("core.agent_runner.enqueue", new=AsyncMock()):
            run = await trigger_agent_run(agent, trigger_type="cron")

        assert run.trigger_type == "cron"


class TestExecuteAgentRun:
    @pytest.mark.asyncio
    async def test_happy_path_completes(self, runner_db, mock_anthropic_client, mock_webhook):
        db, user = runner_db
        agent = await create_agent(
            user_id=user.id, name="exec1", description="d", system_prompt="You are helpful.",
        )

        # Pre-create run record (the real trigger flow does this)
        run = AgentRun(
            agent_id=agent.id, user_id=user.id,
            status="running", trigger_type="manual",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("All done.")])

        await _execute_agent_run(
            agent_id=agent.id, run_id=run.id, trigger_type="manual",
        )

        # Reload run
        refreshed = (await db.execute(
            select(AgentRun).where(AgentRun.id == run.id),
        )).scalar_one()
        assert refreshed.status == "completed"
        assert refreshed.error is None
        assert refreshed.finished_at is not None
        assert refreshed.conversation_id is not None
        assert "All done." in (refreshed.result or "")

    @pytest.mark.asyncio
    async def test_persists_conversation(self, runner_db, mock_anthropic_client, mock_webhook):
        db, user = runner_db
        agent = await create_agent(
            user_id=user.id, name="exec2", description="d", system_prompt="You are helpful.",
        )
        run = AgentRun(
            agent_id=agent.id, user_id=user.id,
            status="running", trigger_type="manual",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("Persisted reply")])

        await _execute_agent_run(
            agent_id=agent.id, run_id=run.id, trigger_type="manual",
        )

        refreshed = (await db.execute(
            select(AgentRun).where(AgentRun.id == run.id),
        )).scalar_one()
        conv = (await db.execute(
            select(Conversation).where(Conversation.id == refreshed.conversation_id),
        )).scalar_one()
        assert conv.user_id == user.id
        assert conv.title.startswith("[Agent]")

        msgs = (await db.execute(
            select(DbMessage).where(DbMessage.conversation_id == conv.id),
        )).scalars().all()
        roles = [m.role for m in msgs]
        assert "user" in roles
        assert "assistant" in roles

    @pytest.mark.asyncio
    async def test_failure_sets_error(self, runner_db, mock_anthropic_client, mock_webhook):
        db, user = runner_db
        agent = await create_agent(
            user_id=user.id, name="exec3", description="d", system_prompt="p",
        )
        run = AgentRun(
            agent_id=agent.id, user_id=user.id,
            status="running", trigger_type="manual",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)

        # Make the agent loop blow up
        with patch("core.agent.run", new=AsyncMock(side_effect=RuntimeError("boom"))):
            await _execute_agent_run(
                agent_id=agent.id, run_id=run.id, trigger_type="manual",
            )

        refreshed = (await db.execute(
            select(AgentRun).where(AgentRun.id == run.id),
        )).scalar_one()
        assert refreshed.status == "failed"
        assert "boom" in (refreshed.error or "")
        assert refreshed.finished_at is not None

    @pytest.mark.asyncio
    async def test_missing_agent_is_noop(self, runner_db, mock_webhook):
        # Should return silently — no exception
        await _execute_agent_run(
            agent_id="nonexistent", run_id="nope", trigger_type="manual",
        )

    @pytest.mark.asyncio
    async def test_updates_last_run_at(self, runner_db, mock_anthropic_client, mock_webhook):
        db, user = runner_db
        agent = await create_agent(
            user_id=user.id, name="exec5", description="d", system_prompt="p",
        )
        assert agent.last_run_at is None

        run = AgentRun(
            agent_id=agent.id, user_id=user.id,
            status="running", trigger_type="manual",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("ok")])

        await _execute_agent_run(
            agent_id=agent.id, run_id=run.id, trigger_type="manual",
        )

        refreshed_agent = (await db.execute(
            select(AgentDefinition).where(AgentDefinition.id == agent.id),
        )).scalar_one()
        assert refreshed_agent.last_run_at is not None

    @pytest.mark.asyncio
    async def test_overlapping_runs_of_same_agent(self, test_user, mock_anthropic_client, mock_webhook):
        """Two back-to-back runs of the same agent must produce distinct
        AgentRun rows and distinct conversations. Uses a per-call session
        factory to mirror production (where each get_db() call is fresh)
        rather than the shared-session fixture used by sibling tests."""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
        from core.db_models import Base as _Base

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        # Re-attach the user into the new engine
        async with factory() as s:
            u = type(test_user)(
                email="conc@example.com", password_hash="x", display_name="c",
            )
            s.add(u)
            await s.commit()
            await s.refresh(u)
            user_id = u.id

        @asynccontextmanager
        async def _fresh_db():
            async with factory() as s:
                yield s

        with patch("core.agent_crud.get_db", side_effect=_fresh_db), \
             patch("core.agent_runner.get_db", side_effect=_fresh_db):
            agent = await create_agent(
                user_id=user_id, name="concurrent", description="d", system_prompt="p",
            )

            async with factory() as s:
                run_a = AgentRun(agent_id=agent.id, user_id=user_id, status="running", trigger_type="manual")
                run_b = AgentRun(agent_id=agent.id, user_id=user_id, status="running", trigger_type="manual")
                s.add_all([run_a, run_b])
                await s.commit()
                await s.refresh(run_a)
                await s.refresh(run_b)
                run_a_id, run_b_id = run_a.id, run_b.id

            _, set_responses = mock_anthropic_client
            set_responses([_build_text_events("reply A"), _build_text_events("reply B")])

            await _execute_agent_run(agent_id=agent.id, run_id=run_a_id, trigger_type="manual")
            await _execute_agent_run(agent_id=agent.id, run_id=run_b_id, trigger_type="manual")

        async with factory() as s:
            refreshed = (await s.execute(
                select(AgentRun).where(AgentRun.agent_id == agent.id),
            )).scalars().all()
            assert len(refreshed) == 2
            assert all(r.status == "completed" for r in refreshed)
            conv_ids = {r.conversation_id for r in refreshed}
            assert len(conv_ids) == 2 and None not in conv_ids

        await engine.dispose()

