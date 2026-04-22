"""Skill registry — auto-discovers, matches, and manages skills.

Holds a single registry for both ``SKILL.md`` skills and legacy
``commands/*.md`` files. The latter are wrapped as ``SkillDef`` instances
with ``kind="legacy-command"`` so that ``/foo`` resolves identically
regardless of file location -- matching CC 2.1+'s unified surface.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from core.models import SkillDef
from skills.loader import _FRONTMATTER_RE, discover_skills

logger = logging.getLogger(__name__)

_SKILLS: dict[str, SkillDef] = {}


def register_skill(skill: SkillDef) -> None:
    """Register a single skill definition."""
    _SKILLS[skill.name] = skill


def unregister_skill(name: str) -> None:
    """Remove a skill by name."""
    _SKILLS.pop(name, None)


_skills_dirs: list[Path] = []
_commands_dirs_as_skills: list[Path] = []


def load_skills(skills_dir: str | Path) -> None:
    """Discover and register skills from a directory. Accumulates across calls."""
    resolved = Path(skills_dir)
    if resolved not in _skills_dirs:
        _skills_dirs.append(resolved)
    skills = discover_skills(skills_dir)
    for s in skills:
        _SKILLS[s.name] = s
    logger.info(f"Loaded {len(_SKILLS)} skills: {list(_SKILLS.keys())}")


def parse_command_as_skill(path: Path) -> SkillDef | None:
    """Parse a legacy ``commands/*.md`` file into a SkillDef.

    Defaults match CC's semantic for command files: user-invocable, NOT
    auto-invoked by the model. If the file has no description, the first
    non-empty line of the body is used.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    match = _FRONTMATTER_RE.match(text)
    meta: dict[str, Any] = {}
    body = text.strip()
    if match:
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            return None
        body = text[match.end():].strip()

    name = meta.get("name", path.stem)
    description = meta.get("description", "")
    if not description and body:
        for line in body.splitlines():
            stripped = line.strip()
            if stripped:
                description = stripped.lstrip("#").strip()
                break

    return SkillDef(
        name=name,
        description=description,
        argument_hint=meta.get("argument-hint", ""),
        # CC default for legacy command files: user-only, hidden from auto-invoke.
        disable_model_invocation=bool(meta.get("disable-model-invocation", True)),
        user_invocable=bool(meta.get("user-invocable", True)),
        skill_dir=str(path.parent),
        prompt=body,
        kind="legacy-command",
    )


def discover_commands_as_skills(commands_dir: str | Path) -> list[SkillDef]:
    """Auto-discover all ``*.md`` files in a commands directory as SkillDefs."""
    commands_path = Path(commands_dir)
    if not commands_path.exists():
        return []
    out: list[SkillDef] = []
    for md_file in sorted(commands_path.glob("*.md")):
        skill = parse_command_as_skill(md_file)
        if skill:
            out.append(skill)
    return out


def load_commands_as_skills(commands_dir: str | Path) -> None:
    """Discover legacy command files and register them as SkillDefs.

    Tracked separately from ``_skills_dirs`` so ``reload_skills`` knows to
    re-scan command directories with the legacy-command parser.
    """
    resolved = Path(commands_dir)
    if resolved not in _commands_dirs_as_skills:
        _commands_dirs_as_skills.append(resolved)
    for skill in discover_commands_as_skills(commands_dir):
        _SKILLS[skill.name] = skill
    logger.info(
        f"Loaded commands as skills from {commands_dir}: "
        f"{[s.name for s in _SKILLS.values() if s.kind == 'legacy-command']}"
    )


def reload_skills() -> int:
    """Re-read all skill files from previously loaded directories.

    Returns the number of skills loaded.
    """
    # Preserve plugin-namespaced skills (loaded via plugin_loader, not from dirs)
    plugin_skills = {k: v for k, v in _SKILLS.items() if ":" in k}
    _SKILLS.clear()
    _SKILLS.update(plugin_skills)
    for d in _skills_dirs:
        skills = discover_skills(d)
        for s in skills:
            _SKILLS[s.name] = s
    for d in _commands_dirs_as_skills:
        for skill in discover_commands_as_skills(d):
            _SKILLS[skill.name] = skill
    logger.info(f"Reloaded {len(_SKILLS)} skills: {list(_SKILLS.keys())}")
    return len(_SKILLS)


def get_skill(name: str) -> SkillDef | None:
    """Get a skill by exact name."""
    return _SKILLS.get(name)


def match_skill_by_name(name: str) -> SkillDef | None:
    """Match a skill by explicit /name invocation.

    Supports both exact names and namespaced names (plugin-name:skill-name).
    Only returns skills that are user-invocable.
    """
    # Exact match first (handles both plain and namespaced names)
    skill = _SKILLS.get(name)
    if skill and skill.user_invocable:
        return skill

    # If no colon in the query, try matching the suffix of namespaced skills
    if ":" not in name:
        for skill_name, skill in _SKILLS.items():
            if ":" in skill_name and skill_name.split(":", 1)[1] == name:
                if skill.user_invocable:
                    return skill

    return None


def match_skill_by_intent(user_message: str) -> SkillDef | None:
    """Match a skill from user message by intent (keyword matching).

    Only considers skills that allow model invocation.
    """
    msg_lower = user_message.strip().lower()
    best_match: SkillDef | None = None
    best_score = 0

    for skill in _SKILLS.values():
        # Skip skills that don't allow auto-invocation
        if skill.disable_model_invocation:
            continue

        score = 0
        # Check skill name words
        for word in skill.name.replace("-", " ").split():
            if word.lower() in msg_lower:
                score += 2
        # Check description words
        for word in skill.description.lower().split():
            if len(word) > 3 and word in msg_lower:
                score += 1

        if score > best_score:
            best_score = score
            best_match = skill

    # Require a minimum score to activate
    return best_match if best_score >= 2 else None


def list_skills() -> list[SkillDef]:
    """List all registered skills."""
    return list(_SKILLS.values())
