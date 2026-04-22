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


# Frontmatter key emit order. Mirrors ``_AGENT_FRONTMATTER_KEY_ORDER``
# in ``core/agent_loader.py`` -- stable ordering keeps wizard re-writes
# (e.g. enable/disable) producing minimal diffs.
_SKILL_FRONTMATTER_KEY_ORDER = (
    "name",
    "description",
    "argument-hint",
    "allowed-tools",
    "tools",
    "shell",
    "context",
    "agent",
    "disable-model-invocation",
    "user-invocable",
    "model",
    "effort",
    "paths",
    "license",
    "compatibility",
    "metadata",
)


def write_skill_def(
    def_: SkillDef,
    skills_dir: str | Path,
    *,
    overwrite: bool = False,
    extra_frontmatter: dict[str, Any] | None = None,
) -> Path:
    """Serialize a SkillDef to ``skills/<name>/SKILL.md``.

    Mirrors ``write_agent_def`` in ``core/agent_loader.py``: refuses to
    overwrite by default, persists CC pass-through fields verbatim, and
    drops defaults so files stay clean.
    """
    root = Path(skills_dir)
    target_dir = root / def_.name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "SKILL.md"

    if target.exists() and not overwrite:
        raise FileExistsError(
            f"SKILL.md already exists at {target}. Pass overwrite=True to replace."
        )

    raw: dict[str, Any] = {
        "name": def_.name,
        "description": def_.description,
    }
    if def_.argument_hint:
        raw["argument-hint"] = def_.argument_hint
    if def_.tools:
        # Use the spec's space-delimited form when there are no commas/parens
        # in any element; otherwise fall back to a YAML list for safety.
        raw["allowed-tools"] = list(def_.tools)
    # Defaults: only emit when they diverge from SkillDef defaults
    if def_.disable_model_invocation:
        raw["disable-model-invocation"] = True
    if def_.user_invocable is False:
        raw["user-invocable"] = False
    if def_.model:
        raw["model"] = def_.model
    if def_.effort:
        raw["effort"] = def_.effort
    if def_.context:
        raw["context"] = def_.context
    if def_.agent:
        raw["agent"] = def_.agent
    if def_.paths:
        raw["paths"] = list(def_.paths)
    if def_.license:
        raw["license"] = def_.license
    if def_.compatibility:
        raw["compatibility"] = def_.compatibility
    if def_.metadata:
        raw["metadata"] = dict(def_.metadata)

    if extra_frontmatter:
        for k, v in extra_frontmatter.items():
            if v in (None, "", [], {}):
                continue  # don't emit empty pass-through fields
            raw[k] = v

    ordered: dict[str, Any] = {}
    for k in _SKILL_FRONTMATTER_KEY_ORDER:
        if k in raw:
            ordered[k] = raw.pop(k)
    ordered.update(sorted(raw.items()))

    fm = yaml.safe_dump(
        ordered, sort_keys=False, default_flow_style=False, allow_unicode=True
    ).rstrip()
    body = (def_.prompt or "").rstrip()
    target.write_text(f"---\n{fm}\n---\n\n{body}\n", encoding="utf-8")
    return target


def set_skill_enabled(skill_path: Path, enabled: bool) -> None:
    """Flip a skill's user/model invocation flags by editing the frontmatter.

    "Disabled" means both ``user-invocable: false`` and
    ``disable-model-invocation: true`` so the skill disappears from the
    ``/`` autocomplete AND won't be auto-invoked. Re-enabling restores
    the canonical defaults (visible, model-invocable). Other frontmatter
    fields are preserved untouched.
    """
    text = skill_path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        # No frontmatter -- synthesise a minimal one so the toggle takes
        # effect on next reload.
        body = text.strip()
        meta: dict[str, Any] = {"name": skill_path.parent.name}
    else:
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            raise ValueError(f"Invalid YAML in {skill_path}")
        body = text[match.end():].strip()

    if enabled:
        meta.pop("disable-model-invocation", None)
        meta.pop("user-invocable", None)
    else:
        meta["disable-model-invocation"] = True
        meta["user-invocable"] = False

    ordered: dict[str, Any] = {}
    for k in _SKILL_FRONTMATTER_KEY_ORDER:
        if k in meta:
            ordered[k] = meta.pop(k)
    ordered.update(sorted(meta.items()))

    fm = yaml.safe_dump(
        ordered, sort_keys=False, default_flow_style=False, allow_unicode=True
    ).rstrip()
    skill_path.write_text(f"---\n{fm}\n---\n\n{body}\n", encoding="utf-8")


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
