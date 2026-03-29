"""Parse Claude Code format skill files (markdown + YAML frontmatter)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from core.models import SkillDef

# Match YAML frontmatter between --- delimiters
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_skill_file(path: Path) -> SkillDef | None:
    """Parse a single skill markdown file into a SkillDef."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    # Extract frontmatter
    match = _FRONTMATTER_RE.match(text)
    if not match:
        # No frontmatter — treat entire file as prompt, use filename as name
        return SkillDef(
            name=path.stem,
            skill_dir=str(path.parent),
            prompt=text.strip(),
        )

    frontmatter_text = match.group(1)
    body = text[match.end() :].strip()

    try:
        meta: dict[str, Any] = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError:
        return None

    # Support both 'name' and filename as skill name
    name = meta.get("name", path.stem)
    description = meta.get("description", "")

    # Support Claude Code's tool filtering formats
    tools: list[str] = []
    if "tools" in meta:
        tools = meta["tools"] if isinstance(meta["tools"], list) else [meta["tools"]]
    elif "allowed-tools" in meta:
        raw = meta["allowed-tools"]
        tools = raw if isinstance(raw, list) else [t.strip() for t in raw.split(",")]

    # Parse paths list
    paths: list[str] = []
    if "paths" in meta:
        raw_paths = meta["paths"]
        paths = raw_paths if isinstance(raw_paths, list) else [p.strip() for p in raw_paths.split(",")]

    return SkillDef(
        name=name,
        description=description,
        argument_hint=meta.get("argument-hint", ""),
        tools=tools,
        disable_model_invocation=bool(meta.get("disable-model-invocation", False)),
        user_invocable=bool(meta.get("user-invocable", True)),
        model=meta.get("model", ""),
        effort=meta.get("effort", ""),
        context=meta.get("context", ""),
        agent=meta.get("agent", ""),
        paths=paths,
        skill_dir=str(path.parent),
        prompt=body,
    )


def discover_skills(skills_dir: str | Path) -> list[SkillDef]:
    """Auto-discover skills from a directory.

    Supports two formats (Claude Code compatible):
    1. Directory format: skills/<name>/SKILL.md  (canonical)
    2. Flat format:      skills/<name>.md         (backward compat)
    """
    skills_path = Path(skills_dir)
    if not skills_path.exists():
        return []

    skills: list[SkillDef] = []
    seen_names: set[str] = set()

    # 1. Directory format: skills/<name>/SKILL.md (takes priority)
    for skill_md in sorted(skills_path.glob("*/SKILL.md")):
        skill = parse_skill_file(skill_md)
        if skill:
            # Use parent directory name if no name in frontmatter
            if skill.name == "SKILL":
                skill = skill.model_copy(update={"name": skill_md.parent.name})
            skills.append(skill)
            seen_names.add(skill.name)

    # 2. Flat format: skills/<name>.md (backward compat, skip if already loaded)
    for md_file in sorted(skills_path.glob("*.md")):
        skill = parse_skill_file(md_file)
        if skill and skill.name not in seen_names:
            skills.append(skill)
            seen_names.add(skill.name)

    return skills
