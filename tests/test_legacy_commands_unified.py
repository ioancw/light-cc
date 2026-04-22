"""Tests for legacy ``commands/*.md`` loaded into the unified skills registry.

CC 2.1+ collapses the old "commands" category into the skills registry --
``/foo`` dispatches against a single dict, with a ``kind`` field
distinguishing real ``SKILL.md`` skills from legacy command files.

Covers (F3 of the parity plan):
  - ``parse_command_as_skill`` defaults: disable_model_invocation=True,
    user_invocable=True, kind="legacy-command", description synthesized
    from the first body line when frontmatter omits it
  - ``load_commands_as_skills`` registers under the unified registry and
    is re-scanned by ``reload_skills``
  - ``commands.registry`` shim: ``get_command`` / ``list_commands`` only
    surface the legacy-command subset, not real skills
  - Plugin commands (``plugins/<name>/commands/``) are namespaced and
    cleanly removed on plugin unload
  - The ``/`` resolver in ``handlers.agent_handler`` activates legacy
    commands via the same ``match_skill_by_name`` path
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from commands.registry import get_command, list_commands, load_commands
from skills.registry import (
    _SKILLS,
    _commands_dirs_as_skills,
    _skills_dirs,
    discover_commands_as_skills,
    load_commands_as_skills,
    load_skills,
    match_skill_by_intent,
    match_skill_by_name,
    parse_command_as_skill,
    reload_skills,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Snapshot + restore the global registries around each test."""
    skills_snap = dict(_SKILLS)
    skills_dirs_snap = list(_skills_dirs)
    cmd_dirs_snap = list(_commands_dirs_as_skills)
    _SKILLS.clear()
    _skills_dirs.clear()
    _commands_dirs_as_skills.clear()
    yield
    _SKILLS.clear()
    _SKILLS.update(skills_snap)
    _skills_dirs.clear()
    _skills_dirs.extend(skills_dirs_snap)
    _commands_dirs_as_skills.clear()
    _commands_dirs_as_skills.extend(cmd_dirs_snap)


# ── parse_command_as_skill ──────────────────────────────────────────────

class TestParseCommandAsSkill:
    def test_no_frontmatter_uses_filename_and_first_line_description(self, tmp_path: Path):
        f = tmp_path / "remind.md"
        f.write_text("# Remind me about something\n\nThe body of the command.\n")
        skill = parse_command_as_skill(f)
        assert skill is not None
        assert skill.name == "remind"
        # First non-empty line, with leading '#' stripped
        assert skill.description == "Remind me about something"
        assert skill.kind == "legacy-command"
        # Legacy default: user-only, no model auto-invoke
        assert skill.user_invocable is True
        assert skill.disable_model_invocation is True

    def test_frontmatter_overrides_defaults(self, tmp_path: Path):
        f = tmp_path / "release.md"
        f.write_text(
            "---\n"
            "name: release\n"
            "description: Cut a release\n"
            "argument-hint: <version>\n"
            "disable-model-invocation: false\n"
            "user-invocable: true\n"
            "---\n"
            "Release procedure for $ARGUMENTS.\n"
        )
        skill = parse_command_as_skill(f)
        assert skill is not None
        assert skill.name == "release"
        assert skill.description == "Cut a release"
        assert skill.argument_hint == "<version>"
        assert skill.disable_model_invocation is False
        assert skill.user_invocable is True
        assert skill.kind == "legacy-command"
        assert "Release procedure for $ARGUMENTS." in skill.prompt

    def test_synthesized_description_strips_leading_hashes(self, tmp_path: Path):
        f = tmp_path / "x.md"
        f.write_text("### A heading line\n\nbody\n")
        skill = parse_command_as_skill(f)
        assert skill and skill.description == "A heading line"


# ── load_commands_as_skills + reload_skills ─────────────────────────────

class TestUnifiedLoading:
    def test_load_commands_registers_in_unified_registry(self, tmp_path: Path):
        cmd_dir = tmp_path / "commands"
        cmd_dir.mkdir()
        (cmd_dir / "alpha.md").write_text("Alpha body\n")
        (cmd_dir / "beta.md").write_text("---\nname: beta\ndescription: Beta cmd\n---\nbody\n")

        load_commands_as_skills(cmd_dir)

        assert "alpha" in _SKILLS
        assert "beta" in _SKILLS
        assert _SKILLS["alpha"].kind == "legacy-command"
        assert _SKILLS["beta"].kind == "legacy-command"

        # match_skill_by_name resolves both -- this is the unified-resolver
        # contract that lets `/alpha` work without a separate command path.
        assert match_skill_by_name("alpha") is _SKILLS["alpha"]
        assert match_skill_by_name("beta") is _SKILLS["beta"]

    def test_reload_skills_rescans_command_dirs(self, tmp_path: Path):
        cmd_dir = tmp_path / "commands"
        cmd_dir.mkdir()
        (cmd_dir / "first.md").write_text("First\n")
        load_commands_as_skills(cmd_dir)
        assert "first" in _SKILLS

        # Add a new command file and reload
        (cmd_dir / "second.md").write_text("Second\n")
        n = reload_skills()
        assert "first" in _SKILLS
        assert "second" in _SKILLS
        assert n >= 2

    def test_legacy_commands_excluded_from_intent_matching(self, tmp_path: Path):
        """Legacy commands default to ``disable_model_invocation=True`` -- the
        intent matcher must skip them, even if the user message matches their
        name keywords."""
        cmd_dir = tmp_path / "commands"
        cmd_dir.mkdir()
        (cmd_dir / "deploy.md").write_text(
            "---\nname: deploy\ndescription: deploy the service\n---\nbody\n"
        )
        load_commands_as_skills(cmd_dir)

        assert match_skill_by_intent("please deploy the service now") is None


