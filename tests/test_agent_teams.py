"""Tests for Agent teams + ``SendMessage`` (R2 of CC parity plan).

Covers:
  - Tool registry surfaces ``SendMessage`` with CC-matching parameter names
    (``to``, ``message``).
  - ``Agent`` tool result includes a stable ``agent_id`` for both the
    user-defined-agent path and the builtin ``AgentType`` path.
  - ``SendMessage`` round-trip continues the same sub-conversation
    (``prior_messages`` are passed through, the new prompt is appended).
  - Tenant isolation: cross-user and cross-conversation ids are rejected;
    unknown ids return a clear error.
  - Failed initial dispatch blocks SendMessage with a descriptive error.
  - ``SendMessage`` is NOT advertised to subagents themselves -- excluded
    from their tool surface so we never hand a subagent a way to talk
    sideways to its sibling.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
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
async def teams_db(test_db, test_user):
    @asynccontextmanager
    async def _get_test_db():
        yield test_db

    with patch("core.agent_crud.get_db", side_effect=_get_test_db), \
         patch("core.database.get_db", side_effect=_get_test_db), \
         patch("core.agent_runner.get_db", side_effect=_get_test_db):
        yield test_db, test_user


# ── Tool registry shape ────────────────────────────────────────────────

class TestSendMessageRegistration:
    def test_send_message_is_registered(self):
        from tools.registry import get_tool_schemas

        schemas = get_tool_schemas(["SendMessage"])
        assert len(schemas) == 1
        assert schemas[0]["name"] == "SendMessage"

    def test_alias_resolves(self):
        from tools.registry import get_tool_schemas

        for alias in ("send_message",):
            schemas = get_tool_schemas([alias])
            assert len(schemas) == 1
            assert schemas[0]["name"] == "SendMessage"

    def test_param_names_match_cc(self):
        """Plan asks for parameter names matching CC verbatim so muscle
        memory carries over -- ``to`` and ``message``, NOT ``agent_id`` /
        ``prompt``."""
        from tools.registry import get_tool_schemas

        schema = get_tool_schemas(["SendMessage"])[0]["input_schema"]
        required = set(schema["required"])
        assert {"to", "message"} == required
        # Defensive: no spurious required fields.
        props = set(schema["properties"].keys())
        assert "to" in props and "message" in props

    def test_send_message_excluded_from_subagent_surface(self):
        """A subagent must NOT be handed SendMessage -- spawning sideways
        chatter from a subagent would break the team-conductor pattern
        where only the parent orchestrates."""
        from core.agent_types import EXCLUDED_TOOLS

        assert "SendMessage" in EXCLUDED_TOOLS


# ── Agent dispatch returns agent_id (existing behaviour) ──────────────

class TestAgentReturnsAgentId:
    @pytest.mark.asyncio
    async def test_builtin_dispatch_returns_agent_id(
        self, mock_anthropic_client, _clean_subagent_state,
    ):
        from tools.subagent import _active_agents, handle_task

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("explorer reply")])

        result_json = await handle_task({
            "subagent_type": "explorer",
            "prompt": "find stuff",
            "description": "probe",
        })
        result = json.loads(result_json)

        assert result.get("agent_id"), result
        # State stashed so SendMessage can resume.
        state = _active_agents[result["agent_id"]]
        assert state.builtin_type_name == "explorer"
        assert state.agent_def_id is None
        assert state.status == "completed"
        # Captured messages: at minimum the original user turn + assistant.
        assert len(state.messages) >= 2
        assert state.messages[0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_user_agent_dispatch_returns_agent_id(
        self, mock_anthropic_client, _clean_subagent_state, teams_db,
    ):
        from core.agent_crud import create_agent
        from core.session import (
            create_connection, set_current_session, destroy_connection,
        )
        from tools.subagent import _active_agents, handle_task

        db, user = teams_db
        agent = await create_agent(
            user_id=user.id, name="reviewer",
            description="Code reviewer.",
            system_prompt="You review code.",
            tools=["Read"], max_turns=3,
        )

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("first review")])

        create_connection("teams-sess-1", user_id=user.id)
        set_current_session("teams-sess-1")
        try:
            result_json = await handle_task({
                "subagent_type": "reviewer",
                "prompt": "review file foo.py",
                "description": "review",
            })
        finally:
            destroy_connection("teams-sess-1")

        result = json.loads(result_json)
        assert result.get("agent_id")
        state = _active_agents[result["agent_id"]]
        assert state.agent_def_id == agent.id
        assert state.user_id == user.id
        assert state.parent_session_id == "teams-sess-1"
        assert state.status == "completed"


# ── SendMessage round-trip ────────────────────────────────────────────

class TestSendMessageRoundTrip:
    @pytest.mark.asyncio
    async def test_continues_same_sub_conversation(
        self, mock_anthropic_client, _clean_subagent_state, teams_db,
    ):
        """SendMessage feeds the captured ``prior_messages`` plus the new
        user turn back through the runner -- the second response should
        see the first turn's history."""
        from core.agent_crud import create_agent
        from core.session import (
            create_connection, set_current_session, destroy_connection,
            set_current_cid,
        )
        from tools.subagent import _active_agents, handle_send_message, handle_task

        db, user = teams_db
        await create_agent(
            user_id=user.id, name="reviewer",
            description="Code reviewer.",
            system_prompt="You review code.",
            tools=["Read"], max_turns=3,
        )

        _, set_responses = mock_anthropic_client
        # Two turns: initial dispatch, then SendMessage continuation.
        set_responses([
            _build_text_events("first reply"),
            _build_text_events("second reply"),
        ])

        create_connection("teams-sess-2", user_id=user.id)
        set_current_session("teams-sess-2")
        set_current_cid("teams-cid-2")
        try:
            initial = json.loads(await handle_task({
                "subagent_type": "reviewer",
                "prompt": "first prompt",
                "description": "round-trip",
            }))
            agent_id = initial["agent_id"]
            state_before = _active_agents[agent_id]
            captured_before = list(state_before.messages)
            assert any(
                m.get("role") == "user" and m.get("content") == "first prompt"
                for m in captured_before
            )

            follow = json.loads(await handle_send_message({
                "to": agent_id,
                "message": "second prompt",
            }))
        finally:
            destroy_connection("teams-sess-2")

        assert follow.get("agent_id") == agent_id
        assert "second reply" in (follow.get("result") or "")

        state_after = _active_agents[agent_id]
        # The new user turn was appended to the captured messages.
        assert any(
            m.get("role") == "user" and m.get("content") == "second prompt"
            for m in state_after.messages
        )
        # Original first prompt is still in there (continuity).
        assert any(
            m.get("role") == "user" and m.get("content") == "first prompt"
            for m in state_after.messages
        )

    @pytest.mark.asyncio
    async def test_round_trip_for_builtin_path(
        self, mock_anthropic_client, _clean_subagent_state,
    ):
        from tools.subagent import _active_agents, handle_send_message, handle_task

        _, set_responses = mock_anthropic_client
        set_responses([
            _build_text_events("first explorer reply"),
            _build_text_events("second explorer reply"),
        ])

        initial = json.loads(await handle_task({
            "subagent_type": "explorer",
            "prompt": "first",
            "description": "rt",
        }))
        agent_id = initial["agent_id"]

        follow = json.loads(await handle_send_message({
            "to": agent_id, "message": "second",
        }))

        assert follow.get("result") and "second explorer reply" in follow["result"]
        state = _active_agents[agent_id]
        assert any(
            m.get("role") == "user" and m.get("content") == "second"
            for m in state.messages
        )


