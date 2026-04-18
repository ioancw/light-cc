"""Python execution tool — run scripts directly without shell quoting issues.

Uses subprocess.run in a thread executor instead of asyncio.create_subprocess_exec
because the latter requires ProactorEventLoop on Windows, which isn't guaranteed
under all ASGI servers.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from functools import partial
from pathlib import Path
from typing import Any

from tools.registry import register_tool

TIMEOUT = 120

# Guaranteed output directory — always exists, injected as OUTPUT_DIR env var
_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "outputs"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _get_python_path() -> str:
    """Get the Python executable path from config, falling back to sys.executable."""
    from core.config import settings

    path = getattr(settings, "python_path", None)
    return path or sys.executable


def _run_python(python_path: str, script_path: str, timeout: int) -> dict:
    """Run a Python script synchronously with sandboxed env — called from a thread executor."""
    from core.sandbox_exec import run_python_script
    return run_python_script(python_path, script_path, timeout=timeout)


async def handle_python_exec(tool_input: dict[str, Any]) -> str:
    """Execute a Python script from a temp file — no shell quoting issues."""
    script = tool_input.get("script", "")
    if not script:
        return json.dumps({"error": "No script provided"})

    timeout = min(tool_input.get("timeout", TIMEOUT), 600)

    fd, tmp_path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)

        python_path = _get_python_path()

        # Run with sandboxed environment (no secrets, per-user output dir)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            partial(_run_python, python_path, tmp_path, timeout),
        )
        return json.dumps(result)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__} (no details)"
        return json.dumps({"error": msg})
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


register_tool(
    name="PythonExec",
    aliases=["python_exec"],
    description=(
        "Execute a Python script as a .py file. Preferred over Bash for all Python code — "
        "avoids shell quoting issues on Windows. "
        "Use for: data analysis, computations, file processing, any task that needs Python libraries. "
        "The env var OUTPUT_DIR points to a writable directory for saving output files — "
        "use `import os; out = os.environ['OUTPUT_DIR']` to get the path. "
        "Print output file paths to stdout for auto-rendering of images in the UI. "
        "For charts: do NOT write .plotly.json files here — compute the data arrays, then call "
        "the CreateChart tool with x_values/y_values. Only fall back to writing a raw "
        ".plotly.json in this tool when CreateChart genuinely cannot express the chart "
        "(custom 3D, chart-of-charts, highly bespoke layouts). When that rare case applies: "
        "one chart = one idea, no embedded long text or callouts, no `template` set."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "script": {
                "type": "string",
                "description": "The Python script to execute. Can be multi-line. All standard libraries and installed packages are available.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (max 600, default 120). Increase for long-running computations.",
            },
        },
        "required": ["script"],
    },
    handler=handle_python_exec,
)
