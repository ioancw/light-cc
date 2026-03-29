"""Discover and load CLAUDE.md project configuration files.

Follows Claude Code conventions:
- Walk from working directory upward to root, collecting CLAUDE.md files
- Ancestors load first, closest directory last (can override)
- Support @path imports to inline referenced files
- Scan subdirectories (up to configurable depth) for nested CLAUDE.md files
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_IMPORT_RE = re.compile(r"^@(\S+)\s*$", re.MULTILINE)
_MAX_SUBDIRECTORY_DEPTH = 2
_MAX_IMPORT_DEPTH = 5


def _walk_ancestors(start: Path) -> list[Path]:
    """Collect CLAUDE.md files from *start* up to the filesystem root.

    Returns paths ordered from root toward *start* so that more-specific
    files appear later and can override ancestor instructions.
    """
    found: list[Path] = []
    current = start.resolve()
    while True:
        candidate = current / "CLAUDE.md"
        if candidate.is_file():
            found.append(candidate)
        parent = current.parent
        if parent == current:
            break
        current = parent
    found.reverse()  # root-first, CWD-last
    return found


def _scan_subdirectories(start: Path, max_depth: int = _MAX_SUBDIRECTORY_DEPTH) -> list[Path]:
    """Find CLAUDE.md files in subdirectories of *start* (up to *max_depth*)."""
    found: list[Path] = []
    if max_depth <= 0:
        return found
    try:
        for child in sorted(start.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                candidate = child / "CLAUDE.md"
                if candidate.is_file():
                    found.append(candidate)
                found.extend(_scan_subdirectories(child, max_depth - 1))
    except PermissionError:
        pass
    return found


def _resolve_imports(text: str, base_dir: Path, depth: int = 0) -> str:
    """Replace ``@path`` directives with the content of the referenced file.

    Recursion is capped at *_MAX_IMPORT_DEPTH* to prevent cycles.
    """
    if depth >= _MAX_IMPORT_DEPTH:
        return text

    def _replace(match: re.Match[str]) -> str:
        rel = match.group(1)
        target = (base_dir / rel).resolve()
        if not target.is_file():
            logger.warning("CLAUDE.md @import target not found: %s", target)
            return match.group(0)  # leave the directive as-is
        try:
            content = target.read_text(encoding="utf-8")
            return _resolve_imports(content, target.parent, depth + 1)
        except Exception:
            logger.warning("Failed to read @import target: %s", target, exc_info=True)
            return match.group(0)

    return _IMPORT_RE.sub(_replace, text)


def load_project_config(working_dir: Path | str | None = None) -> str:
    """Load and merge all CLAUDE.md files relevant to *working_dir*.

    Returns the merged text ready for system-prompt injection, or ``""``
    if no CLAUDE.md files are found.
    """
    if working_dir is None:
        working_dir = Path.cwd()
    working_dir = Path(working_dir).resolve()

    # Collect ancestor CLAUDE.md files (root-first)
    ancestor_files = _walk_ancestors(working_dir)
    # Collect subdirectory CLAUDE.md files
    subdirectory_files = _scan_subdirectories(working_dir)

    all_files = ancestor_files + subdirectory_files

    if not all_files:
        return ""

    sections: list[str] = []
    for path in all_files:
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if not raw:
                continue
            resolved = _resolve_imports(raw, path.parent)
            # Add a header indicating source for debugging
            rel = path.relative_to(working_dir) if path.is_relative_to(working_dir) else path
            sections.append(f"<!-- from {rel} -->\n{resolved}")
        except Exception:
            logger.warning("Failed to read CLAUDE.md: %s", path, exc_info=True)

    return "\n\n".join(sections)
