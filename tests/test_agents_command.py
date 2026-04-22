"""Tests for the ``/agents`` built-in slash command (U1 of CC parity plan).

Covers:
  - Bare ``/agents`` lists agents grouped by source with the
    ``@agent-<name>`` dispatch hint per row, and a help footer
  - ``/agents show <name>`` dumps name, source, model, tools, system prompt
  - ``/agents enable <name>`` and ``/agents disable <name>`` toggle
    ``AgentDefinition.enabled`` in the DB and surface re-dispatch hints
  - Unknown name returns a helpful error
  - ``/agents create <name>`` returns the W1 deferral message (so users
    aren't stuck before the wizard ships)
  - Chat-side wiring: ``/agents`` typed in chat produces a ``text_delta``
    + ``turn_complete``; ``enable``/``disable`` also fire ``agents_updated``
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from core.agent_crud import create_agent, get_agent_by_name
from core.db_models import AgentDefinition
from handlers.commands import handle_agents_command


@pytest_asyncio.fixture
async def patched_db(test_db, test_user):
    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.agent_crud.get_db", side_effect=_get_db), \
         patch("core.database.get_db", side_effect=_get_db):
        yield test_db, test_user


# ── Direct handler tests ────────────────────────────────────────────────

class TestListing:
    @pytest.mark.asyncio
    async def test_empty_roster_explains_how_to_create(self, patched_db):
        _, user = patched_db
        result = await handle_agents_command("", user.id)
        assert "No agents yet" in result
        assert "AGENT.md" in result

    @pytest.mark.asyncio
    async def test_lists_agents_grouped_by_source_with_dispatch_hint(
        self, patched_db, test_db
    ):
        _, user = patched_db
        await create_agent(
            user_id=user.id, name="alpha",
            description="User-authored alpha.",
            system_prompt="alpha prompt",
        )
        await create_agent(
            user_id=user.id, name="beta",
            description="User-authored beta.",
            system_prompt="beta prompt",
        )
        # Force one of them into a different source (simulates plugin install)
        from sqlalchemy import update
        await test_db.execute(
            update(AgentDefinition)
            .where(AgentDefinition.name == "beta", AgentDefinition.user_id == user.id)
            .values(source="plugin:research-pack")
        )
        await test_db.commit()

        result = await handle_agents_command("", user.id)

        # Header + both agents present
        assert "**Agents**" in result
        assert "alpha" in result
        assert "beta" in result
        # Source labels
        assert "_user_" in result
        assert "plugin (research-pack)" in result
        # Dispatch hints printed verbatim so users can copy/paste
        assert "@agent-alpha <prompt>" in result
        assert "@agent-beta <prompt>" in result
        # Footer documents the subcommands
        assert "show" in result and "enable" in result and "disable" in result

    @pytest.mark.asyncio
    async def test_disabled_agents_marked_in_listing(self, patched_db):
        _, user = patched_db
        agent = await create_agent(
            user_id=user.id, name="dimmed",
            description="A quiet one.",
            system_prompt="...",
        )
        await handle_agents_command(f"disable {agent.name}", user.id)

        result = await handle_agents_command("", user.id)
        assert "dimmed" in result
        assert "(disabled)" in result


class TestShow:
    @pytest.mark.asyncio
    async def test_show_dumps_full_definition(self, patched_db):
        _, user = patched_db
        await create_agent(
            user_id=user.id, name="trader",
            description="Equities desk persona.",
            system_prompt="You are a trader.",
            tools=["WebSearch", "WebFetch"],
            model="claude-sonnet-4-6",
        )
        result = await handle_agents_command("show trader", user.id)
        assert "**Agent: trader**" in result
        assert "Equities desk persona." in result
        assert "claude-sonnet-4-6" in result
        assert "WebSearch" in result and "WebFetch" in result
        assert "You are a trader." in result
        assert "@agent-trader" in result

    @pytest.mark.asyncio
    async def test_show_unknown_name_is_helpful(self, patched_db):
        _, user = patched_db
        result = await handle_agents_command("show ghost", user.id)
        assert "not found" in result.lower()
        assert "/agents" in result


class TestEnableDisable:
    @pytest.mark.asyncio
    async def test_disable_then_enable_toggles_db_flag(self, patched_db):
        _, user = patched_db
        await create_agent(
            user_id=user.id, name="toggle-me",
            description="Toggleable.",
            system_prompt="...",
        )
        # Pre-condition: created enabled
        a = await get_agent_by_name("toggle-me", user.id)
        assert a.enabled is True

        result = await handle_agents_command("disable toggle-me", user.id)
        assert "disabled" in result.lower()
        assert "@agent-toggle-me" in result  # mentions re-dispatch hint
        a = await get_agent_by_name("toggle-me", user.id)
        assert a.enabled is False

        result = await handle_agents_command("enable toggle-me", user.id)
        assert "enabled" in result.lower()
        a = await get_agent_by_name("toggle-me", user.id)
        assert a.enabled is True

    @pytest.mark.asyncio
    async def test_double_disable_is_a_noop_with_message(self, patched_db):
        _, user = patched_db
        await create_agent(
            user_id=user.id, name="already-off",
            description="x", system_prompt="x",
        )
        await handle_agents_command("disable already-off", user.id)
        result = await handle_agents_command("disable already-off", user.id)
        assert "already" in result.lower()

    @pytest.mark.asyncio
    async def test_enable_unknown_returns_error(self, patched_db):
        _, user = patched_db
        result = await handle_agents_command("enable nothing-here", user.id)
        assert "not found" in result.lower()


class TestCreateDirectCall:
    @pytest.mark.asyncio
    async def test_create_pointer_when_called_outside_chat(self, patched_db):
        _, user = patched_db
        # The wizard itself lives in handlers/agent_handler.py because it
        # needs a session_id; direct callers (tests, scripts) get a
        # pointer to chat + the manual AGENT.md fallback.
        result = await handle_agents_command("create trader", user.id)
        assert "wizard" in result.lower()
        assert "AGENT.md" in result


class TestHelp:
    @pytest.mark.asyncio
    async def test_unknown_subcommand_prints_help(self, patched_db):
        _, user = patched_db
        result = await handle_agents_command("frobnicate", user.id)
        assert "Agent commands" in result
        assert "/agents show" in result
        assert "/agents enable" in result


# ── Chat dispatch wiring ────────────────────────────────────────────────

@pytest_asyncio.fixture
async def chat_db(test_db, test_user):
    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.agent_crud.get_db", side_effect=_get_db), \
         patch("core.database.get_db", side_effect=_get_db):
        yield test_db, test_user


class TestChatWiring:
    @pytest.mark.asyncio
    async def test_slash_agents_in_chat_emits_text_and_complete(self, chat_db):
        from core.session import (
            create_connection, create_conv_session, destroy_connection,
            set_current_session,
        )
        from handlers.agent_handler import handle_user_message

        _, user = chat_db
        sid, cid = "agents-sess-1", "agents-cid-1"
        create_connection(sid, user_id=user.id)
        create_conv_session(cid, sid)
        set_current_session(sid)

        await create_agent(
            user_id=user.id, name="visible-one",
            description="Visible agent.", system_prompt="...",
        )

        sent: list[tuple[str, dict]] = []

        async def send_event(name, payload):
            sent.append((name, payload))

        try:
            with patch(
                "handlers.agent_handler.save_conversation",
                new=AsyncMock(return_value="conv-x"),
            ):
                await handle_user_message(
                    sid, cid,
                    {"text": "/agents"},
                    send_event,
                    build_system_prompt=lambda *a, **kw: "ignored",
                    outputs_dir=None,
                )

            event_names = [n for n, _ in sent]
            assert "text_delta" in event_names
            assert "turn_complete" in event_names
            text_payload = "".join(
                p["text"] for n, p in sent if n == "text_delta"
            )
            assert "visible-one" in text_payload
            # Bare list shouldn't trigger an agents_updated bump
            assert "agents_updated" not in event_names
        finally:
            destroy_connection(sid)

    @pytest.mark.asyncio
    async def test_slash_agents_disable_fires_agents_updated(self, chat_db):
        from core.session import (
            create_connection, create_conv_session, destroy_connection,
            set_current_session,
        )
        from handlers.agent_handler import handle_user_message

        _, user = chat_db
        sid, cid = "agents-sess-2", "agents-cid-2"
        create_connection(sid, user_id=user.id)
        create_conv_session(cid, sid)
        set_current_session(sid)

        await create_agent(
            user_id=user.id, name="bumpable",
            description="x", system_prompt="x",
        )

        sent: list[tuple[str, dict]] = []

        async def send_event(name, payload):
            sent.append((name, payload))

        try:
            with patch(
                "handlers.agent_handler.save_conversation",
                new=AsyncMock(return_value="conv-x"),
            ):
                await handle_user_message(
                    sid, cid,
                    {"text": "/agents disable bumpable"},
                    send_event,
                    build_system_prompt=lambda *a, **kw: "ignored",
                    outputs_dir=None,
                )

            event_names = [n for n, _ in sent]
            assert "agents_updated" in event_names, (
                "frontend roster should refresh after enable/disable"
            )
        finally:
            destroy_connection(sid)
