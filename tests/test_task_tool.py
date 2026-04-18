"""Tests for the ``Task`` tool -- CC-compliant in-conversation subagent delegation.

Companion to test_subagent.py (which covers builtin AgentType resolution and the
lower-level run_subagent helper). This file focuses on the production hot path:

    handle_task({subagent_type, prompt, description, ...})
        -> resolve user AgentDefinition by name (preferred)
        -> fall back to builtin AgentType
        -> run via run_agent_once with persist_conversation=False

The first path produces an AgentRun row for audit; the second does not.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from tests.conftest import _build_text_events


@pytest.fixture(autouse=True)
def _patch_session():
    with (
        patch("core.agent.current_session_get", return_value="default"),
        patch("core.agent.record_usage", new_callable=AsyncMock),
    ):
        yield


@pytest.fixture
def _clean_subagent_state():
    from tools.subagent import _active_agents, _notify_callbacks
    _active_agents.clear()
    _notify_callbacks.clear()
    yield
    _active_agents.clear()
    _notify_callbacks.clear()


@pytest_asyncio.fixture
async def task_db(test_db, test_user):
    async def _get_test_db():
        return test_db

    with patch("core.agent_crud.get_db", side_effect=_get_test_db), \
         patch("core.database.get_db", side_effect=_get_test_db), \
         patch("core.agent_runner.get_db", side_effect=_get_test_db):
        yield test_db, test_user


class TestTaskToolSchema:
    def test_task_tool_is_registered(self):
        from tools.registry import get_tool_schemas
        schemas = get_tool_schemas(["Task"])
        assert len(schemas) == 1
        assert schemas[0]["name"] == "Task"

    def test_task_tool_required_fields(self):
        from tools.registry import get_tool_schemas
        schema = get_tool_schemas(["Task"])[0]["input_schema"]
        required = set(schema["required"])
        assert {"subagent_type", "prompt", "description"} <= required

    def test_aliases_resolve_to_task(self):
        from tools.registry import get_tool_schemas
        for alias in ("Agent", "subagent", "BackgroundAgent", "background_agent"):
            schemas = get_tool_schemas([alias])
            assert len(schemas) == 1
            assert schemas[0]["name"] == "Task"


class TestTaskToolBuiltinPath:
    @pytest.mark.asyncio
    async def test_builtin_agent_type_runs_ephemerally(
        self, mock_anthropic_client, _clean_subagent_state,
    ):
        """Unknown-to-user agent name resolves to a builtin AgentType and
        runs without creating an AgentRun row."""
        from tools.subagent import handle_task

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("ephemeral output")])

        result_json = await handle_task({
            "subagent_type": "explorer",
            "prompt": "Find stuff",
            "description": "probe",
        })
        result = json.loads(result_json)

        assert "agent_id" in result
        assert "run_id" not in result  # builtins don't get AgentRun rows
        assert "ephemeral output" in result["result"]


class TestTaskToolAgentDefinitionPath:
    @pytest.mark.asyncio
    async def test_user_agent_produces_agent_run_row(
        self, mock_anthropic_client, _clean_subagent_state, task_db,
    ):
        """When Task resolves to an AgentDefinition, run_agent_once should
        persist an AgentRun row for audit."""
        from core.agent_crud import create_agent
        from core.db_models import AgentRun
        from core.session import create_connection, set_current_session, destroy_connection
        from sqlalchemy import select
        from tools.subagent import handle_task

        db, user = task_db
        agent = await create_agent(
            user_id=user.id, name="audit-subject",
            description="d", system_prompt="You are audited.",
            tools=["Read"], max_turns=3,
        )

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("audited output")])

        create_connection("task-audit-session", user_id=user.id)
        set_current_session("task-audit-session")
        try:
            result_json = await handle_task({
                "subagent_type": "audit-subject",
                "prompt": "Run it",
                "description": "audit run",
            })
        finally:
            destroy_connection("task-audit-session")

        result = json.loads(result_json)
        assert "run_id" in result, result
        assert "audited output" in result["result"]

        runs = (await db.execute(
            select(AgentRun).where(AgentRun.agent_id == agent.id),
        )).scalars().all()
        assert len(runs) == 1
        assert runs[0].id == result["run_id"]
        assert runs[0].trigger_type == "subagent"
        assert runs[0].conversation_id is None  # persist_conversation=False

    @pytest.mark.asyncio
    async def test_user_agent_shadows_builtin_via_task(
        self, mock_anthropic_client, _clean_subagent_state, task_db,
    ):
        """If a user names their agent ``researcher``, Task should dispatch
        to the user's definition, not the builtin researcher."""
        from core.agent_crud import create_agent
        from core.db_models import AgentRun
        from core.session import create_connection, set_current_session, destroy_connection
        from sqlalchemy import select
        from tools.subagent import handle_task

        db, user = task_db
        agent = await create_agent(
            user_id=user.id, name="researcher",
            description="shadow version",
            system_prompt="SHADOW SYSTEM PROMPT",
            tools=["Read"], max_turns=4,
        )

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("shadow says hi")])

        create_connection("shadow-sess", user_id=user.id)
        set_current_session("shadow-sess")
        try:
            result_json = await handle_task({
                "subagent_type": "researcher",
                "prompt": "go",
                "description": "shadowed",
            })
        finally:
            destroy_connection("shadow-sess")

        result = json.loads(result_json)
        assert "run_id" in result  # would be absent if we hit the builtin

        runs = (await db.execute(
            select(AgentRun).where(AgentRun.agent_id == agent.id),
        )).scalars().all()
        assert len(runs) == 1


class TestTaskToolExcludesSelf:
    def test_task_is_in_excluded_tools(self):
        from core.agent_types import EXCLUDED_TOOLS
        assert "Task" in EXCLUDED_TOOLS
        # The original aliases must also remain excluded for defence in depth.
        assert "Agent" in EXCLUDED_TOOLS
        assert "subagent" in EXCLUDED_TOOLS


class TestTaskToolUnknown:
    @pytest.mark.asyncio
    async def test_unknown_subagent_type_reports_error(self, _clean_subagent_state):
        from tools.subagent import handle_task
        result_json = await handle_task({
            "subagent_type": "definitely-not-a-thing",
            "prompt": "hi",
            "description": "nope",
        })
        result = json.loads(result_json)
        assert "error" in result
        assert "definitely-not-a-thing" in result["error"]
