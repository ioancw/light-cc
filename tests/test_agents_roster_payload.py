"""Tests for the agents-roster payload sent over WS for the U2 picker.

The frontend ``@agent-`` autocomplete reads ``appState.agents`` -- a flat list
of ``{name, description, source}`` rows -- which is populated from two events:

1. ``connected`` (initial handshake, ``data.agents``)
2. ``agents_updated`` (bumped whenever a wizard / enable / disable / reload
   mutates the roster, ``data.agents``)

Both ship the *same* shape so the frontend treats them uniformly. These
tests pin the contract: enabled-only filter, plugin-namespaced names pass
through verbatim, and the shape remains a list of dicts (not the legacy
empty-payload bump).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from core.agent_crud import create_agent, update_agent
from handlers.commands import list_agents_for_client


@pytest_asyncio.fixture
async def patched_db(test_db, test_user):
    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.agent_crud.get_db", side_effect=_get_db), \
         patch("core.database.get_db", side_effect=_get_db):
        yield test_db, test_user


class TestRosterShape:
    @pytest.mark.asyncio
    async def test_empty_returns_empty_list(self, patched_db):
        _, user = patched_db
        out = await list_agents_for_client(user.id)
        assert out == []

    @pytest.mark.asyncio
    async def test_includes_enabled_with_name_description_source(self, patched_db):
        _, user = patched_db
        await create_agent(
            user_id=user.id, name="trader",
            description="Runs trade analyses.", system_prompt="...",
        )
        out = await list_agents_for_client(user.id)
        assert len(out) == 1
        row = out[0]
        # Frontend depends on these exact keys -- contract pin.
        assert row["name"] == "trader"
        assert row["description"] == "Runs trade analyses."
        assert row["source"] == "user"

    @pytest.mark.asyncio
    async def test_disabled_agents_filtered_out(self, patched_db):
        _, user = patched_db
        a = await create_agent(
            user_id=user.id, name="dormant",
            description="x", system_prompt="x",
        )
        await update_agent(a.id, user.id, enabled=False)
        out = await list_agents_for_client(user.id)
        names = [r["name"] for r in out]
        assert "dormant" not in names

    @pytest.mark.asyncio
    async def test_plugin_namespaced_name_flows_through_unchanged(
        self, patched_db, test_db
    ):
        from core.db_models import AgentDefinition
        _, user = patched_db
        # Plugin agents store the colon literally in name + carry source="plugin:foo".
        a = AgentDefinition(
            id="plug-agent-1",
            user_id=user.id,
            name="acme:reviewer",
            description="Vendor reviewer",
            system_prompt="...",
            source="plugin:acme",
            enabled=True,
        )
        test_db.add(a)
        await test_db.commit()
        out = await list_agents_for_client(user.id)
        names = {r["name"]: r for r in out}
        assert "acme:reviewer" in names
        assert names["acme:reviewer"]["source"] == "plugin:acme"

    @pytest.mark.asyncio
    async def test_db_failure_returns_empty_not_raises(self, patched_db):
        _, user = patched_db
        with patch(
            "core.agent_crud.list_agents",
            side_effect=RuntimeError("db down"),
        ):
            # Best-effort: the WS handshake must not crash on a transient DB hiccup.
            out = await list_agents_for_client(user.id)
        assert out == []


# ── agents_updated wire shape ───────────────────────────────────────────

@pytest_asyncio.fixture
async def chat_db(test_db, test_user):
    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.agent_crud.get_db", side_effect=_get_db), \
         patch("core.database.get_db", side_effect=_get_db):
        yield test_db, test_user


class TestAgentsUpdatedShape:
    @pytest.mark.asyncio
    async def test_disable_emits_agents_payload(self, chat_db):
        from core.session import (
            create_connection, create_conv_session, destroy_connection,
            set_current_session,
        )
        from handlers.agent_handler import handle_user_message

        _, user = chat_db
        sid, cid = "u2-sess-1", "u2-cid-1"
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

            updates = [p for n, p in sent if n == "agents_updated"]
            assert updates, "disable should bump agents_updated"
            payload = updates[0]
            # Frontend reads `data.agents` -- must always be a list.
            assert "agents" in payload
            assert isinstance(payload["agents"], list)
            # The disabled agent should already be filtered out of the bump.
            names = {row["name"] for row in payload["agents"]}
            assert "bumpable" not in names
        finally:
            destroy_connection(sid)