# ── commands.registry compat shim ───────────────────────────────────────

class TestCompatShim:
    def test_shim_load_commands_delegates(self, tmp_path: Path):
        cmd_dir = tmp_path / "commands"
        cmd_dir.mkdir()
        (cmd_dir / "ping.md").write_text("ping body\n")
        load_commands(cmd_dir)
        assert _SKILLS["ping"].kind == "legacy-command"

    def test_get_command_returns_only_legacy(self, tmp_path: Path):
        # Real skill (kind="skill")
        skills_dir = tmp_path / "skills" / "real-one"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: real-one\ndescription: a real skill\n---\nbody\n"
        )
        load_skills(tmp_path / "skills")

        # Legacy command (kind="legacy-command")
        cmd_dir = tmp_path / "commands"
        cmd_dir.mkdir()
        (cmd_dir / "legacy-one.md").write_text("legacy body\n")
        load_commands(cmd_dir)

        assert get_command("real-one") is None  # not legacy
        assert get_command("legacy-one") is not None
        names = {c.name for c in list_commands()}
        assert names == {"legacy-one"}


# ── plugin loader uses unified registry ─────────────────────────────────

class TestPluginIntegration:
    @pytest.mark.asyncio
    async def test_plugin_command_namespaced_and_unloaded(self, tmp_path: Path):
        from core.plugin_loader import PluginLoader

        plugin = tmp_path / "myplug"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text(
            '{"name": "myplug", "version": "0.1.0", "description": "x"}'
        )
        (plugin / "commands").mkdir()
        (plugin / "commands" / "do-thing.md").write_text(
            "---\ndescription: Do a thing\n---\nbody\n"
        )

        loader = PluginLoader()
        info = await loader.load_plugin(plugin)
        assert info is not None
        assert "myplug:do-thing" in info.commands
        # Stored in the unified skills registry, kind=legacy-command
        assert "myplug:do-thing" in _SKILLS
        assert _SKILLS["myplug:do-thing"].kind == "legacy-command"

        # Visible to the unified resolver
        assert match_skill_by_name("myplug:do-thing") is not None

        await loader.unload_plugin("myplug")
        assert "myplug:do-thing" not in _SKILLS


# ── handler resolver uses unified registry ──────────────────────────────

@pytest_asyncio.fixture
async def chat_db(test_db, test_user):
    @asynccontextmanager
    async def _get_db():
        yield test_db

    with patch("core.agent_crud.get_db", side_effect=_get_db), \
         patch("core.database.get_db", side_effect=_get_db), \
         patch("core.agent_runner.get_db", side_effect=_get_db):
        yield test_db, test_user


class TestHandlerResolver:
    @pytest.mark.asyncio
    async def test_slash_dispatch_activates_legacy_command_as_skill(
        self, chat_db, mock_anthropic_client, tmp_path: Path
    ):
        """A registered legacy command should resolve via ``/name``, fire the
        ``skill_activated`` event with ``type="command"``, and run through
        the normal agent loop."""
        from core.session import (
            create_connection, create_conv_session, destroy_connection,
            set_current_session,
        )
        from handlers.agent_handler import handle_user_message
        from tests.conftest import _build_text_events

        cmd_dir = tmp_path / "commands"
        cmd_dir.mkdir()
        (cmd_dir / "ship-it.md").write_text(
            "---\ndescription: Ship the build\n---\nProceed to ship $ARGUMENTS.\n"
        )
        load_commands_as_skills(cmd_dir)

        _, user = chat_db
        sid, cid = "lc-sess-1", "lc-cid-1"
        create_connection(sid, user_id=user.id)
        create_conv_session(cid, sid)
        set_current_session(sid)

        _, set_responses = mock_anthropic_client
        set_responses([_build_text_events("shipped")])

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
                    {"text": "/ship-it v1.2.3"},
                    send_event,
                    build_system_prompt=lambda *a, **kw: "ignored",
                    outputs_dir=None,
                )

            activations = [p for n, p in sent if n == "skill_activated"]
            assert activations, "expected skill_activated event"
            assert activations[0]["name"] == "ship-it"
            # Legacy commands surface as type="command" so the UI can label.
            assert activations[0]["type"] == "command"
        finally:
            destroy_connection(sid)
