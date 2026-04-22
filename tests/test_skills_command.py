"""Tests for the ``/skills`` built-in (W2 of CC parity plan).

Covers:
  - Bare ``/skills`` lists registered skills grouped by source with the
    ``/<name>`` invocation hint per row, and a help footer.
  - ``/skills show <name>`` dumps name, source, tools, body.
  - ``/skills enable`` / ``/skills disable`` flip the on-disk frontmatter
    flags so reload picks them up.
  - Unknown name returns a helpful error.
  - ``/skills create`` returns a chat-pointer message (the wizard runs
    in chat -- this is the direct-call fallback).
  - Chat-side wiring: ``/skills`` typed in chat produces ``text_delta`` +
    ``turn_complete``; ``enable`` / ``disable`` also fire ``skills_updated``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from core.models import SkillDef
from handlers.commands import handle_skills_command
from skills import registry
from skills.loader import write_skill_def


@pytest.fixture
def skills_dir(tmp_path: Path):
    """Empty skills dir loaded into the live registry, restored after."""
    snapshot = dict(registry._SKILLS)
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    registry._SKILLS.clear()
    registry._skills_dirs.clear()
    registry._commands_dirs_as_skills.clear()
    registry._skills_dirs.append(skills_root)
    yield skills_root
    registry._SKILLS.clear()
    registry._SKILLS.update(snapshot)
    registry._skills_dirs.clear()
    registry._commands_dirs_as_skills.clear()


def _make_skill(skills_root: Path, name: str, description: str = "x", **kwargs):
    """Write a SKILL.md and load it into the live registry."""
    s = SkillDef(name=name, description=description, prompt="body", **kwargs)
    write_skill_def(s, skills_root)
    registry.reload_skills()


# ── Direct handler tests ────────────────────────────────────────────────

class TestListing:
    @pytest.mark.asyncio
    async def test_empty_roster_explains_how_to_create(self, skills_dir):
        result = await handle_skills_command("", user_id="u")
        assert "No skills" in result
        assert "/skills create" in result

    @pytest.mark.asyncio
    async def test_lists_skills_with_invocation_hint(self, skills_dir):
        _make_skill(skills_dir, "alpha", description="The alpha one.")
        _make_skill(
            skills_dir, "beta", description="The beta one.",
            argument_hint="<arg>",
        )
        result = await handle_skills_command("", user_id="u")
        assert "alpha" in result
        assert "beta" in result
        assert "/alpha" in result
        assert "/beta" in result
        assert "<arg>" in result
        # Footer documents the subcommands
        assert "show" in result and "enable" in result and "disable" in result

    @pytest.mark.asyncio
    async def test_disabled_skills_marked_in_listing(self, skills_dir):
        _make_skill(skills_dir, "dimmed")
        await handle_skills_command("disable dimmed", user_id="u")
        result = await handle_skills_command("", user_id="u")
        assert "dimmed" in result
        assert "(disabled)" in result


class TestShow:
    @pytest.mark.asyncio
    async def test_show_dumps_full_definition(self, skills_dir):
        _make_skill(
            skills_dir, "trader",
            description="Trade things.",
            argument_hint="[ticker]",
            tools=["WebSearch", "WebFetch"],
        )
        result = await handle_skills_command("show trader", user_id="u")
        assert "**Skill: /trader**" in result
        assert "Trade things." in result
        assert "[ticker]" in result
        assert "WebSearch" in result and "WebFetch" in result
        assert "body" in result

    @pytest.mark.asyncio
    async def test_show_unknown_name_is_helpful(self, skills_dir):
        result = await handle_skills_command("show ghost", user_id="u")
        assert "not found" in result.lower()
        assert "/skills" in result


class TestEnableDisable:
    @pytest.mark.asyncio
    async def test_disable_then_enable_round_trip(self, skills_dir):
        _make_skill(skills_dir, "toggle-me")
        # Pre-condition: created enabled
        s = registry.get_skill("toggle-me")
        assert s.user_invocable is True

        result = await handle_skills_command("disable toggle-me", user_id="u")
        assert "disabled" in result.lower()
        s = registry.get_skill("toggle-me")
        assert s.user_invocable is False
        assert s.disable_model_invocation is True

        result = await handle_skills_command("enable toggle-me", user_id="u")
        assert "enabled" in result.lower()
        s = registry.get_skill("toggle-me")
        assert s.user_invocable is True
        assert s.disable_model_invocation is False

    @pytest.mark.asyncio
    async def test_enable_unknown_returns_error(self, skills_dir):
        result = await handle_skills_command("enable nothing-here", user_id="u")
        assert "not found" in result.lower()


class TestCreateDirectCall:
    @pytest.mark.asyncio
    async def test_create_pointer_when_called_outside_chat(self, skills_dir):
        # Wizard lives in handlers/agent_handler.py because it needs
        # session_id; direct callers (tests, scripts) get a chat pointer.
        result = await handle_skills_command("create trader", user_id="u")
        assert "wizard" in result.lower()
        assert "SKILL.md" in result


class TestHelp:
    @pytest.mark.asyncio
    async def test_unknown_subcommand_prints_help(self, skills_dir):
        result = await handle_skills_command("frobnicate", user_id="u")
        assert "Skill commands" in result
        assert "/skills show" in result
        assert "/skills enable" in result


# ── Chat dispatch wiring ────────────────────────────────────────────────

@pytest_asyncio.fixture
async def chat_session(skills_dir, test_db, test_user):
    from core.session import (
        create_connection, create_conv_session, destroy_connection,
        set_current_session,
    )

    sid, cid = "skills-sess-1", "skills-cid-1"
    create_connection(sid, user_id=test_user.id)
    create_conv_session(cid, sid)
    set_current_session(sid)

    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.database.get_db", side_effect=_get_db):
        try:
            yield sid, cid, test_user, skills_dir
        finally:
            destroy_connection(sid)


class TestChatWiring:
    @pytest.mark.asyncio
    async def test_slash_skills_in_chat_emits_text_and_complete(self, chat_session):
        from handlers.agent_handler import handle_user_message

        sid, cid, user, _ = chat_session
        _make_skill(_, "visible-one") if False else None
        # Use the real registry seeded by the fixture: add one skill so the
        # listing is non-empty.
        registry._SKILLS.clear()
        registry._SKILLS["visible-one"] = SkillDef(
            name="visible-one", description="visible", prompt="x",
        )

        sent: list[tuple[str, dict]] = []

        async def send_event(name, payload):
            sent.append((name, payload))

        with patch(
            "handlers.agent_handler.save_conversation",
            new=AsyncMock(return_value="conv-x"),
        ):
            await handle_user_message(
                sid, cid,
                {"text": "/skills"},
                send_event,
                build_system_prompt=lambda *a, **kw: "ignored",
                outputs_dir=None,
            )

        event_names = [n for n, _ in sent]
        assert "text_delta" in event_names
        assert "turn_complete" in event_names
        text_payload = "".join(p["text"] for n, p in sent if n == "text_delta")
        assert "visible-one" in text_payload
        # Bare list shouldn't trigger a skills_updated bump.
        assert "skills_updated" not in event_names

    @pytest.mark.asyncio
    async def test_slash_skills_disable_fires_skills_updated(self, chat_session):
        from handlers.agent_handler import handle_user_message

        sid, cid, user, sroot = chat_session
        _make_skill(sroot, "bumpable")

        sent: list[tuple[str, dict]] = []

        async def send_event(name, payload):
            sent.append((name, payload))

        with patch(
            "handlers.agent_handler.save_conversation",
            new=AsyncMock(return_value="conv-x"),
        ):
            await handle_user_message(
                sid, cid,
                {"text": "/skills disable bumpable"},
                send_event,
                build_system_prompt=lambda *a, **kw: "ignored",
                outputs_dir=None,
            )

        event_names = [n for n, _ in sent]
        assert "skills_updated" in event_names, (
            "frontend slash picker should refresh after enable/disable"
        )
