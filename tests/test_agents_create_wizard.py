"""Tests for W1: ``/agents create`` interactive wizard.

Covers:
  - ``write_agent_def`` round-trip: writes valid AGENT.md that
    ``parse_agent_file`` can re-parse, frontmatter key order is stable,
    optional CC pass-through fields are persisted, defaults are omitted.
  - Wizard state machine: name validation, optional skip, ``back`` rewinds,
    ``cancel`` clears state, full happy path writes file + DB row.
  - Chat wiring: ``/agents create`` typed in chat starts the wizard, the
    next message is routed to the wizard (not the model), and on confirm
    the frontend receives an ``agents_updated`` bump.
  - Overwrite safety: a name that already has an AGENT.md prompts
    ``yes/no`` instead of silently stomping the file.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import yaml

from core.agent_crud import get_agent_by_name
from core.agent_loader import AgentDef, parse_agent_file, write_agent_def
from handlers.agents_wizard import (
    AgentWizardState,
    cancel_wizard,
    handle_wizard_input,
    is_wizard_active,
    start_wizard,
)


# ── write_agent_def round-trip ───────────────────────────────────────────

class TestWriteAgentDef:
    def test_round_trip_minimal(self, tmp_path: Path):
        d = AgentDef(
            name="researcher",
            description="Research things.",
            system_prompt="You are a researcher.",
        )
        path = write_agent_def(d, tmp_path)
        assert path.exists()
        assert path.name == "AGENT.md"
        assert path.parent.name == "researcher"

        parsed = parse_agent_file(path)
        assert parsed is not None
        assert parsed.name == "researcher"
        assert parsed.description == "Research things."
        assert parsed.system_prompt == "You are a researcher."
        # Defaults: NOT serialised (file should be clean).
        text = path.read_text(encoding="utf-8")
        assert "max-turns" not in text
        assert "timeout" not in text
        assert "memory-scope" not in text
        assert "enabled" not in text

    def test_round_trip_with_overrides(self, tmp_path: Path):
        d = AgentDef(
            name="trader",
            description="Trade.",
            system_prompt="You trade.",
            model="claude-opus-4-7",
            tools=["WebSearch", "WebFetch"],
            skills=["analyze"],
            max_turns=50,
            timeout_seconds=600,
            memory_scope="agent",
            enabled=False,
        )
        path = write_agent_def(d, tmp_path)
        text = path.read_text(encoding="utf-8")
        # Overrides ARE serialised.
        assert "model: claude-opus-4-7" in text
        assert "max-turns: 50" in text
        assert "timeout: 600" in text
        assert "memory-scope: agent" in text
        assert "enabled: false" in text

        parsed = parse_agent_file(path)
        assert parsed is not None
        assert parsed.tools == ["WebSearch", "WebFetch"]
        assert parsed.skills == ["analyze"]
        assert parsed.max_turns == 50
        assert parsed.timeout_seconds == 600
        assert parsed.memory_scope == "agent"
        assert parsed.enabled is False

    def test_extra_frontmatter_persisted_verbatim(self, tmp_path: Path):
        """CC pass-through fields are written even if Light CC ignores them."""
        d = AgentDef(name="x", description="x", system_prompt="x")
        path = write_agent_def(
            d, tmp_path,
            extra_frontmatter={
                "permissionMode": "acceptEdits",
                "isolation": "worktree",
                "background": True,
                "color": "blue",
                "disallowedTools": ["Bash"],
                "mcpServers": ["filesystem"],
                "initialPrompt": "Be brief.",
            },
        )
        meta = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        assert meta["permissionMode"] == "acceptEdits"
        assert meta["isolation"] == "worktree"
        assert meta["background"] is True
        assert meta["color"] == "blue"
        assert meta["disallowedTools"] == ["Bash"]
        assert meta["mcpServers"] == ["filesystem"]
        assert meta["initialPrompt"] == "Be brief."

    def test_empty_extras_are_dropped(self, tmp_path: Path):
        d = AgentDef(name="x", description="x", system_prompt="x")
        path = write_agent_def(
            d, tmp_path,
            extra_frontmatter={
                "permissionMode": "",
                "color": None,
                "disallowedTools": [],
                "mcpServers": {},
                "background": False,  # falsy but explicit -> kept? No: filter drops {} and [] only
            },
        )
        text = path.read_text(encoding="utf-8")
        assert "permissionMode" not in text
        assert "color" not in text
        assert "disallowedTools" not in text
        assert "mcpServers" not in text

    def test_refuses_overwrite_by_default(self, tmp_path: Path):
        d = AgentDef(name="dup", description="x", system_prompt="x")
        write_agent_def(d, tmp_path)
        with pytest.raises(FileExistsError):
            write_agent_def(d, tmp_path)

    def test_overwrite_flag_replaces_file(self, tmp_path: Path):
        first = AgentDef(name="dup", description="v1", system_prompt="x")
        write_agent_def(first, tmp_path)
        second = AgentDef(name="dup", description="v2", system_prompt="y")
        path = write_agent_def(second, tmp_path, overwrite=True)
        parsed = parse_agent_file(path)
        assert parsed is not None
        assert parsed.description == "v2"
        assert parsed.system_prompt == "y"

    def test_frontmatter_key_order_is_stable(self, tmp_path: Path):
        """Diff hygiene: extras land after the canonical keys."""
        d = AgentDef(
            name="ordered",
            description="d",
            system_prompt="x",
            model="m",
            tools=["t"],
        )
        path = write_agent_def(
            d, tmp_path,
            extra_frontmatter={"color": "red", "permissionMode": "plan"},
        )
        text = path.read_text(encoding="utf-8")
        # Strip the leading ``---\n`` so ``find()`` indexes line-by-line cleanly.
        head = text.split("---\n", 2)[1]
        order = [head.find(f"\n{k}:") if i else head.find(f"{k}:")
                 for i, k in enumerate(("name", "description", "model", "tools",
                                         "permissionMode", "color"))]
        assert all(o >= 0 for o in order), f"missing keys in {head!r}"
        assert order == sorted(order), f"keys out of order: {order}"


# ── Wizard state machine ─────────────────────────────────────────────────

@pytest_asyncio.fixture
async def wizard_session(test_db, test_user):
    """Real session + DB patches so the wizard can persist state and rows."""
    from core.session import create_connection, destroy_connection

    sid = "wiz-sess-1"
    create_connection(sid, user_id=test_user.id)

    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.agent_loader.get_db", side_effect=_get_db, create=True), \
         patch("core.database.get_db", side_effect=_get_db), \
         patch("core.agent_crud.get_db", side_effect=_get_db):
        try:
            yield sid, test_user
        finally:
            destroy_connection(sid)


class TestWizardStateMachine:
    def test_start_without_hint_asks_for_name(self, wizard_session):
        sid, user = wizard_session
        first = start_wizard(sid, user.id)
        assert is_wizard_active(sid)
        assert "name" in first.lower()
        assert "Step 1" in first

    def test_start_with_valid_hint_skips_name(self, wizard_session):
        sid, user = wizard_session
        first = start_wizard(sid, user.id, name_hint="my-agent")
        # Should land on Step 2 (description) directly.
        assert "Step 2" in first
        assert "description" in first.lower()

    def test_start_with_invalid_hint_falls_back_to_step_1(self, wizard_session):
        sid, user = wizard_session
        first = start_wizard(sid, user.id, name_hint="Bad Name!")
        assert "Step 1" in first

    @pytest.mark.asyncio
    async def test_cancel_clears_state(self, wizard_session, tmp_path):
        sid, user = wizard_session
        start_wizard(sid, user.id)
        out = await handle_wizard_input(sid, user.id, "cancel", tmp_path)
        assert out.finished is True
        assert "cancel" in out.text.lower()
        assert not is_wizard_active(sid)

    @pytest.mark.asyncio
    async def test_invalid_name_re_asks(self, wizard_session, tmp_path):
        sid, user = wizard_session
        start_wizard(sid, user.id)
        out = await handle_wizard_input(sid, user.id, "Not Kebab!", tmp_path)
        # Wizard re-asks; should still be active.
        assert is_wizard_active(sid)
        assert "kebab" in out.text.lower()

    @pytest.mark.asyncio
    async def test_back_rewinds_one_step(self, wizard_session, tmp_path):
        sid, user = wizard_session
        start_wizard(sid, user.id)
        await handle_wizard_input(sid, user.id, "good-name", tmp_path)  # -> step 2
        out = await handle_wizard_input(sid, user.id, "back", tmp_path)
        assert "Step 1" in out.text

    @pytest.mark.asyncio
    async def test_optional_step_accepts_skip(self, wizard_session, tmp_path):
        sid, user = wizard_session
        start_wizard(sid, user.id)
        await handle_wizard_input(sid, user.id, "good-name", tmp_path)
        await handle_wizard_input(sid, user.id, "Test agent.", tmp_path)
        # Now on Step 3 (model) which is optional.
        out = await handle_wizard_input(sid, user.id, "skip", tmp_path)
        assert "Step 4" in out.text  # advanced past model

    @pytest.mark.asyncio
    async def test_unknown_permission_mode_re_asks(self, wizard_session, tmp_path):
        sid, user = wizard_session
        start_wizard(sid, user.id, name_hint="perm-test")
        await handle_wizard_input(sid, user.id, "Test.", tmp_path)
        # skip through to permission mode (steps 3-7)
        for _ in range(5):
            await handle_wizard_input(sid, user.id, "skip", tmp_path)
        out = await handle_wizard_input(sid, user.id, "noSuchMode", tmp_path)
        assert "default" in out.text  # error lists valid modes
        assert "acceptEdits" in out.text


# ── Full happy path: confirm writes file + syncs DB ──────────────────────

class TestWizardHappyPath:
    @pytest.mark.asyncio
    async def test_full_wizard_creates_agent(self, wizard_session, tmp_path):
        sid, user = wizard_session
        # Use tmp_path as project root so files land somewhere safe.
        agents_dir = tmp_path / "agents"

        with patch(
            "core.config.settings.paths.agents_dirs",
            ["agents"],
        ):
            start_wizard(sid, user.id, name_hint="autopilot")
            # Step 2: description
            await handle_wizard_input(sid, user.id, "Drives the car.", tmp_path)
            # Steps 3-12: skip optional fields
            for _ in range(10):
                await handle_wizard_input(sid, user.id, "skip", tmp_path)
            # Step 13: system prompt
            out = await handle_wizard_input(
                sid, user.id, "You are an autopilot agent.", tmp_path,
            )
            assert "Review" in out.text
            # Confirm
            final = await handle_wizard_input(sid, user.id, "confirm", tmp_path)

        assert final.finished is True
        assert final.agents_updated is True
        assert "autopilot" in final.text.lower()
        assert not is_wizard_active(sid)

        # File on disk
        target = agents_dir / "autopilot" / "AGENT.md"
        assert target.exists()
        parsed = parse_agent_file(target)
        assert parsed is not None
        assert parsed.description == "Drives the car."
        assert parsed.system_prompt == "You are an autopilot agent."

        # DB row
        row = await get_agent_by_name("autopilot", user.id)
        assert row is not None
        assert row.source == "user"

    @pytest.mark.asyncio
    async def test_overwrite_prompt_when_file_exists(self, wizard_session, tmp_path):
        sid, user = wizard_session
        agents_dir = tmp_path / "agents"
        # Pre-create an existing AGENT.md
        existing = AgentDef(name="dupe", description="old", system_prompt="old body")
        write_agent_def(existing, agents_dir)

        with patch(
            "core.config.settings.paths.agents_dirs",
            ["agents"],
        ):
            start_wizard(sid, user.id, name_hint="dupe")
            await handle_wizard_input(sid, user.id, "new desc", tmp_path)
            for _ in range(10):
                await handle_wizard_input(sid, user.id, "skip", tmp_path)
            await handle_wizard_input(sid, user.id, "new body", tmp_path)
            prompt = await handle_wizard_input(sid, user.id, "confirm", tmp_path)
            assert "already exists" in prompt.text.lower()
            assert "yes" in prompt.text.lower()

            # Decline overwrite
            decline = await handle_wizard_input(sid, user.id, "no", tmp_path)
            assert decline.finished is True
            assert not is_wizard_active(sid)

            # File should still hold the old content
            parsed = parse_agent_file(agents_dir / "dupe" / "AGENT.md")
            assert parsed.description == "old"

    @pytest.mark.asyncio
    async def test_overwrite_yes_replaces_file(self, wizard_session, tmp_path):
        sid, user = wizard_session
        agents_dir = tmp_path / "agents"
        write_agent_def(
            AgentDef(name="dupe2", description="old", system_prompt="old"),
            agents_dir,
        )

        with patch("core.config.settings.paths.agents_dirs", ["agents"]):
            start_wizard(sid, user.id, name_hint="dupe2")
            await handle_wizard_input(sid, user.id, "fresh", tmp_path)
            for _ in range(10):
                await handle_wizard_input(sid, user.id, "skip", tmp_path)
            await handle_wizard_input(sid, user.id, "fresh body", tmp_path)
            await handle_wizard_input(sid, user.id, "confirm", tmp_path)
            await handle_wizard_input(sid, user.id, "yes", tmp_path)

        parsed = parse_agent_file(agents_dir / "dupe2" / "AGENT.md")
        assert parsed.description == "fresh"
        assert parsed.system_prompt == "fresh body"


# ── Chat wiring ──────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def chat_wizard(test_db, test_user):
    from core.session import (
        create_connection, create_conv_session, destroy_connection,
        set_current_session,
    )

    sid, cid = "chat-wiz-1", "chat-wiz-cid-1"
    create_connection(sid, user_id=test_user.id)
    create_conv_session(cid, sid)
    set_current_session(sid)

    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.database.get_db", side_effect=_get_db), \
         patch("core.agent_crud.get_db", side_effect=_get_db):
        try:
            yield sid, cid, test_user
        finally:
            destroy_connection(sid)


class TestChatWiring:
    @pytest.mark.asyncio
    async def test_slash_agents_create_starts_wizard_in_chat(
        self, chat_wizard, tmp_path,
    ):
        from handlers.agent_handler import handle_user_message

        sid, cid, user = chat_wizard
        sent: list[tuple[str, dict]] = []

        async def send_event(name, payload):
            sent.append((name, payload))

        with patch(
            "handlers.agent_handler.save_conversation",
            new=AsyncMock(return_value="conv-x"),
        ):
            await handle_user_message(
                sid, cid,
                {"text": "/agents create my-bot"},
                send_event,
                build_system_prompt=lambda *a, **kw: "ignored",
                outputs_dir=None,
            )

        # Wizard should now be active and the first prompt should be in chat.
        assert is_wizard_active(sid)
        text_payload = "".join(p["text"] for n, p in sent if n == "text_delta")
        assert "Step 2" in text_payload  # name was hinted -> jumped to description
        assert "turn_complete" in [n for n, _ in sent]

    @pytest.mark.asyncio
    async def test_followup_message_routes_to_wizard(self, chat_wizard, tmp_path):
        from handlers.agent_handler import handle_user_message

        sid, cid, user = chat_wizard

        # Seed an in-progress wizard.
        start_wizard(sid, user.id, name_hint="follow-bot")

        sent: list[tuple[str, dict]] = []

        async def send_event(name, payload):
            sent.append((name, payload))

        with patch(
            "handlers.agent_handler.save_conversation",
            new=AsyncMock(return_value="conv-x"),
        ), patch(
            "core.config.settings.project_dir", str(tmp_path),
        ):
            await handle_user_message(
                sid, cid,
                {"text": "A friendly bot."},
                send_event,
                build_system_prompt=lambda *a, **kw: "ignored",
                outputs_dir=None,
            )

        # Wizard should have advanced to Step 3 (model).
        text_payload = "".join(p["text"] for n, p in sent if n == "text_delta")
        assert "Step 3" in text_payload
        # Wizard turn must NOT pollute the conversation message log.
        from core.session import conv_session_get
        msgs = conv_session_get(cid, "messages") or []
        assert all(m.get("content") != "A friendly bot." for m in msgs), (
            "wizard inputs should bypass conversation persistence"
        )

    @pytest.mark.asyncio
    async def test_cancel_in_chat_clears_wizard(self, chat_wizard, tmp_path):
        from handlers.agent_handler import handle_user_message

        sid, cid, user = chat_wizard
        start_wizard(sid, user.id, name_hint="cancel-bot")

        sent: list[tuple[str, dict]] = []

        async def send_event(name, payload):
            sent.append((name, payload))

        with patch(
            "handlers.agent_handler.save_conversation",
            new=AsyncMock(return_value="conv-x"),
        ), patch(
            "core.config.settings.project_dir", str(tmp_path),
        ):
            await handle_user_message(
                sid, cid,
                {"text": "cancel"},
                send_event,
                build_system_prompt=lambda *a, **kw: "ignored",
                outputs_dir=None,
            )

        assert not is_wizard_active(sid)
        text_payload = "".join(p["text"] for n, p in sent if n == "text_delta")
        assert "cancel" in text_payload.lower()
