"""Sandboxed subprocess execution — sanitized environment for tool commands.

When running inside a container on Linux, bash commands are prefixed with
``unshare --net`` for network isolation. Scheduled tasks use a tighter timeout.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.session import current_session_get

_logger = logging.getLogger(__name__)

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

# Scheduled tasks use a tighter timeout than interactive commands
SCHEDULED_TASK_TIMEOUT = 60


def is_containerized() -> bool:
    """Detect if we are running inside a Docker/container environment."""
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text()
        if "docker" in cgroup or "containerd" in cgroup or "kubepods" in cgroup:
            return True
    except (FileNotFoundError, PermissionError):
        pass
    return False


def check_sandbox_warnings() -> None:
    """Log warnings at startup if production environment lacks container isolation."""
    env = os.environ.get("ENV", os.environ.get("ENVIRONMENT", "dev")).lower()
    if env in ("production", "prod") and not is_containerized():
        _logger.warning(
            "Running in production without container isolation. "
            "Deploy with Docker or Kubernetes for proper sandboxing."
        )


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

    # Network isolation: when running inside a container on Linux, prefix
    # the command with unshare --net to prevent network access from tool calls.
    actual_command = command
    if platform.system() == "Linux" and is_containerized():
        actual_command = f"unshare --net -- {command}"

    try:
        result = subprocess.run(
            actual_command,
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
