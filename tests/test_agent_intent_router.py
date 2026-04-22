"""Tests for the agent intent router (R1 of CC parity plan).

Covers:
  - ``match_agent_by_intent`` scoring: name word (x2) + description word (x1)
    above a configurable threshold; disabled / foreign-user agents excluded;
    "default" user and empty input return None.
  - ``build_system_prompt`` shape: agents block uses the new ROUTING header,
    sits above other sections, includes the worked example; per-turn
    ``routing_hint`` rides at the very top.
  - Chat wiring: a non-slash, non-mention message that intent-matches an
    agent reaches ``build_system_prompt`` with a populated ``routing_hint``;
    a non-matching message receives ``routing_hint=None``; an explicit
    ``@agent-<name>`` mention bypasses the matcher entirely.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio


# ── match_agent_by_intent ───────────────────────────────────────────────

@pytest_asyncio.fixture
async def crud_db(test_db, test_user):
    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.agent_crud.get_db", side_effect=_get_db):
        yield test_db, test_user


class TestMatchAgentByIntent:
    @pytest.mark.asyncio
    async def test_name_word_match_above_threshold(self, crud_db):
        from core.agent_crud import create_agent, match_agent_by_intent

        _, user = crud_db
        await create_agent(
            user_id=user.id, name="person-research",
            description="Research a person.",
            system_prompt="x",
        )
        # "person" + "research" both appear -> score 4 (>= 2)
        match = await match_agent_by_intent(
            "please research the person John at Acme", user.id,
        )
        assert match is not None
        assert match.name == "person-research"

    @pytest.mark.asyncio
    async def test_description_words_above_threshold(self, crud_db):
        from core.agent_crud import create_agent, match_agent_by_intent

        _, user = crud_db
        await create_agent(
            user_id=user.id, name="briefing",
            description="Daily morning portfolio summary.",
            system_prompt="x",
        )
        # "morning" + "portfolio" = 2 description hits -> score 2 (>= 2)
        match = await match_agent_by_intent(
            "give me a morning portfolio update", user.id,
        )
        assert match is not None
        assert match.name == "briefing"

    @pytest.mark.asyncio
    async def test_below_threshold_returns_none(self, crud_db):
        from core.agent_crud import create_agent, match_agent_by_intent

        _, user = crud_db
        await create_agent(
            user_id=user.id, name="briefing",
            description="Daily morning portfolio summary.",
            system_prompt="x",
        )
        # Only "morning" (>3 chars) hits -> score 1, below threshold.
        match = await match_agent_by_intent("good morning!", user.id)
        assert match is None

    @pytest.mark.asyncio
    async def test_disabled_agent_skipped(self, crud_db):
        from core.agent_crud import (
            create_agent, match_agent_by_intent, update_agent,
        )

        _, user = crud_db
        agent = await create_agent(
            user_id=user.id, name="person-research",
            description="Research a person.",
            system_prompt="x",
        )
        await update_agent(agent.id, user.id, enabled=False)
        match = await match_agent_by_intent(
            "research the person John", user.id,
        )
        assert match is None

    @pytest.mark.asyncio
    async def test_foreign_user_agent_invisible(self, crud_db, test_db):
        from core.agent_crud import create_agent, match_agent_by_intent
        from core.db_models import User

        _, user = crud_db
        # Create a second user and give THEM the agent.
        other = User(
            email="other@example.com", display_name="other",
            password_hash="x", is_admin=False,
        )
        test_db.add(other)
        await test_db.commit()
        await test_db.refresh(other)
        await create_agent(
            user_id=other.id, name="person-research",
            description="Research a person.",
            system_prompt="x",
        )
        match = await match_agent_by_intent(
            "research the person John", user.id,
        )
        assert match is None

    @pytest.mark.asyncio
    async def test_default_user_returns_none(self, crud_db):
        from core.agent_crud import match_agent_by_intent

        match = await match_agent_by_intent("research John", "default")
        assert match is None

    @pytest.mark.asyncio
    async def test_empty_message_returns_none(self, crud_db):
        from core.agent_crud import create_agent, match_agent_by_intent

        _, user = crud_db
        await create_agent(
            user_id=user.id, name="person-research",
            description="Research a person.",
            system_prompt="x",
        )
        assert await match_agent_by_intent("", user.id) is None
        assert await match_agent_by_intent("   ", user.id) is None

    @pytest.mark.asyncio
    async def test_picks_higher_scoring_agent(self, crud_db):
        from core.agent_crud import create_agent, match_agent_by_intent

        _, user = crud_db
        await create_agent(
            user_id=user.id, name="person-research",
            description="Research a person, find LinkedIn.",
            system_prompt="x",
        )
        await create_agent(
            user_id=user.id, name="briefing",
            description="Morning summary.",
            system_prompt="x",
        )
        # Mentions "research" + "person" + "LinkedIn" -> person-research wins.
        match = await match_agent_by_intent(
            "research the person John on LinkedIn", user.id,
        )
        assert match is not None
        assert match.name == "person-research"

    @pytest.mark.asyncio
    async def test_threshold_override(self, crud_db):
        from core.agent_crud import create_agent, match_agent_by_intent

        _, user = crud_db
        await create_agent(
            user_id=user.id, name="briefing",
            description="Daily summary.",
            system_prompt="x",
        )
        # Score 1 from "daily" -- default threshold rejects, lower accepts.
        assert await match_agent_by_intent("daily check", user.id) is None
        match = await match_agent_by_intent(
            "daily check", user.id, threshold=1,
        )
        assert match is not None


# ── build_system_prompt: ROUTING block + routing_hint ─────────────────

class TestSystemPromptShape:
    def test_routing_header_present_when_agents_provided(self):
        from core.system_prompt import build_system_prompt

        prompt = build_system_prompt(
            available_agents=[("person-research", "Research a person.")],
        )
        assert "ROUTING -- read before responding" in prompt
        # No legacy header text leaked through.
        assert "## Available Agents" not in prompt

    def test_worked_example_present(self):
        from core.system_prompt import build_system_prompt

        prompt = build_system_prompt(
            available_agents=[("person-research", "Research a person.")],
        )
        assert "person-research" in prompt
        assert "Agent(agent_type=" in prompt
        assert "WRONG" in prompt and "CORRECT" in prompt

    def test_agents_block_above_skills(self):
        """Plan asks the routing block to sit above skill listings so the
        model reads delegation rules first."""
        from core.system_prompt import build_system_prompt

        prompt = build_system_prompt(
            available_agents=[("foo", "Do foo things.")],
        )
        # Skill / Command blocks may be absent if no skills registered, but
        # the ROUTING block must precede any sections that follow base.
        routing_idx = prompt.find("ROUTING -- read before responding")
        # Sections that legitimately can sit below ROUTING:
        for later in (
            "## Project Instructions",
            "## Project Rules",
            "## Available Skills",
            "## Available Commands",
            "## Auto-Activated Skills",
            "## Your Memory",
        ):
            i = prompt.find(later)
            if i >= 0:
                assert i > routing_idx, f"{later} should come after ROUTING block"

    def test_routing_hint_injected_at_top(self):
        from core.system_prompt import build_system_prompt

        hint = "TURN-SPECIFIC delegate to `person-research`."
        prompt = build_system_prompt(
            available_agents=[("person-research", "Research a person.")],
            routing_hint=hint,
        )
        assert "## TURN ROUTING -- read first" in prompt
        assert hint in prompt
        # Hint precedes the ROUTING block which precedes everything else.
        assert prompt.find("TURN ROUTING") < prompt.find(
            "ROUTING -- read before responding"
        )

    def test_no_routing_hint_section_when_omitted(self):
        from core.system_prompt import build_system_prompt

        prompt = build_system_prompt(
            available_agents=[("foo", "bar")],
        )
        assert "TURN ROUTING" not in prompt


# ── Chat wiring: matcher hooks into handle_user_message ───────────────

@pytest_asyncio.fixture
async def chat_db(test_db, test_user):
    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.agent_crud.get_db", side_effect=_get_db), \
         patch("core.database.get_db", side_effect=_get_db), \
         patch("core.agent_runner.get_db", side_effect=_get_db):
        yield test_db, test_user


def _capture_build_system_prompt():
    """Returns (callable, container) -- container[0] holds last kwargs."""
    captured: dict = {}

    def _bp(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "system-prompt"

    return _bp, captured


class TestChatWiring:
    @pytest.mark.asyncio
    async def test_intent_match_injects_routing_hint(
        self, chat_db, mock_anthropic_client,
    ):
        from core.agent_crud import create_agent
        from core.session import (
            create_connection, create_conv_session, destroy_connection,
            set_current_session,
        )
        from handlers.agent_handler import handle_user_message
        from tests.conftest import _build_text_events

        _, user = chat_db
        await create_agent(
            user_id=user.id, name="person-research",
            description="Research a person.",
            system_prompt="x",
        )

        sid, cid = "router-sess-1", "router-cid-1"
        create_connection(sid, user_id=user.id)
        create_conv_session(cid, sid)
        set_current_session(sid)

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("ok")])

        bp, captured = _capture_build_system_prompt()

        async def send_event(name, payload):
            pass

        try:
            with patch(
                "handlers.agent_handler.save_conversation",
                new=AsyncMock(return_value="conv-x"),
            ):
                await handle_user_message(
                    sid, cid,
                    {"text": "please research this person John at Acme"},
                    send_event,
                    build_system_prompt=bp,
                    outputs_dir=None,
                )

            hint = captured["kwargs"].get("routing_hint")
            assert hint is not None, "routing_hint should be set on intent match"
            assert "person-research" in hint
            assert "Agent(agent_type=" in hint
        finally:
            destroy_connection(sid)

    @pytest.mark.asyncio
    async def test_no_match_leaves_routing_hint_none(
        self, chat_db, mock_anthropic_client,
    ):
        from core.agent_crud import create_agent
        from core.session import (
            create_connection, create_conv_session, destroy_connection,
            set_current_session,
        )
        from handlers.agent_handler import handle_user_message
        from tests.conftest import _build_text_events

        _, user = chat_db
        await create_agent(
            user_id=user.id, name="person-research",
            description="Research a person.",
            system_prompt="x",
        )

        sid, cid = "router-sess-2", "router-cid-2"
        create_connection(sid, user_id=user.id)
        create_conv_session(cid, sid)
        set_current_session(sid)

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("ok")])

        bp, captured = _capture_build_system_prompt()

        async def send_event(name, payload):
            pass

        try:
            with patch(
                "handlers.agent_handler.save_conversation",
                new=AsyncMock(return_value="conv-x"),
            ):
                await handle_user_message(
                    sid, cid,
                    {"text": "what is the capital of France?"},
                    send_event,
                    build_system_prompt=bp,
                    outputs_dir=None,
                )

            assert captured["kwargs"].get("routing_hint") is None
        finally:
            destroy_connection(sid)

    @pytest.mark.asyncio
    async def test_explicit_mention_bypasses_matcher(self, chat_db):
        """``@agent-<name>`` returns early -- the matcher must NOT fire (we
        already know the user's intent)."""
        from core.agent_crud import create_agent
        from core.agent_runner import AgentRunResult
        from core.session import (
            create_connection, create_conv_session, destroy_connection,
            set_current_session,
        )
        from handlers.agent_handler import handle_user_message

        _, user = chat_db
        await create_agent(
            user_id=user.id, name="person-research",
            description="Research a person.",
            system_prompt="x",
        )

        sid, cid = "router-sess-3", "router-cid-3"
        create_connection(sid, user_id=user.id)
        create_conv_session(cid, sid)
        set_current_session(sid)

        bp, captured = _capture_build_system_prompt()
        fake_run = AgentRunResult(
            run_id="r1", status="completed",
            result_text="done", error=None, tokens_used=1,
        )

        async def send_event(name, payload):
            pass

        try:
            with patch(
                "core.agent_runner.run_agent_once",
                new=AsyncMock(return_value=fake_run),
            ), patch(
                "core.agent_crud.match_agent_by_intent",
                new=AsyncMock(return_value=None),
            ) as matcher_mock, patch(
                "handlers.agent_handler.save_conversation",
                new=AsyncMock(return_value="conv-x"),
            ):
                await handle_user_message(
                    sid, cid,
                    {"text": "@agent-person-research find John"},
                    send_event,
                    build_system_prompt=bp,
                    outputs_dir=None,
                )

            # Mention path returns before either build_system_prompt or the
            # intent matcher are reached.
            matcher_mock.assert_not_called()
            assert captured == {}
        finally:
            destroy_connection(sid)

    @pytest.mark.asyncio
    async def test_slash_command_bypasses_matcher(
        self, chat_db, mock_anthropic_client,
    ):
        """An explicit ``/foo`` (matched or not) should NOT trigger the agent
        matcher -- the user already chose their dispatch surface."""
        from core.agent_crud import create_agent
        from core.session import (
            create_connection, create_conv_session, destroy_connection,
            set_current_session,
        )
        from handlers.agent_handler import handle_user_message
        from tests.conftest import _build_text_events

        _, user = chat_db
        await create_agent(
            user_id=user.id, name="person-research",
            description="Research a person.",
            system_prompt="x",
        )

        sid, cid = "router-sess-4", "router-cid-4"
        create_connection(sid, user_id=user.id)
        create_conv_session(cid, sid)
        set_current_session(sid)

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("ok")])

        bp, captured = _capture_build_system_prompt()

        async def send_event(name, payload):
            pass

        try:
            with patch(
                "core.agent_crud.match_agent_by_intent",
                new=AsyncMock(return_value=None),
            ) as matcher_mock, patch(
                "handlers.agent_handler.save_conversation",
                new=AsyncMock(return_value="conv-x"),
            ):
                # Use a slash that won't match any skill -- still no matcher.
                await handle_user_message(
                    sid, cid,
                    {"text": "/no-such-skill research that person John"},
                    send_event,
                    build_system_prompt=bp,
                    outputs_dir=None,
                )

            matcher_mock.assert_not_called()
            assert captured["kwargs"].get("routing_hint") is None
        finally:
            destroy_connection(sid)
