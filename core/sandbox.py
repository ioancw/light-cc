"""Per-user workspace isolation and path validation.

Security model:
- Regular users: read the full project, write only to their own workspace
- Admin users: additionally write to shared project areas (skills, commands, .cortex)
- Bash commands: cwd locked to user workspace, path traversal blocked
"""

from __future__ import annotations

import json
from pathlib import Path

from core.config import settings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_ROOT = _PROJECT_ROOT / settings.paths.data_dir / "users"

# Project subdirectories that admins (but not regular users) may write to
_ADMIN_WRITABLE_DIRS = [
    _PROJECT_ROOT / "skills",
    _PROJECT_ROOT / "commands",
    _PROJECT_ROOT / ".cortex",
]


def _is_current_user_admin() -> bool:
    """Check if the current session user has admin privileges."""
    from core.session import current_session_get
    return bool(current_session_get("is_admin"))


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

    def validate_path(
        self,
        requested: str,
        read_only: bool = False,
        is_admin: bool = False,
    ) -> Path:
        """Resolve a path and verify it's within allowed directories.

        Accepts paths relative to workspace root or absolute paths within it.
        - read_only=True: also allows paths within the project root (Read, Glob, Grep)
        - is_admin=True: also allows writes to shared project areas (skills, commands, .cortex)
        Raises PermissionError if the path escapes the allowed directories.
        """
        if "\x00" in requested:
            raise PermissionError("Null bytes not allowed in paths")

        path = Path(requested)

        # If relative, resolve against workspace root
        if not path.is_absolute():
            path = self.root / path

        resolved = path.resolve()

        # User's own directories -- always writable
        allowed_roots = [
            self.root.resolve(),
            self.outputs.resolve(),
            self.uploads.resolve(),
            self.memory.resolve(),
        ]

        # Admin users can write to shared project areas
        if is_admin:
            allowed_roots.extend(d.resolve() for d in _ADMIN_WRITABLE_DIRS)

        # Read-only tools may browse the full project root
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
        resolved = workspace.validate_path(
            file_path,
            read_only=read_only,
            is_admin=_is_current_user_admin(),
        )
        return resolved, None
    except PermissionError as e:
        return None, json.dumps({"error": str(e)})


def validate_bash_command(command: str) -> str | None:
    """Check a bash command for path traversal attempts.

    Returns an error message if the command tries to escape the workspace,
    or None if it's safe.
    """
    from core.session import current_session_get

    user_id = current_session_get("user_id")
    if not user_id or user_id == "default":
        return None

    # Block commands that try to change directory outside workspace
    import re

    # Patterns that indicate path traversal or writing outside workspace
    _ESCAPE_PATTERNS = [
        r'cd\s+/',           # cd /absolute/path
        r'cd\s+\.\.',        # cd ..
        r'>\s*/',            # redirect to absolute path
        r'>>\s*/',           # append to absolute path
        r'cp\s+.*\s+/',     # cp ... /absolute
        r'mv\s+.*\s+/',     # mv ... /absolute
        r'ln\s+',            # symlinks can escape
        r'mount\s+',         # mount can overlay
        r'chroot\s+',        # chroot escape
    ]

    cmd_lower = command.lower().strip()
    for pattern in _ESCAPE_PATTERNS:
        if re.search(pattern, cmd_lower):
            # Allow if admin
            if _is_current_user_admin():
                return None
            return f"Command blocked: cannot write outside your workspace. Use relative paths within your workspace directory."

    return None
