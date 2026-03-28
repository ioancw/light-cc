"""Bash tool — execute shell commands via subprocess.

Uses subprocess.run in a thread executor instead of asyncio.create_subprocess_shell
because the latter requires ProactorEventLoop on Windows, which isn't guaranteed
under all ASGI servers (uvicorn may use SelectorEventLoop depending on config).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from functools import partial
from typing import Any

from tools.registry import register_tool

TIMEOUT = 120  # seconds

# Match: python[3](.exe) -c "..." (the script is the last argument)
_PYTHON_C_RE = re.compile(
    r'^("?[^"]*python[^"]*"?'   # python executable (optionally quoted)
    r'\s+-c\s+)'                 # -c flag
    r'"([\s\S]*)"$'              # the script body in double quotes
)


def _needs_tempfile(command: str) -> bool:
    """On Windows, multiline `python -c` commands break in cmd.exe."""
    if sys.platform != "win32":
        return False
    m = _PYTHON_C_RE.match(command.strip())
    return m is not None and "\n" in m.group(2)


def _rewrite_as_tempfile(command: str) -> tuple[str, str]:
    """Rewrite a `python -c "..."` command to use a temp file.

    Returns (new_command, temp_file_path).
    """
    m = _PYTHON_C_RE.match(command.strip())
    prefix = m.group(1)  # e.g. 'C:/miniconda3/python.exe -c '
    script = m.group(2)

    # Extract just the python executable from the prefix
    python_exe = prefix[: prefix.index(" -c")].strip().strip('"')

    fd, tmp_path = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(script)

    return f'"{python_exe}" "{tmp_path}"', tmp_path


def _run_in_shell(command: str, timeout: int) -> dict[str, Any]:
    """Run a command synchronously with sandboxed env — called from a thread executor."""
    from core.sandbox_exec import run_shell_command
    return run_shell_command(command, timeout=timeout)


async def handle_bash(tool_input: dict[str, Any]) -> str:
    command = tool_input.get("command", "")
    if not command:
        return json.dumps({"error": "No command provided"})

    timeout = min(tool_input.get("timeout", TIMEOUT), 600)
    tmp_path: str | None = None

    try:
        run_command = command
        if _needs_tempfile(command):
            run_command, tmp_path = _rewrite_as_tempfile(command)

        # Run in thread executor with sandboxed environment
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            partial(_run_in_shell, run_command, timeout),
        )
        return json.dumps(result)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__} (no details)"
        return json.dumps({"error": msg})
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


register_tool(
    name="Bash",
    aliases=["bash"],
    description="Execute a shell command. Returns stdout, stderr, and exit code.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (max 600, default 120)",
            },
        },
        "required": ["command"],
    },
    handler=handle_bash,
)
