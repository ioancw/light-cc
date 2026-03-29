"""Permission modes matching Claude Code's Shift+Tab cycling.

Modes:
- DEFAULT: Ask for file edits and risky shell commands
- AUTO_EDIT: Auto-approve Write/Edit, ask for shell commands
- PLAN: Read-only tools only (no edits, no execution)
- AUTO: Auto-approve everything (still block dangerous commands)
"""

from __future__ import annotations

from enum import Enum

from core.permissions import is_blocked, is_risky

# Tools that are always safe (read-only, no side effects)
READ_ONLY_TOOLS = frozenset({
    "Read", "Glob", "Grep",
    "ListMemories", "SearchMemory",
    "WebSearch", "WebFetch",
    "TaskList", "TaskGet",
    "ToolSearch",
})

# Tools that modify files
FILE_EDIT_TOOLS = frozenset({"Write", "Edit"})


class PermissionMode(str, Enum):
    DEFAULT = "default"
    AUTO_EDIT = "auto_edit"
    PLAN = "plan"
    AUTO = "auto"

    def next(self) -> PermissionMode:
        """Cycle to the next mode (for Shift+Tab behavior)."""
        order = [
            PermissionMode.DEFAULT,
            PermissionMode.AUTO_EDIT,
            PermissionMode.PLAN,
            PermissionMode.AUTO,
        ]
        idx = order.index(self)
        return order[(idx + 1) % len(order)]


def check_permission(
    mode: PermissionMode,
    tool_name: str,
    tool_input: dict,
) -> bool | str | None:
    """Check whether a tool call is allowed under the given mode.

    Returns:
        True  — auto-allow (no prompt needed)
        str   — deny with reason message
        None  — ask the user for confirmation
    """
    # Blocked commands are always denied regardless of mode
    if is_blocked(tool_name, tool_input):
        return "BLOCKED: This command is not allowed for safety reasons."

    if mode == PermissionMode.PLAN:
        if tool_name in READ_ONLY_TOOLS:
            return True
        return f"PLAN MODE: Tool '{tool_name}' is not available in plan mode. Only read-only tools are allowed."

    if mode == PermissionMode.AUTO:
        # Auto-approve everything that isn't blocked
        return True

    if mode == PermissionMode.AUTO_EDIT:
        # Auto-approve file edits
        if tool_name in FILE_EDIT_TOOLS:
            return True
        if tool_name in READ_ONLY_TOOLS:
            return True
        # Ask for shell commands and other tools
        if is_risky(tool_name, tool_input):
            return None  # ask user
        return True  # non-risky, non-edit tools are auto-approved

    # DEFAULT mode
    if tool_name in READ_ONLY_TOOLS:
        return True
    if tool_name in FILE_EDIT_TOOLS:
        return None  # ask user
    if is_risky(tool_name, tool_input):
        return None  # ask user
    return True  # non-risky tools auto-approved
