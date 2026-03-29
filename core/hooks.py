"""Deterministic hook system matching Claude Code conventions.

Hooks fire external scripts at lifecycle events. They run outside the
agentic loop — no LLM involved. Configured in config.yaml under ``hooks``.

Events:
- PreToolUse: before a tool executes (non-zero exit blocks the call)
- PostToolUse: after a tool executes
- SessionStart: when a session is created
- SessionEnd: when a session is destroyed
- PromptSubmit: when user sends a message
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class HookDef(BaseModel):
    """A single hook definition from config."""

    script: str
    tools: list[str] = Field(default_factory=list)  # filter: only fire for these tools
    timeout: int = 30  # seconds


@dataclass
class HookResult:
    """Result from running a hook script."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


# Global registry: event_name -> list[HookDef]
_hooks: dict[str, list[HookDef]] = {}


def load_hooks(config: dict[str, Any] | None = None) -> None:
    """Load hook definitions from config dict.

    Expected format::

        {
            "PreToolUse": [{"script": "./lint.sh", "tools": ["Write"]}],
            "PostToolUse": [{"script": "./format.sh"}],
        }
    """
    _hooks.clear()
    if not config:
        return

    for event_name, hook_list in config.items():
        if not isinstance(hook_list, list):
            logger.warning("Hooks config for %s should be a list, got %s", event_name, type(hook_list).__name__)
            continue
        defs: list[HookDef] = []
        for item in hook_list:
            try:
                defs.append(HookDef(**item) if isinstance(item, dict) else HookDef(script=str(item)))
            except Exception:
                logger.warning("Invalid hook definition for %s: %s", event_name, item, exc_info=True)
        if defs:
            _hooks[event_name] = defs
            logger.info("Loaded %d hook(s) for %s", len(defs), event_name)


def has_hooks(event: str) -> bool:
    """Check if any hooks are registered for an event."""
    return bool(_hooks.get(event))


async def fire_hooks(
    event: str,
    context: dict[str, Any] | None = None,
    tool_name: str | None = None,
) -> list[HookResult]:
    """Fire all hooks registered for *event*.

    For PreToolUse/PostToolUse, *tool_name* is checked against each hook's
    ``tools`` filter. Context is passed to the script as JSON on stdin.

    Returns list of results. For PreToolUse, a non-zero exit code means
    the tool call should be blocked.
    """
    defs = _hooks.get(event, [])
    if not defs:
        return []

    results: list[HookResult] = []
    for hook_def in defs:
        # Apply tool filter
        if tool_name and hook_def.tools and tool_name not in hook_def.tools:
            continue

        result = await _run_hook_script(hook_def, context or {})
        results.append(result)

        if result.stdout:
            logger.debug("Hook %s stdout: %s", hook_def.script, result.stdout[:200])
        if result.stderr:
            logger.warning("Hook %s stderr: %s", hook_def.script, result.stderr[:200])

        # For PreToolUse, non-zero exit blocks further hooks too
        if event == "PreToolUse" and result.exit_code != 0:
            logger.info("PreToolUse hook %s blocked tool %s (exit code %d)",
                        hook_def.script, tool_name, result.exit_code)
            break

    return results


async def _run_hook_script(hook_def: HookDef, context: dict[str, Any]) -> HookResult:
    """Execute a hook script as a subprocess, passing context on stdin."""
    try:
        proc = await asyncio.create_subprocess_shell(
            hook_def.script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdin_data = json.dumps(context, default=str).encode("utf-8")

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_data),
                timeout=hook_def.timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return HookResult(exit_code=-1, stderr=f"Hook timed out after {hook_def.timeout}s")

        return HookResult(
            exit_code=proc.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace").strip(),
            stderr=stderr.decode("utf-8", errors="replace").strip(),
        )

    except Exception as e:
        logger.error("Failed to run hook %s: %s", hook_def.script, e)
        return HookResult(exit_code=-1, stderr=str(e))
