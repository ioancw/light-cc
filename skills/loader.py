"""Parse agentskills.io compliant SKILL.md files (markdown + YAML frontmatter).

Supports the agentskills.io open standard (https://agentskills.io/specification)
plus Claude Code extension fields (argument-hint, context, agent, etc.).
"""

from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Any

import yaml

from core.models import SkillDef

logger = logging.getLogger(__name__)

# Match YAML frontmatter between --- delimiters
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# agentskills.io name validation: lowercase alphanumeric + hyphens,
# no leading/trailing hyphens, no consecutive hyphens, max 64 chars
_VALID_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

MAX_SKILL_LINES = 500  # agentskills.io recommendation


def _validate_skill_name(name: str) -> bool:
    """Validate a skill name per the agentskills.io spec."""
    if not name or len(name) > 64:
        return False
    if "--" in name:
        return False
    return bool(_VALID_NAME_RE.match(name))


def _parse_allowed_tools(raw: Any) -> list[str]:
    """Parse allowed-tools from various formats.

    agentskills.io spec: space-delimited string (e.g. "Bash(git:*) Read")
    Claude Code legacy: list or comma-separated string
    """
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str):
        return []
    # If it contains commas, split on commas (legacy format)
    if "," in raw:
        return [t.strip() for t in raw.split(",") if t.strip()]
    # Otherwise space-delimited (agentskills.io standard)
    return [t for t in raw.split() if t]


def parse_skill_file(path: Path) -> SkillDef | None:
    """Parse a single skill markdown file into a SkillDef."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    # Extract frontmatter
    match = _FRONTMATTER_RE.match(text)
    if not match:
        # No frontmatter -- treat entire file as prompt, use filename as name
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

    # Validate name per agentskills.io spec (warn but don't reject)
    # Allow plugin-namespaced names (plugin:skill) as a Claude Code extension
    base_name = name.split(":")[-1] if ":" in name else name
    if not _validate_skill_name(base_name):
        logger.warning(
            f"Skill name '{name}' does not comply with agentskills.io spec "
            f"(lowercase alphanumeric + hyphens, max 64 chars, no leading/trailing/consecutive hyphens). "
            f"Skill loaded anyway."
        )

    # Warn if directory name doesn't match skill name (spec requirement)
    parent_name = path.parent.name
    if path.name == "SKILL.md" and base_name != parent_name and name != parent_name:
        logger.warning(
            f"Skill name '{name}' does not match parent directory '{parent_name}' "
            f"(agentskills.io spec requires these to match)."
        )

    # Parse allowed-tools (supports both space-delimited and comma-separated)
    tools: list[str] = []
    if "tools" in meta:
        tools = _parse_allowed_tools(meta["tools"])
    elif "allowed-tools" in meta:
        tools = _parse_allowed_tools(meta["allowed-tools"])

    # Parse paths list
    paths: list[str] = []
    if "paths" in meta:
        raw_paths = meta["paths"]
        paths = raw_paths if isinstance(raw_paths, list) else [p.strip() for p in raw_paths.split(",")]

    # Parse agentskills.io standard optional fields
    license_val = meta.get("license", "")
    compatibility = meta.get("compatibility", "")
    metadata = meta.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    # Warn if body exceeds recommended length
    line_count = body.count("\n") + 1 if body else 0
    if line_count > MAX_SKILL_LINES:
        logger.warning(
            f"Skill '{name}' body is {line_count} lines "
            f"(agentskills.io recommends < {MAX_SKILL_LINES}). "
            f"Consider splitting into references/ files."
        )

    return SkillDef(
        name=name,
        description=description,
        license=license_val,
        compatibility=compatibility,
        metadata=metadata,
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

    Supports two formats (Claude Code / agentskills.io compatible):
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