# ── Tenant isolation + error paths ────────────────────────────────────

class TestSendMessageIsolation:
    @pytest.mark.asyncio
    async def test_unknown_id_rejected(self, _clean_subagent_state):
        from tools.subagent import handle_send_message

        result = json.loads(await handle_send_message({
            "to": "nonexistent",
            "message": "hello",
        }))
        assert "error" in result
        assert "Unknown agent_id" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_to_param(self, _clean_subagent_state):
        from tools.subagent import handle_send_message

        result = json.loads(await handle_send_message({"message": "x"}))
        assert "error" in result and "to" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_message_param(
        self, _clean_subagent_state, mock_anthropic_client,
    ):
        from tools.subagent import handle_send_message, handle_task

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("ok")])
        initial = json.loads(await handle_task({
            "subagent_type": "explorer", "prompt": "x", "description": "x",
        }))
        result = json.loads(await handle_send_message({
            "to": initial["agent_id"],
        }))
        assert "error" in result and "message" in result["error"]

    @pytest.mark.asyncio
    async def test_cross_user_id_rejected(
        self, mock_anthropic_client, _clean_subagent_state, teams_db,
    ):
        from core.agent_crud import create_agent
        from core.db_models import User
        from core.session import (
            create_connection, set_current_session, destroy_connection,
            set_current_cid,
        )
        from tools.subagent import handle_send_message, handle_task

        db, user_a = teams_db
        user_b = User(
            email="b@example.com", display_name="b",
            password_hash="x", is_admin=False,
        )
        db.add(user_b)
        await db.commit()
        await db.refresh(user_b)

        await create_agent(
            user_id=user_a.id, name="reviewer",
            description="d", system_prompt="x",
            tools=["Read"], max_turns=2,
        )

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("hi")])

        create_connection("iso-a", user_id=user_a.id)
        set_current_session("iso-a")
        set_current_cid("iso-cid-a")
        try:
            initial = json.loads(await handle_task({
                "subagent_type": "reviewer",
                "prompt": "p", "description": "d",
            }))
            agent_id = initial["agent_id"]
        finally:
            destroy_connection("iso-a")

        # Switch to user_b's session and try to address user_a's agent_id.
        create_connection("iso-b", user_id=user_b.id)
        set_current_session("iso-b")
        set_current_cid("iso-cid-b")
        try:
            result = json.loads(await handle_send_message({
                "to": agent_id, "message": "leak?",
            }))
        finally:
            destroy_connection("iso-b")

        assert "error" in result
        assert "different user" in result["error"]

    @pytest.mark.asyncio
    async def test_cross_conversation_id_rejected(
        self, mock_anthropic_client, _clean_subagent_state, teams_db,
    ):
        from core.agent_crud import create_agent
        from core.session import (
            create_connection, set_current_session, destroy_connection,
            set_current_cid,
        )
        from tools.subagent import handle_send_message, handle_task

        db, user = teams_db
        await create_agent(
            user_id=user.id, name="reviewer",
            description="d", system_prompt="x",
            tools=["Read"], max_turns=2,
        )

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("hi")])

        create_connection("iso-sess", user_id=user.id)
        set_current_session("iso-sess")
        set_current_cid("conv-A")
        try:
            initial = json.loads(await handle_task({
                "subagent_type": "reviewer",
                "prompt": "p", "description": "d",
            }))
            agent_id = initial["agent_id"]

            # Same user, same session, but a sibling conversation now.
            set_current_cid("conv-B")
            result = json.loads(await handle_send_message({
                "to": agent_id, "message": "leak?",
            }))
        finally:
            destroy_connection("iso-sess")

        assert "error" in result
        assert "different conversation" in result["error"]

    @pytest.mark.asyncio
    async def test_failed_initial_blocks_send_message(
        self, _clean_subagent_state,
    ):
        """If the initial dispatch failed, SendMessage refuses to resume --
        there is nothing to continue from."""
        import time
        from tools.subagent import (
            SubAgentState, _active_agents, handle_send_message,
        )

        state = SubAgentState(
            agent_id="failed-id",
            subagent_type="explorer",
            status="failed",
            result="boom",
            created_at=time.time(),
            builtin_type_name="explorer",
            done_event=asyncio.Event(),
            lock=asyncio.Lock(),
        )
        state.done_event.set()
        _active_agents["failed-id"] = state

        result = json.loads(await handle_send_message({
            "to": "failed-id", "message": "retry?",
        }))
        assert "error" in result
        assert "failed" in result["error"]


