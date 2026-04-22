"""Tests for plugin loading (core/plugin_loader.py)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.plugin_loader import PluginInfo, PluginLoader


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    """Create a minimal plugin directory structure."""
    plugin = tmp_path / "test-plugin"
    plugin.mkdir()

    # Plugin manifest
    manifest_dir = plugin / ".claude-plugin"
    manifest_dir.mkdir()
    (manifest_dir / "plugin.json").write_text(json.dumps({
        "name": "test-plugin",
        "version": "1.0.0",
        "description": "A test plugin",
        "author": "Test Author",
    }))

    return plugin


@pytest.fixture
def plugin_dir_with_skills(plugin_dir: Path) -> Path:
    """Plugin directory with a skill."""
    skills_dir = plugin_dir / "skills" / "my-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\n"
        "name: my-skill\n"
        "description: A test skill\n"
        "---\n"
        "Do the thing with $ARGUMENTS\n"
    )
    return plugin_dir


@pytest.fixture
def plugin_dir_with_commands(plugin_dir: Path) -> Path:
    """Plugin directory with a command."""
    commands_dir = plugin_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "my-cmd.md").write_text(
        "---\n"
        "description: A test command\n"
        "---\n"
        "Run the command with $ARGUMENTS\n"
    )
    return plugin_dir


class TestPluginManifestParsing:
    @pytest.mark.asyncio
    async def test_load_valid_plugin(self, plugin_dir):
        loader = PluginLoader()
        info = await loader.load_plugin(plugin_dir)

        assert info is not None
        assert info.name == "test-plugin"
        assert info.version == "1.0.0"
        assert info.description == "A test plugin"
        assert info.author == "Test Author"

    @pytest.mark.asyncio
    async def test_no_manifest_returns_none(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        loader = PluginLoader()
        info = await loader.load_plugin(empty_dir)
        assert info is None

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self, tmp_path):
        plugin = tmp_path / "bad-plugin"
        plugin.mkdir()
        manifest_dir = plugin / ".claude-plugin"
        manifest_dir.mkdir()
        (manifest_dir / "plugin.json").write_text("not json{{{")

        loader = PluginLoader()
        info = await loader.load_plugin(plugin)
        assert info is None

    @pytest.mark.asyncio
    async def test_name_falls_back_to_directory(self, tmp_path):
        plugin = tmp_path / "my-dir-name"
        plugin.mkdir()
        manifest_dir = plugin / ".claude-plugin"
        manifest_dir.mkdir()
        (manifest_dir / "plugin.json").write_text(json.dumps({
            "version": "0.1.0",
        }))

        loader = PluginLoader()
        info = await loader.load_plugin(plugin)
        assert info is not None
        assert info.name == "my-dir-name"


class TestPluginDiscovery:
    @pytest.mark.asyncio
    async def test_load_plugins_from_directory(self, tmp_path):
        # Create two plugins
        for name in ["plugin-a", "plugin-b"]:
            d = tmp_path / name
            d.mkdir()
            md = d / ".claude-plugin"
            md.mkdir()
            (md / "plugin.json").write_text(json.dumps({"name": name}))

        # Also a non-plugin directory
        (tmp_path / "not-a-plugin").mkdir()

        loader = PluginLoader()
        loaded = await loader.load_plugins_from(tmp_path)
        names = [p.name for p in loaded]
        assert "plugin-a" in names
        assert "plugin-b" in names
        assert len(loaded) == 2

    @pytest.mark.asyncio
    async def test_load_from_nonexistent_directory(self, tmp_path):
        loader = PluginLoader()
        loaded = await loader.load_plugins_from(tmp_path / "nonexistent")
        assert loaded == []


class TestPluginNamespacing:
    @pytest.mark.asyncio
    async def test_skills_namespaced(self, plugin_dir_with_skills):
        loader = PluginLoader()

        with patch("skills.registry.register_skill") as mock_register:
            with patch("skills.loader.discover_skills") as mock_discover:
                from core.models import SkillDef
                mock_discover.return_value = [SkillDef(
                    name="my-skill",
                    description="A test skill",
                    prompt="Do the thing",
                )]
                info = await loader.load_plugin(plugin_dir_with_skills)

        assert info is not None
        assert "test-plugin:my-skill" in info.skills
        # Verify register_skill was called with namespaced name
        call_args = mock_register.call_args[0][0]
        assert call_args.name == "test-plugin:my-skill"


class TestPluginUnload:
    @pytest.mark.asyncio
    async def test_unload_plugin(self, plugin_dir):
        loader = PluginLoader()
        info = await loader.load_plugin(plugin_dir)
        assert info is not None
        assert loader.get_plugin("test-plugin") is not None

        await loader.unload_plugin("test-plugin")
        assert loader.get_plugin("test-plugin") is None

    @pytest.mark.asyncio
    async def test_unload_nonexistent(self):
        loader = PluginLoader()
        await loader.unload_plugin("nonexistent")  # should not raise

    @pytest.mark.asyncio
    async def test_unload_disconnects_mcp(self, plugin_dir):
        loader = PluginLoader()
        info = await loader.load_plugin(plugin_dir)
        info.mcp_servers = ["test-server"]
        loader._plugins["test-plugin"] = info

        mock_manager = MagicMock()
        mock_manager.disconnect = AsyncMock()

        with patch("core.mcp_client.get_mcp_manager", return_value=mock_manager):
            await loader.unload_plugin("test-plugin")

        mock_manager.disconnect.assert_called_once_with("test-server")

    @pytest.mark.asyncio
    async def test_unload_all(self, tmp_path):
        loader = PluginLoader()
        for name in ["p1", "p2"]:
            d = tmp_path / name
            d.mkdir()
            md = d / ".claude-plugin"
            md.mkdir()
            (md / "plugin.json").write_text(json.dumps({"name": name}))
            await loader.load_plugin(d)

        assert len(loader.list_plugins()) == 2
        await loader.unload_all()
        assert len(loader.list_plugins()) == 0


class TestPluginAgents:
    """F4: plugins can ship agent definitions that sync to the DB."""

    @pytest.fixture
    def plugin_dir_with_agent(self, plugin_dir: Path) -> Path:
        agents_dir = plugin_dir / "agents" / "my-agent"
        agents_dir.mkdir(parents=True)
        (agents_dir / "AGENT.md").write_text(
            "---\n"
            "name: my-agent\n"
            "description: Plugin-shipped agent\n"
            "---\n"
            "You are a helpful plugin agent.\n"
        )
        return plugin_dir

    @pytest.mark.asyncio
    async def test_agents_namespaced(self, plugin_dir_with_agent):
        """Plugin-owned agents should be namespaced plugin-name:agent-name."""
        loader = PluginLoader()

        with patch.object(loader, "_sync_plugin_agents", AsyncMock()) as mock_sync:
            info = await loader.load_plugin(plugin_dir_with_agent)

        assert info is not None
        assert "test-plugin:my-agent" in info.agents
        # Sync helper should have been called with namespaced defs
        assert mock_sync.await_count == 1
        passed_defs = mock_sync.await_args[0][1]
        assert passed_defs[0].name == "test-plugin:my-agent"

    @pytest.mark.asyncio
    async def test_agents_persisted_with_plugin_source(
        self, plugin_dir_with_agent, test_db, test_user,
    ):
        """End-to-end: loading a plugin writes an AgentDefinition with source='plugin:<name>'."""
        from sqlalchemy import select
        from core.db_models import AgentDefinition

        @asynccontextmanager
        async def _get_test_db():
            yield test_db

        loader = PluginLoader()
        with patch("core.database.get_db", side_effect=_get_test_db):
            info = await loader.load_plugin(plugin_dir_with_agent)

        assert info is not None
        rows = (await test_db.execute(
            select(AgentDefinition).where(AgentDefinition.user_id == test_user.id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].name == "test-plugin:my-agent"
        assert rows[0].source == "plugin:test-plugin"

    @pytest.mark.asyncio
    async def test_unload_deletes_plugin_agents(
        self, plugin_dir_with_agent, test_db, test_user,
    ):
        """Unloading a plugin removes its DB-backed agents."""
        from sqlalchemy import select
        from core.db_models import AgentDefinition

        @asynccontextmanager
        async def _get_test_db():
            yield test_db

        loader = PluginLoader()
        with patch("core.database.get_db", side_effect=_get_test_db):
            await loader.load_plugin(plugin_dir_with_agent)
            await loader.unload_plugin("test-plugin")

        rows = (await test_db.execute(select(AgentDefinition))).scalars().all()
        assert rows == []

    @pytest.mark.asyncio
    async def test_user_edited_agent_not_overwritten(
        self, plugin_dir_with_agent, test_db, test_user,
    ):
        """If a user has an agent with the same namespaced name, plugin sync skips it."""
        from sqlalchemy import select
        from core.db_models import AgentDefinition

        # Pre-seed a user-owned row with the namespaced name
        existing = AgentDefinition(
            user_id=test_user.id,
            name="test-plugin:my-agent",
            description="my edited version",
            system_prompt="custom prompt",
            source="user",
        )
        test_db.add(existing)
        await test_db.commit()

        @asynccontextmanager
        async def _get_test_db():
            yield test_db

        loader = PluginLoader()
        with patch("core.database.get_db", side_effect=_get_test_db):
            await loader.load_plugin(plugin_dir_with_agent)

        rows = (await test_db.execute(
            select(AgentDefinition).where(AgentDefinition.user_id == test_user.id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].source == "user"  # not overwritten
        assert rows[0].description == "my edited version"


class TestPluginList:
    @pytest.mark.asyncio
    async def test_list_plugins(self, plugin_dir):
        loader = PluginLoader()
        await loader.load_plugin(plugin_dir)

        plugins = loader.list_plugins()
        assert len(plugins) == 1
        assert plugins[0].name == "test-plugin"

    @pytest.mark.asyncio
    async def test_get_plugin(self, plugin_dir):
        loader = PluginLoader()
        await loader.load_plugin(plugin_dir)

        info = loader.get_plugin("test-plugin")
        assert info is not None
        assert info.version == "1.0.0"

        assert loader.get_plugin("nonexistent") is None
