"""Command registry — discovers and manages slash commands."""

from __future__ import annotations

import logging
from pathlib import Path

from core.models import CommandDef
from commands.loader import discover_commands

logger = logging.getLogger(__name__)

_COMMANDS: dict[str, CommandDef] = {}
_commands_dirs: list[Path] = []


def load_commands(commands_dir: str | Path) -> None:
    """Discover and register commands from a directory. Accumulates across calls."""
    resolved = Path(commands_dir)
    if resolved not in _commands_dirs:
        _commands_dirs.append(resolved)
    commands = discover_commands(commands_dir)
    for c in commands:
        _COMMANDS[c.name] = c
    logger.info(f"Loaded {len(_COMMANDS)} commands: {list(_COMMANDS.keys())}")


def reload_commands() -> int:
    """Re-read all command files from previously loaded directories.

    Returns the number of commands loaded.
    """
    plugin_cmds = {k: v for k, v in _COMMANDS.items() if ":" in k}
    _COMMANDS.clear()
    _COMMANDS.update(plugin_cmds)
    for d in _commands_dirs:
        commands = discover_commands(d)
        for c in commands:
            _COMMANDS[c.name] = c
    logger.info(f"Reloaded {len(_COMMANDS)} commands: {list(_COMMANDS.keys())}")
    return len(_COMMANDS)


def get_command(name: str) -> CommandDef | None:
    """Get a command by exact name."""
    return _COMMANDS.get(name)


def list_commands() -> list[CommandDef]:
    """List all registered commands."""
    return list(_COMMANDS.values())