# ── Pause/resume around in-flight dispatch ────────────────────────────

class TestSendMessageAwaitsInitial:
    @pytest.mark.asyncio
    async def test_send_message_waits_for_in_flight_dispatch(
        self, _clean_subagent_state,
    ):
        """If the initial dispatch hasn't fired ``done_event`` yet,
        SendMessage waits rather than racing into resumption with empty
        captured state."""
        import time
        from tools.subagent import (
            SubAgentState, _active_agents, handle_send_message,
        )

        ev = asyncio.Event()
        # Build a state that looks in-flight (no done_event.set()) and then
        # set it from a sibling task. SendMessage should unblock.
        state = SubAgentState(
            agent_id="busy-id",
            subagent_type="explorer",
            status="running",
            created_at=time.time(),
            builtin_type_name="explorer",
            done_event=ev,
            lock=asyncio.Lock(),
            messages=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "ok"},
            ],
        )
        # Mark completed so when the wait unblocks, we proceed past the
        # "failed" guard. We don't actually want to run the model in this
        # test -- we just verify that SendMessage *waits*.
        async def _release_then_fail():
            await asyncio.sleep(0.05)
            state.status = "failed"
            state.result = "intentional"
            ev.set()

        _active_agents["busy-id"] = state

        sender = asyncio.create_task(handle_send_message({
            "to": "busy-id", "message": "second",
        }))
        releaser = asyncio.create_task(_release_then_fail())

        result_json = await sender
        await releaser
        result = json.loads(result_json)
        # Because we flipped status to "failed" before the lock acquired,
        # the response should be the failed-initial error -- proving that
        # SendMessage actually waited for the event before checking.
        assert "error" in result
        assert "failed" in result["error"]
