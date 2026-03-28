"""Command registry — discovers and manages slash commands."""

from __future__ import annotations

import logging
from pathlib import Path

from core.models import CommandDef
from commands.loader import discover_commands

logger = logging.getLogger(__name__)

_COMMANDS: dict[str, CommandDef] = {}


def load_commands(commands_dir: str | Path) -> None:
    """Discover and register commands from a directory. Accumulates across calls."""
    commands = discover_commands(commands_dir)
    for c in commands:
        _COMMANDS[c.name] = c
    logger.info(f"Loaded {len(_COMMANDS)} commands: {list(_COMMANDS.keys())}")


def get_command(name: str) -> CommandDef | None:
    """Get a command by exact name."""
    return _COMMANDS.get(name)


def list_commands() -> list[CommandDef]:
    """List all registered commands."""
    return list(_COMMANDS.values())
