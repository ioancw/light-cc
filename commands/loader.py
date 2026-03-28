"""Parse command files (commands/*.md) with YAML frontmatter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from core.models import CommandDef

# Match YAML frontmatter between --- delimiters
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_command_file(path: Path) -> CommandDef | None:
    """Parse a single command markdown file into a CommandDef."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    match = _FRONTMATTER_RE.match(text)
    if not match:
        return CommandDef(
            name=path.stem,
            prompt=text.strip(),
        )

    frontmatter_text = match.group(1)
    body = text[match.end() :].strip()

    try:
        meta: dict[str, Any] = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError:
        return None

    name = meta.get("name", path.stem)
    description = meta.get("description", "")

    return CommandDef(
        name=name,
        description=description,
        argument_hint=meta.get("argument-hint", ""),
        prompt=body,
    )


def discover_commands(commands_dir: str | Path) -> list[CommandDef]:
    """Auto-discover all .md command files in a directory."""
    commands_path = Path(commands_dir)
    if not commands_path.exists():
        return []

    commands: list[CommandDef] = []
    for md_file in sorted(commands_path.glob("*.md")):
        cmd = parse_command_file(md_file)
        if cmd:
            commands.append(cmd)
    return commands
