"""Tests for the sub-agent system (tools/subagent.py, core/agent_types.py)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import _build_text_events, _build_tool_events, MockStream


@pytest.fixture(autouse=True)
def _patch_session():
    """Patch session lookups so sub-agent code doesn't need real state."""
    with (
        patch("core.agent.current_session_get", return_value="default"),
        patch("core.agent.record_usage", new_callable=AsyncMock),
    ):
        yield


@pytest.fixture
def _clean_subagent_state():
    """Reset sub-agent state between tests."""
    from tools.subagent import _active_agents, _notify_callbacks
    _active_agents.clear()
    _notify_callbacks.clear()
    yield
    _active_agents.clear()
    _notify_callbacks.clear()


# ── Agent type registry ────────────────────────────────────────────────

class TestAgentTypeRegistry:
    def test_default_types_registered(self):
        from core.agent_types import get_agent_type, list_agent_types

        types = list_agent_types()
        type_names = [t.name for t in types]
        assert "explorer" in type_names
        assert "planner" in type_names
        assert "coder" in type_names
        assert "researcher" in type_names
        assert "default" in type_names

    def test_explorer_has_read_only_tools(self):
        from core.agent_types import get_agent_type

        explorer = get_agent_type("explorer")
        assert explorer is not None
        assert "Read" in explorer.tool_names
        assert "Grep" in explorer.tool_names
        assert "Glob" in explorer.tool_names
        # Explorer should NOT have write tools
        assert "Write" not in explorer.tool_names
        assert "Edit" not in explorer.tool_names

    def test_coder_has_execution_tools(self):
        from core.agent_types import get_agent_type

        coder = get_agent_type("coder")
        assert coder is not None
        # Coder should have broad tool access
        # tool_names empty means "all minus excluded"
        # or it explicitly lists execution tools
        assert coder.tool_names == [] or "Bash" in coder.tool_names

    def test_get_unknown_type_returns_none(self):
        from core.agent_types import get_agent_type

        assert get_agent_type("nonexistent_type_xyz") is None

    def test_register_custom_type(self):
        from core.agent_types import register_agent_type, get_agent_type, AgentType

        custom = AgentType(
            name="custom_test",
            system_prompt="You are a custom test agent.",
            tool_names=["Read"],
            max_turns=5,
        )
        register_agent_type(custom)
        retrieved = get_agent_type("custom_test")
        assert retrieved is not None
        assert retrieved.system_prompt == "You are a custom test agent."
        assert retrieved.max_turns == 5


# ── Sub-agent spawning ─────────────────────────────────────────────────

class TestSubAgentSpawn:
    @pytest.mark.asyncio
    async def test_foreground_subagent(self, mock_anthropic_client, _clean_subagent_state):
        """Foreground sub-agent should run and return result."""
        from tools.subagent import run_subagent

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("Sub-agent result here")])

        output, messages = await run_subagent(
            prompt="Find the main entry point",
            system="You are an explorer.",
            tool_names=["Read"],
            max_turns=5,
        )

        assert "Sub-agent result here" in output
        assert len(messages) >= 2  # user + assistant

    @pytest.mark.asyncio
    async def test_subagent_with_agent_type(self, mock_anthropic_client, _clean_subagent_state):
        """Sub-agent should inherit settings from agent type."""
        from tools.subagent import run_subagent
        from core.agent_types import get_agent_type

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("Explored!")])

        explorer = get_agent_type("explorer")
        output, _ = await run_subagent(
            prompt="Search the codebase",
            system=explorer.system_prompt,
            tool_names=explorer.tool_names,
            max_turns=explorer.max_turns,
        )

        assert "Explored!" in output

    @pytest.mark.asyncio
    async def test_subagent_tool_execution(self, mock_anthropic_client, _clean_subagent_state):
        """Sub-agent should execute tools and return final text."""
        from tools.subagent import run_subagent

        _, set_responses = mock_anthropic_client

        # Turn 1: tool call, Turn 2: text response
        tool_events = _build_tool_events("t1", "Read", {"file_path": "test.py"})
        text_events = _build_text_events("Found the file contents")

        set_responses([tool_events, text_events])

        with patch("core.agent.execute_tool", new_callable=AsyncMock, return_value='{"content": "print(1)"}'):
            with patch("core.rate_limit.check_rate_limit", return_value=(True, "")):
                output, messages = await run_subagent(
                    prompt="Read test.py",
                    system="You are helpful.",
                    tool_names=["Read"],
                    max_turns=5,
                )

        assert "Found the file contents" in output
        # user, assistant+tool, tool_result, assistant+text
        assert len(messages) == 4


# ── Sub-agent tool handler ─────────────────────────────────────────────

class TestSubAgentToolHandler:
    @pytest.mark.asyncio
    async def test_handle_agent_spawn(self, mock_anthropic_client, _clean_subagent_state):
        """The Agent tool handler should spawn and return results."""
        from tools.subagent import handle_agent

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("Handler result")])

        result_json = await handle_agent({
            "prompt": "Explore the project",
            "agent_type": "explorer",
        })

        result = json.loads(result_json)
        assert "agent_id" in result
        assert "Handler result" in result.get("result", "")

    @pytest.mark.asyncio
    async def test_handle_agent_background(self, mock_anthropic_client, _clean_subagent_state):
        """Background agent should return agent_id immediately."""
        from tools.subagent import handle_agent

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("BG result")])

        result_json = await handle_agent({
            "prompt": "Long running task",
            "agent_type": "researcher",
            "run_in_background": True,
            "description": "Test background agent",
        })

        result = json.loads(result_json)
        assert "agent_id" in result
        assert result["status"] == "started"


# ── Agent status ───────────────────────────────────────────────────────

class TestAgentStatus:
    @pytest.mark.asyncio
    async def test_status_of_completed_agent(self, mock_anthropic_client, _clean_subagent_state):
        """Should return status for a completed agent."""
        from tools.subagent import handle_agent, handle_agent_status

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("Done")])

        # Spawn foreground agent
        spawn_result = json.loads(await handle_agent({
            "prompt": "Quick task",
            "agent_type": "default",
        }))

        agent_id = spawn_result.get("agent_id")
        if agent_id:
            status_json = await handle_agent_status({"agent_id": agent_id})
            status = json.loads(status_json)
            assert status["status"] in ("completed", "done")

    @pytest.mark.asyncio
    async def test_status_list_all(self, _clean_subagent_state):
        """Listing all agents when none exist should return empty."""
        from tools.subagent import handle_agent_status

        result_json = await handle_agent_status({})
        result = json.loads(result_json)
        assert "agents" in result or "error" not in result
