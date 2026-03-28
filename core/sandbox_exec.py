"""Sandboxed subprocess execution — sanitized environment for tool commands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.session import current_session_get

# Keys that are safe to pass through to subprocesses
_ENV_WHITELIST = {
    "PATH",
    "PYTHONPATH",
    "HOME",
    "USERPROFILE",      # Windows equivalent of HOME
    "SYSTEMROOT",       # Required for Windows subprocess
    "COMSPEC",          # Required for Windows shell
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "TERM",
    "MPLBACKEND",
    "OUTPUT_DIR",
}

# Keys that must NEVER leak into subprocesses
_ENV_BLOCKLIST = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "DATABASE_URL",
    "JWT_SECRET",
    "SECRET_KEY",
}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _build_safe_env(
    *,
    output_dir: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a sanitized environment dict for subprocess execution.

    Includes only whitelisted keys from os.environ, explicitly blocks secrets,
    and injects output_dir and project root into PYTHONPATH.
    """
    env: dict[str, str] = {}

    # Copy only whitelisted keys
    for key in _ENV_WHITELIST:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val

    # Ensure blocked keys are never present
    for key in _ENV_BLOCKLIST:
        env.pop(key, None)

    # Always set these
    env["MPLBACKEND"] = "Agg"

    if output_dir:
        env["OUTPUT_DIR"] = output_dir

    # Add project root to PYTHONPATH so scripts can import chart_theme, etc.
    project_root = str(_PROJECT_ROOT)
    existing_pypath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = project_root + os.pathsep + existing_pypath if existing_pypath else project_root

    if extra:
        for k, v in extra.items():
            if k not in _ENV_BLOCKLIST:
                env[k] = v

    return env


def _get_user_cwd() -> str | None:
    """Get the current user's workspace directory from the session, if available."""
    user_id = current_session_get("user_id")
    if not user_id or user_id == "default":
        return None

    from core.sandbox import get_workspace
    workspace = get_workspace(user_id)
    return str(workspace.root)


def _get_user_output_dir() -> str:
    """Get the current user's output directory, falling back to global."""
    user_id = current_session_get("user_id")
    if user_id and user_id != "default":
        from core.sandbox import get_workspace
        workspace = get_workspace(user_id)
        return str(workspace.outputs)
    return str(_PROJECT_ROOT / "data" / "outputs")


def run_shell_command(
    command: str,
    *,
    timeout: int = 120,
) -> dict[str, Any]:
    """Run a shell command with sanitized environment.

    Called from a thread executor (synchronous).
    """
    env = _build_safe_env(output_dir=_get_user_output_dir())
    cwd = _get_user_cwd()

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            env=env,
            cwd=cwd,
            timeout=timeout,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout.decode("utf-8", errors="replace")[:50000],
            "stderr": result.stderr.decode("utf-8", errors="replace")[:10000],
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s"}


def run_python_script(
    python_path: str,
    script_path: str,
    *,
    timeout: int = 120,
) -> dict[str, Any]:
    """Run a Python script with sanitized environment.

    Called from a thread executor (synchronous).
    """
    output_dir = _get_user_output_dir()
    env = _build_safe_env(output_dir=output_dir)
    cwd = _get_user_cwd()

    try:
        result = subprocess.run(
            [python_path, script_path],
            capture_output=True,
            env=env,
            cwd=cwd,
            timeout=timeout,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout.decode("utf-8", errors="replace")[:50000],
            "stderr": result.stderr.decode("utf-8", errors="replace")[:10000],
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Script timed out after {timeout}s"}
