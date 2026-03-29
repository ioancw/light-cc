"""Skill registry — auto-discovers, matches, and manages skills."""

from __future__ import annotations

import logging
from pathlib import Path

from core.models import SkillDef
from skills.loader import discover_skills

logger = logging.getLogger(__name__)

_SKILLS: dict[str, SkillDef] = {}


def register_skill(skill: SkillDef) -> None:
    """Register a single skill definition."""
    _SKILLS[skill.name] = skill


def load_skills(skills_dir: str | Path) -> None:
    """Discover and register skills from a directory. Accumulates across calls."""
    skills = discover_skills(skills_dir)
    for s in skills:
        _SKILLS[s.name] = s
    logger.info(f"Loaded {len(_SKILLS)} skills: {list(_SKILLS.keys())}")


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
