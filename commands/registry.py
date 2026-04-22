"""Back-compat shim — legacy commands now live in the unified skills registry.

In CC 2.1+ the ``/foo`` surface dispatches to a single registry that holds
both ``SKILL.md`` skills and legacy ``commands/*.md`` files; the latter are
just SkillDefs with ``kind="legacy-command"``. This module preserves the
older import surface (``load_commands``, ``get_command``, ``list_commands``)
so existing callers and tests keep working without change.
"""

from __future__ import annotations

from pathlib import Path

from core.models import SkillDef
from skills.registry import (
    _SKILLS,
    load_commands_as_skills,
)


def load_commands(commands_dir: str | Path) -> None:
    """Register a directory of legacy command files in the unified registry."""
    load_commands_as_skills(commands_dir)


def reload_commands() -> int:
    """Return the count of legacy-command-kind entries currently registered.

    The actual reload happens via ``skills.registry.reload_skills`` which
    re-scans both skills and command directories in one pass.
    """
    return sum(1 for s in _SKILLS.values() if s.kind == "legacy-command")


def get_command(name: str) -> SkillDef | None:
    """Get a legacy-command-origin skill by name.

    Returns ``None`` for real ``SKILL.md`` skills so callers can distinguish
    if they really need to (most should just use ``match_skill_by_name``).
    """
    skill = _SKILLS.get(name)
    if skill and skill.kind == "legacy-command":
        return skill
    return None


def list_commands() -> list[SkillDef]:
    """List all legacy-command-origin skills."""
    return [s for s in _SKILLS.values() if s.kind == "legacy-command"]
