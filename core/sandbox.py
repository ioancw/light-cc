"""Per-user workspace isolation and path validation."""

from __future__ import annotations

import json
from pathlib import Path

from core.config import settings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_ROOT = _PROJECT_ROOT / settings.paths.data_dir / "users"


class UserWorkspace:
    """Encapsulates a user's isolated file workspace."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.root = _DATA_ROOT / user_id / "workspace"
        self.outputs = _DATA_ROOT / user_id / "outputs"
        self.uploads = _DATA_ROOT / user_id / "uploads"
        self.memory = _DATA_ROOT / user_id / "memory"

        # Ensure directories exist
        for d in (self.root, self.outputs, self.uploads, self.memory):
            d.mkdir(parents=True, exist_ok=True)

    def validate_path(self, requested: str, read_only: bool = False) -> Path:
        """Resolve a path and verify it's within allowed directories.

        Accepts paths relative to workspace root or absolute paths within it.
        When read_only=True, also allows paths within the project root (for
        tools like Glob, Grep, Read that need to browse project files).
        Raises PermissionError if the path escapes the allowed directories.
        """
        if "\x00" in requested:
            raise PermissionError("Null bytes not allowed in paths")

        path = Path(requested)

        # If relative, resolve against workspace root
        if not path.is_absolute():
            path = self.root / path

        resolved = path.resolve()

        # Allow access within workspace, outputs, uploads, or memory dirs
        allowed_roots = [
            self.root.resolve(),
            self.outputs.resolve(),
            self.uploads.resolve(),
            self.memory.resolve(),
        ]

        # Read-only tools may also access the project root
        if read_only:
            allowed_roots.append(_PROJECT_ROOT.resolve())

        for allowed in allowed_roots:
            try:
                resolved.relative_to(allowed)
                return resolved
            except ValueError:
                continue

        raise PermissionError(
            f"Access denied: path '{requested}' is outside your workspace"
        )


def get_workspace(user_id: str) -> UserWorkspace:
    """Factory for user workspace instances."""
    return UserWorkspace(user_id)


def validate_tool_path(file_path: str, read_only: bool = False) -> tuple[Path | None, str | None]:
    """Validate a file path from a tool call against the current user's workspace.

    When read_only=True, also permits paths within the project root (for Glob,
    Grep, Read which need to browse project files like skills and source code).

    Returns (resolved_path, None) on success, or (None, error_json) on failure.
    """
    from core.session import current_session_get

    user_id = current_session_get("user_id")
    if not user_id or user_id == "default":
        # No isolation enforced for legacy/default sessions
        return Path(file_path), None

    workspace = get_workspace(user_id)
    try:
        resolved = workspace.validate_path(file_path, read_only=read_only)
        return resolved, None
    except PermissionError as e:
        return None, json.dumps({"error": str(e)})
