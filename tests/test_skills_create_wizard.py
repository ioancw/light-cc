"""Tests for W2: ``/skills create`` interactive wizard.

Mirrors ``tests/test_agents_create_wizard.py``. Covers:
  - ``write_skill_def`` round-trip + defaults omission + extras pass-through.
  - ``set_skill_enabled`` flips both ``user-invocable`` and
    ``disable-model-invocation`` on disk.
  - Wizard state machine: name validation, optional skip, ``back`` rewinds,
    ``cancel`` clears state, full happy path writes file + reloads registry.
  - Chat wiring: ``/skills create`` typed in chat starts the wizard, the
    next message is routed to the wizard (not the model), confirm fires
    ``skills_updated``.
  - Overwrite safety: a name that already has a SKILL.md prompts
    ``yes/no`` instead of silently stomping the file.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import yaml

from core.models import SkillDef
from skills import registry
from skills.loader import (
    parse_skill_file,
    set_skill_enabled,
    write_skill_def,
)
from handlers.skills_wizard import (
    cancel_wizard,
    handle_wizard_input,
    is_wizard_active,
    start_wizard,
)


# ── write_skill_def round-trip ───────────────────────────────────────────

class TestWriteSkillDef:
    def test_round_trip_minimal(self, tmp_path: Path):
        s = SkillDef(name="researcher", description="Research things.", prompt="Body.")
        path = write_skill_def(s, tmp_path)
        assert path.exists()
        assert path.name == "SKILL.md"
        assert path.parent.name == "researcher"

        parsed = parse_skill_file(path)
        assert parsed.name == "researcher"
        assert parsed.description == "Research things."
        assert parsed.prompt == "Body."
        # Defaults omitted -- keep file clean.
        text = path.read_text(encoding="utf-8")
        assert "user-invocable" not in text
        assert "disable-model-invocation" not in text

    def test_round_trip_with_overrides(self, tmp_path: Path):
        s = SkillDef(
            name="trader",
            description="Trade.",
            prompt="You trade.",
            argument_hint="[ticker]",
            tools=["WebSearch", "WebFetch"],
            user_invocable=False,
            disable_model_invocation=True,
            context="fork",
        )
        path = write_skill_def(s, tmp_path)
        text = path.read_text(encoding="utf-8")
        assert "argument-hint: '[ticker]'" in text or "argument-hint: \"[ticker]\"" in text or "[ticker]" in text
        assert "user-invocable: false" in text
        assert "disable-model-invocation: true" in text
        assert "context: fork" in text

        parsed = parse_skill_file(path)
        assert parsed.tools == ["WebSearch", "WebFetch"]
        assert parsed.user_invocable is False
        assert parsed.disable_model_invocation is True
        assert parsed.context == "fork"

    def test_extra_frontmatter_persisted(self, tmp_path: Path):
        s = SkillDef(name="x", description="x", prompt="x")
        path = write_skill_def(
            s, tmp_path, extra_frontmatter={"shell": "bash", "color": "blue"},
        )
        meta = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        assert meta["shell"] == "bash"
        assert meta["color"] == "blue"

    def test_refuses_overwrite_by_default(self, tmp_path: Path):
        s = SkillDef(name="dup", description="x", prompt="x")
        write_skill_def(s, tmp_path)
        with pytest.raises(FileExistsError):
            write_skill_def(s, tmp_path)

    def test_overwrite_replaces_file(self, tmp_path: Path):
        write_skill_def(SkillDef(name="dup", description="v1", prompt="x"), tmp_path)
        path = write_skill_def(
            SkillDef(name="dup", description="v2", prompt="y"), tmp_path,
            overwrite=True,
        )
        parsed = parse_skill_file(path)
        assert parsed.description == "v2"
        assert parsed.prompt == "y"


# ── set_skill_enabled ────────────────────────────────────────────────────

class TestSetSkillEnabled:
    def test_disable_then_enable_round_trip(self, tmp_path: Path):
        s = SkillDef(name="x", description="x", prompt="body")
        path = write_skill_def(s, tmp_path)

        set_skill_enabled(path, False)
        parsed = parse_skill_file(path)
        assert parsed.user_invocable is False
        assert parsed.disable_model_invocation is True

        set_skill_enabled(path, True)
        parsed = parse_skill_file(path)
        # Re-enable removes both keys -> defaults restored.
        assert parsed.user_invocable is True
        assert parsed.disable_model_invocation is False

    def test_disable_preserves_other_frontmatter(self, tmp_path: Path):
        s = SkillDef(
            name="x", description="d", prompt="b",
            argument_hint="[a]", tools=["Read"], context="fork",
        )
        path = write_skill_def(s, tmp_path)
        set_skill_enabled(path, False)
        parsed = parse_skill_file(path)
        assert parsed.description == "d"
        assert parsed.argument_hint == "[a]"
        assert parsed.tools == ["Read"]
        assert parsed.context == "fork"


# ── Wizard state machine ─────────────────────────────────────────────────

@pytest_asyncio.fixture
async def wizard_session(tmp_path):
    """Real WS connection so the wizard can persist state."""
    from core.session import create_connection, destroy_connection

    sid = "skill-wiz-1"
    create_connection(sid, user_id="user-x")

    # Snapshot the registry; the wizard's reload_skills mutates it.
    snap = dict(registry._SKILLS)
    sdirs = list(registry._skills_dirs)
    cdirs = list(registry._commands_dirs_as_skills)
    try:
        yield sid, "user-x"
    finally:
        destroy_connection(sid)
        registry._SKILLS.clear()
        registry._SKILLS.update(snap)
        registry._skills_dirs.clear()
        registry._skills_dirs.extend(sdirs)
        registry._commands_dirs_as_skills.clear()
        registry._commands_dirs_as_skills.extend(cdirs)


class TestWizardStateMachine:
    def test_start_without_hint_asks_for_name(self, wizard_session):
        sid, user_id = wizard_session
        first = start_wizard(sid, user_id)
        assert is_wizard_active(sid)
        assert "name" in first.lower()
        assert "Step 1" in first

    def test_start_with_valid_hint_skips_name(self, wizard_session):
        sid, user_id = wizard_session
        first = start_wizard(sid, user_id, name_hint="my-skill")
        assert "Step 2" in first

    def test_start_with_invalid_hint_falls_back(self, wizard_session):
        sid, user_id = wizard_session
        first = start_wizard(sid, user_id, name_hint="Bad Name!")
        assert "Step 1" in first

    @pytest.mark.asyncio
    async def test_cancel_clears_state(self, wizard_session, tmp_path):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        out = await handle_wizard_input(sid, user_id, "cancel", tmp_path)
        assert out.finished is True
        assert "cancel" in out.text.lower()
        assert not is_wizard_active(sid)

    @pytest.mark.asyncio
    async def test_invalid_name_re_asks(self, wizard_session, tmp_path):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        out = await handle_wizard_input(sid, user_id, "Bad Name", tmp_path)
        assert is_wizard_active(sid)
        assert "kebab" in out.text.lower()

    @pytest.mark.asyncio
    async def test_back_rewinds(self, wizard_session, tmp_path):
        sid, user_id = wizard_session
        start_wizard(sid, user_id)
        await handle_wizard_input(sid, user_id, "good-name", tmp_path)
        out = await handle_wizard_input(sid, user_id, "back", tmp_path)
        assert "Step 1" in out.text

    @pytest.mark.asyncio
    async def test_unknown_context_re_asks(self, wizard_session, tmp_path):
        sid, user_id = wizard_session
        start_wizard(sid, user_id, name_hint="ctx-test")
        await handle_wizard_input(sid, user_id, "Test.", tmp_path)
        # Skip steps 3-6 to land on context (step 7).
        for _ in range(4):
            await handle_wizard_input(sid, user_id, "skip", tmp_path)
        out = await handle_wizard_input(sid, user_id, "weirdmode", tmp_path)
        assert "fork" in out.text


# ── Full happy path ──────────────────────────────────────────────────────

class TestWizardHappyPath:
    @pytest.mark.asyncio
    async def test_full_wizard_creates_skill(self, wizard_session, tmp_path):
        sid, user_id = wizard_session

        with patch("core.config.settings.paths.skills_dirs", ["skills"]):
            start_wizard(sid, user_id, name_hint="autopilot")
            await handle_wizard_input(sid, user_id, "Drives.", tmp_path)
            # Steps 3-7: skip optional fields.
            for _ in range(5):
                await handle_wizard_input(sid, user_id, "skip", tmp_path)
            # Step 8: body.
            out = await handle_wizard_input(sid, user_id, "Skill body.", tmp_path)
            assert "Review" in out.text
            final = await handle_wizard_input(sid, user_id, "confirm", tmp_path)

        assert final.finished is True
        assert final.skills_updated is True
        assert "autopilot" in final.text.lower()
        assert not is_wizard_active(sid)

        target = tmp_path / "skills" / "autopilot" / "SKILL.md"
        assert target.exists()
        parsed = parse_skill_file(target)
        assert parsed.description == "Drives."
        assert parsed.prompt == "Skill body."

        # Loaded into the live registry.
        assert registry.get_skill("autopilot") is not None

    @pytest.mark.asyncio
    async def test_overwrite_prompt_when_file_exists(self, wizard_session, tmp_path):
        sid, user_id = wizard_session
        skills_root = tmp_path / "skills"
        write_skill_def(
            SkillDef(name="dupe", description="old", prompt="old"),
            skills_root,
        )

        with patch("core.config.settings.paths.skills_dirs", ["skills"]):
            start_wizard(sid, user_id, name_hint="dupe")
            await handle_wizard_input(sid, user_id, "new desc", tmp_path)
            for _ in range(5):
                await handle_wizard_input(sid, user_id, "skip", tmp_path)
            await handle_wizard_input(sid, user_id, "new body", tmp_path)
            prompt = await handle_wizard_input(sid, user_id, "confirm", tmp_path)
            assert "already exists" in prompt.text.lower()

            decline = await handle_wizard_input(sid, user_id, "no", tmp_path)
            assert decline.finished is True
            assert not is_wizard_active(sid)

            parsed = parse_skill_file(skills_root / "dupe" / "SKILL.md")
            assert parsed.description == "old"

    @pytest.mark.asyncio
    async def test_overwrite_yes_replaces_file(self, wizard_session, tmp_path):
        sid, user_id = wizard_session
        skills_root = tmp_path / "skills"
        write_skill_def(
            SkillDef(name="dupe2", description="old", prompt="old"),
            skills_root,
        )

        with patch("core.config.settings.paths.skills_dirs", ["skills"]):
            start_wizard(sid, user_id, name_hint="dupe2")
            await handle_wizard_input(sid, user_id, "fresh", tmp_path)
            for _ in range(5):
                await handle_wizard_input(sid, user_id, "skip", tmp_path)
            await handle_wizard_input(sid, user_id, "fresh body", tmp_path)
            await handle_wizard_input(sid, user_id, "confirm", tmp_path)
            await handle_wizard_input(sid, user_id, "yes", tmp_path)

        parsed = parse_skill_file(skills_root / "dupe2" / "SKILL.md")
        assert parsed.description == "fresh"
        assert parsed.prompt == "fresh body"


# ── Chat wiring ──────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def chat_wizard(test_db, test_user):
    from core.session import (
        create_connection, create_conv_session, destroy_connection,
        set_current_session,
    )

    sid, cid = "chat-skill-wiz", "chat-skill-cid"
    create_connection(sid, user_id=test_user.id)
    create_conv_session(cid, sid)
    set_current_session(sid)

    snap = dict(registry._SKILLS)
    sdirs = list(registry._skills_dirs)

    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.database.get_db", side_effect=_get_db):
        try:
            yield sid, cid, test_user
        finally:
            destroy_connection(sid)
            registry._SKILLS.clear()
            registry._SKILLS.update(snap)
            registry._skills_dirs.clear()
            registry._skills_dirs.extend(sdirs)


class TestChatWiring:
    @pytest.mark.asyncio
    async def test_slash_skills_create_starts_wizard_in_chat(
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
                {"text": "/skills create my-skill"},
                send_event,
                build_system_prompt=lambda *a, **kw: "ignored",
                outputs_dir=None,
            )

        assert is_wizard_active(sid)
        text_payload = "".join(p["text"] for n, p in sent if n == "text_delta")
        assert "Step 2" in text_payload
        assert "turn_complete" in [n for n, _ in sent]

    @pytest.mark.asyncio
    async def test_followup_message_routes_to_wizard(self, chat_wizard, tmp_path):
        from handlers.agent_handler import handle_user_message

        sid, cid, user = chat_wizard
        start_wizard(sid, user.id, name_hint="follow-skill")

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
                {"text": "A friendly skill."},
                send_event,
                build_system_prompt=lambda *a, **kw: "ignored",
                outputs_dir=None,
            )

        text_payload = "".join(p["text"] for n, p in sent if n == "text_delta")
        assert "Step 3" in text_payload
        # Wizard turn must NOT pollute the conversation log.
        from core.session import conv_session_get
        msgs = conv_session_get(cid, "messages") or []
        assert all(m.get("content") != "A friendly skill." for m in msgs)

    @pytest.mark.asyncio
    async def test_cancel_in_chat_clears_wizard(self, chat_wizard, tmp_path):
        from handlers.agent_handler import handle_user_message

        sid, cid, user = chat_wizard
        start_wizard(sid, user.id, name_hint="cancel-skill")

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
