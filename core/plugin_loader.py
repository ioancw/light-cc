"""Plugin loader — discover and load Claude Code/Cowork format plugins.

Plugin directory structure:
    my-plugin/
        .claude-plugin/
            plugin.json       # Plugin manifest
        .mcp.json             # MCP server definitions (optional)
        commands/
            *.md              # Slash commands (optional)
        skills/
            <name>/SKILL.md   # Skills (optional)
        agents/
            <name>/AGENT.md   # Agent definitions (optional)

Agents loaded from plugins are namespaced as `plugin-name:agent-name` and stored
in the DB with source='plugin:<plugin-name>' so they can be cleanly torn down
on uninstall.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PluginInfo:
    """Metadata and state for a loaded plugin."""

    def __init__(self, name: str, version: str, description: str, author: str, path: Path):
        self.name = name
        self.version = version
        self.description = description
        self.author = author
        self.path = path
        self.mcp_servers: list[str] = []  # Names of connected MCP servers
        self.commands: list[str] = []  # Names of loaded commands
        self.skills: list[str] = []  # Names of loaded skills
        self.agents: list[str] = []  # Namespaced names of loaded agents


class PluginLoader:
    """Discovers and loads plugins from directories."""

    def __init__(self):
        self._plugins: dict[str, PluginInfo] = {}

    async def load_plugin(self, plugin_dir: Path) -> PluginInfo | None:
        """Load a single plugin from a directory.

        The directory must contain .claude-plugin/plugin.json.
        """
        manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
        if not manifest_path.exists():
            logger.debug(f"No plugin manifest at {manifest_path}")
            return None

        try:
            manifest: dict[str, Any] = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except Exception as e:
            logger.error(f"Failed to parse {manifest_path}: {e}")
            return None

        name = manifest.get("name", plugin_dir.name)
        info = PluginInfo(
            name=name,
            version=manifest.get("version", "0.0.0"),
            description=manifest.get("description", ""),
            author=manifest.get("author", ""),
            path=plugin_dir,
        )

        # 1. Load MCP servers from .mcp.json
        mcp_config = plugin_dir / ".mcp.json"
        if mcp_config.exists():
            try:
                from core.mcp_client import load_mcp_config
                await load_mcp_config(str(mcp_config))
                # Track which servers were loaded
                mcp_data = json.loads(mcp_config.read_text(encoding="utf-8"))
                info.mcp_servers = list(mcp_data.get("mcpServers", {}).keys())
            except Exception as e:
                logger.error(f"Plugin '{name}': failed to load MCP config: {e}")

        # 2. Load commands from commands/ directory (namespaced as plugin-name:command-name)
        commands_dir = plugin_dir / "commands"
        if commands_dir.exists():
            from commands.loader import discover_commands
            from commands.registry import _COMMANDS
            for cmd in discover_commands(commands_dir):
                namespaced = f"{name}:{cmd.name}"
                cmd.name = namespaced
                _COMMANDS[namespaced] = cmd
                info.commands.append(namespaced)

        # 3. Load skills from skills/ directory (namespaced as plugin-name:skill-name)
        skills_dir = plugin_dir / "skills"
        if skills_dir.exists():
            from skills.registry import register_skill
            from skills.loader import discover_skills
            for skill in discover_skills(skills_dir):
                namespaced = f"{name}:{skill.name}"
                skill = skill.model_copy(update={"name": namespaced})
                register_skill(skill)
                info.skills.append(namespaced)

        # 4. Load agents from agents/ directory (namespaced as plugin-name:agent-name)
        agents_dir = plugin_dir / "agents"
        if agents_dir.exists():
            from core.agent_loader import discover_agents
            defs = discover_agents(agents_dir)
            for d in defs:
                namespaced = f"{name}:{d.name}"
                d.name = namespaced
                info.agents.append(namespaced)
            if defs:
                await self._sync_plugin_agents(name, defs)

        self._plugins[name] = info
        logger.info(
            f"Plugin '{name}' v{info.version} loaded: "
            f"{len(info.mcp_servers)} MCP servers, "
            f"{len(info.commands)} commands, "
            f"{len(info.skills)} skills, "
            f"{len(info.agents)} agents"
        )
        return info

    async def _sync_plugin_agents(self, plugin_name: str, defs: list) -> None:
        """Upsert plugin-owned agents into the DB for every existing user.

        New users created later pick these up in routes/auth.py signup.
        """
        from sqlalchemy import select
        from core.agent_loader import sync_agent_defs_to_db
        from core.database import get_db
        from core.db_models import User

        source_label = f"plugin:{plugin_name}"
        db = await get_db()
        try:
            user_ids = list((await db.execute(select(User.id))).scalars().all())
        finally:
            await db.close()

        for uid in user_ids:
            try:
                await sync_agent_defs_to_db(defs, uid, source_label=source_label)
            except Exception as e:
                logger.warning(
                    f"Plugin '{plugin_name}': failed to sync agents for user {uid}: {e}"
                )

    async def _delete_plugin_agents(self, plugin_name: str) -> None:
        """Remove all DB rows owned by this plugin (source='plugin:<name>')."""
        from sqlalchemy import delete
        from core.database import get_db
        from core.db_models import AgentDefinition

        source_label = f"plugin:{plugin_name}"
        db = await get_db()
        try:
            await db.execute(
                delete(AgentDefinition).where(AgentDefinition.source == source_label)
            )
            await db.commit()
        except Exception as e:
            logger.warning(f"Plugin '{plugin_name}': failed to delete agents: {e}")
        finally:
            await db.close()

    async def load_plugins_from(self, base_dir: Path) -> list[PluginInfo]:
        """Scan base_dir for subdirectories containing .claude-plugin/."""
        if not base_dir.exists():
            return []

        loaded = []
        for entry in sorted(base_dir.iterdir()):
            if not entry.is_dir():
                continue
            if (entry / ".claude-plugin" / "plugin.json").exists():
                info = await self.load_plugin(entry)
                if info:
                    loaded.append(info)

        if loaded:
            logger.info(
                f"Loaded {len(loaded)} plugins from {base_dir}: "
                f"{[p.name for p in loaded]}"
            )
        return loaded

    async def unload_plugin(self, name: str) -> None:
        """Disconnect MCP servers, remove commands, and remove skills for a plugin."""
        info = self._plugins.pop(name, None)
        if not info:
            return

        if info.mcp_servers:
            from core.mcp_client import get_mcp_manager
            manager = get_mcp_manager()
            for server_name in info.mcp_servers:
                await manager.disconnect(server_name)

        # Remove plugin commands from global registry
        if info.commands:
            from commands.registry import _COMMANDS
            for cmd_name in info.commands:
                _COMMANDS.pop(cmd_name, None)

        # Remove plugin skills from global registry
        if info.skills:
            from skills.registry import unregister_skill
            for skill_name in info.skills:
                unregister_skill(skill_name)

        # Remove plugin agents from DB
        if info.agents:
            await self._delete_plugin_agents(name)

        logger.info(f"Plugin '{name}' unloaded")

    async def unload_all(self) -> None:
        """Unload all plugins."""
        names = list(self._plugins.keys())
        for name in names:
            await self.unload_plugin(name)

    def list_plugins(self) -> list[PluginInfo]:
        """List all loaded plugins."""
        return list(self._plugins.values())

    def get_plugin(self, name: str) -> PluginInfo | None:
        """Get a plugin by name."""
        return self._plugins.get(name)


# Singleton instance
_loader: PluginLoader | None = None


def get_plugin_loader() -> PluginLoader:
    """Get or create the global PluginLoader instance."""
    global _loader
    if _loader is None:
        _loader = PluginLoader()
    return _loader
