"""Discover and load .claude/rules/ directory files.

Rules are markdown files with optional YAML frontmatter containing a ``paths``
field (glob patterns). Rules without ``paths`` are always active. Rules with
``paths`` activate only when the session touches matching files.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class RuleDef(BaseModel):
    """A rule parsed from .claude/rules/*.md."""

    name: str
    paths: list[str] = Field(default_factory=list)
    content: str = ""

    @property
    def always_active(self) -> bool:
        return len(self.paths) == 0


def _parse_rule_file(path: Path) -> RuleDef | None:
    """Parse a rule markdown file, extracting optional frontmatter."""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        logger.warning("Failed to read rule file: %s", path, exc_info=True)
        return None

    name = path.stem
    paths: list[str] = []
    content = raw

    match = _FRONTMATTER_RE.match(raw)
    if match:
        try:
            meta: dict[str, Any] = yaml.safe_load(match.group(1)) or {}
            name = meta.get("name", name)
            raw_paths = meta.get("paths", [])
            if isinstance(raw_paths, str):
                raw_paths = [raw_paths]
            paths = raw_paths
        except yaml.YAMLError:
            logger.warning("Invalid YAML frontmatter in rule: %s", path)
        content = raw[match.end():]

    content = content.strip()
    if not content:
        return None

    return RuleDef(name=name, paths=paths, content=content)


def load_rules(project_dir: Path | str | None = None) -> list[RuleDef]:
    """Discover all rule files from ``.claude/rules/`` in *project_dir*.

    Returns a list of :class:`RuleDef` instances.
    """
    if project_dir is None:
        project_dir = Path.cwd()
    project_dir = Path(project_dir).resolve()

    rules_dir = project_dir / ".claude" / "rules"
    if not rules_dir.is_dir():
        return []

    rules: list[RuleDef] = []
    for path in sorted(rules_dir.glob("*.md")):
        rule = _parse_rule_file(path)
        if rule is not None:
            rules.append(rule)
            logger.debug("Loaded rule: %s (paths=%s)", rule.name, rule.paths)

    return rules


def get_active_rules(rules: list[RuleDef], active_files: list[str] | None = None) -> str:
    """Return merged text of rules that should be active given *active_files*.

    Rules without ``paths`` are always included. Rules with ``paths`` are
    included only if at least one glob pattern matches any file in
    *active_files*.
    """
    if not rules:
        return ""

    active_files = active_files or []
    sections: list[str] = []

    for rule in rules:
        if rule.always_active:
            sections.append(rule.content)
            continue
        # Check if any active file matches any of the rule's glob patterns
        for pattern in rule.paths:
            if any(fnmatch.fnmatch(f, pattern) for f in active_files):
                sections.append(rule.content)
                break

    return "\n\n".join(sections)
