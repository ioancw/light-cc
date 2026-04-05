"""Permission system — detect risky tools and blocked commands.

THREAT MODEL NOTE:
This module is a UX guardrail (defense-in-depth), NOT a security sandbox.
It uses substring pattern matching on command strings and Python source text,
which is fundamentally bypassable via encoding, obfuscation, variable expansion,
eval(), aliases, and many other techniques.

For actual security isolation, use process-level sandboxing (containers,
seccomp, nsjail). This module prevents accidental destructive commands in
normal usage — it should not be relied upon to contain a malicious actor.
"""

from __future__ import annotations

import re
from typing import Any

# Only bash commands that look destructive need confirmation.
# Write/edit are normal workflow — don't interrupt for those.
BLOCKED_PATTERNS = [
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    "> /dev/",
    ":(){ :|:& };:",
    "chmod -R 777 /",
    "--no-preserve-root",
]

# Bash patterns that are risky enough to confirm
RISKY_BASH_PATTERNS = [
    "rm -rf",
    "rm -r",
    "drop table",
    "drop database",
    "truncate",
    "shutdown",
    "reboot",
    "kill -9",
    "pkill",
]


# System paths that should never be written to
_SYSTEM_PATHS = [
    "/etc/", "/usr/", "/bin/", "/sbin/", "/boot/", "/proc/", "/sys/",
    "C:\\Windows\\", "C:\\Program Files", "C:\\Program Files (x86)",
]

# Dangerous imports/calls in Python scripts
_RISKY_PYTHON_PATTERNS = [
    "os.system(",
    "subprocess.",
    "shutil.rmtree(",
    "__import__(",
    "importlib.",
    "ctypes.",
]


def _split_command(command: str) -> list[str]:
    """Split a shell command on separators and normalize for pattern matching.

    Handles newline injection, null bytes, and chained commands so each
    segment is checked independently against blocked/risky patterns.
    """
    # Replace newlines, carriage returns, and null bytes with semicolons
    normalized = re.sub(r'[\n\r\x00]', ' ; ', command)
    # Split on shell command separators: ; && || |
    segments = re.split(r'\s*(?:;|&&|\|\|)\s*', normalized)
    # Also check backtick and $() subshells by extracting their contents
    subshells = re.findall(r'`([^`]*)`|\$\(([^)]*)\)', command)
    for match in subshells:
        content = match[0] or match[1]
        if content.strip():
            segments.append(content.strip())
    # Always include the full command as a segment so patterns spanning
    # separators (like fork bombs ":(){ :|:& };:") are still matched.
    result = [command.lower()]
    result.extend(seg.strip().lower() for seg in segments if seg.strip())
    return result


def _normalize(tool_name: str) -> str:
    """Resolve aliases so permission checks work with both name forms."""
    from tools.registry import resolve_tool_name
    return resolve_tool_name(tool_name)


def is_blocked(tool_name: str, tool_input: dict[str, Any]) -> bool:
    """Check if a tool call should be blocked entirely."""
    name = _normalize(tool_name)
    if name == "Bash":
        segments = _split_command(tool_input.get("command", ""))
        return any(
            pattern.lower() in seg
            for seg in segments
            for pattern in BLOCKED_PATTERNS
        )
    return False


def is_risky(tool_name: str, tool_input: dict[str, Any]) -> bool:
    """Check if a tool call requires user confirmation."""
    name = _normalize(tool_name)
    if name == "Bash":
        segments = _split_command(tool_input.get("command", ""))
        if any(
            pattern.lower() in seg
            for seg in segments
            for pattern in RISKY_BASH_PATTERNS
        ):
            return True

    # Flag writes/edits to system paths
    if name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        if any(file_path.startswith(sp) for sp in _SYSTEM_PATHS):
            return True

    # Flag python_exec scripts with dangerous imports
    if name == "PythonExec":
        script = tool_input.get("script", "")
        if any(pat in script for pat in _RISKY_PYTHON_PATTERNS):
            return True

    return False


def summarize_tool_call(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Create a human-readable summary of what the tool will do."""
    name = _normalize(tool_name)
    if name == "Bash":
        cmd = tool_input.get("command", "")
        if len(cmd) > 100:
            cmd = cmd[:100] + "..."
        return f"`{cmd}`"
    return f"{tool_name}"
