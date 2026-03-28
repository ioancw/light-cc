"""Background agent tool — fire-and-forget sub-tasks with notifications."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Awaitable, Callable

from tools.registry import register_tool, get_all_tool_schemas, get_tool_schemas
from core.permissions import is_blocked, is_risky
from core.job_queue import register_job, enqueue

# Store background tasks: task_id -> {status, result, name, created_at}
_background_tasks: dict[str, dict[str, Any]] = {}

MAX_BACKGROUND_TASKS = 100
TASK_TTL_SECONDS = 3600

# Optional callback for pushing notifications to the UI (set by server.py)
_notify_callback: Callable[[str, str], Awaitable[None]] | None = None


def set_notification_callback(cb: Callable[[str, str], Awaitable[None]]) -> None:
    global _notify_callback
    _notify_callback = cb


def _cleanup_tasks() -> None:
    """Remove completed/failed tasks older than TTL."""
    now = time.time()
    expired = [
        tid
        for tid, t in _background_tasks.items()
        if t["status"] in ("completed", "failed")
        and now - t.get("created_at", 0) > TASK_TTL_SECONDS
    ]
    for tid in expired:
        del _background_tasks[tid]


async def _bg_permission_check(name: str, tool_input: dict[str, Any]) -> bool | str:
    """Permission check for background agents — risky commands are denied."""
    if is_blocked(name, tool_input):
        return "BLOCKED: This command is not allowed for safety reasons."
    if is_risky(name, tool_input):
        return "DENIED: Risky commands require user confirmation and cannot run in background."
    return True


async def _run_background(task_id: str, prompt: str, task_name: str, **_kwargs: Any) -> None:
    """Run an agent in the background and notify when done."""
    from core import agent

    system = (
        "You are a background agent working on a specific task. "
        "Complete the task thoroughly and return a clear, concise result."
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    # Respect parent's skill tool filter
    from core.session import get_active_tool_filter
    tool_filter = get_active_tool_filter()
    tools = get_tool_schemas(tool_filter) if tool_filter else get_all_tool_schemas()

    output_parts: list[str] = []

    async def on_text(text: str) -> None:
        output_parts.append(text)

    async def on_tool_start(name: str, tool_input: dict[str, Any]) -> None:
        return None

    async def on_tool_end(ctx: Any, result: str) -> None:
        pass

    try:
        await agent.run(
            messages=messages,
            tools=tools,
            system=system,
            on_text=on_text,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
            on_permission_check=_bg_permission_check,
            max_turns=20,
        )
        result = "".join(output_parts)
        _background_tasks[task_id]["status"] = "completed"
        _background_tasks[task_id]["result"] = result[:10000]

        if _notify_callback:
            await _notify_callback(
                task_id,
                f"**Background task completed:** {task_name}\n\n{result[:2000]}",
            )

    except Exception as e:
        _background_tasks[task_id]["status"] = "failed"
        _background_tasks[task_id]["result"] = str(e)

        if _notify_callback:
            await _notify_callback(
                task_id,
                f"**Background task failed:** {task_name}\n\nError: {e}",
            )


async def handle_background_agent(tool_input: dict[str, Any]) -> str:
    """Start a background agent task."""
    prompt = tool_input.get("prompt", "")
    task_name = tool_input.get("task_name", "Background task")

    if not prompt:
        return json.dumps({"error": "No prompt provided"})

    _cleanup_tasks()

    if len(_background_tasks) >= MAX_BACKGROUND_TASKS:
        return json.dumps({"error": "Too many background tasks. Wait for some to complete."})

    task_id = str(uuid.uuid4())[:8]

    _background_tasks[task_id] = {
        "status": "running",
        "result": None,
        "name": task_name,
        "created_at": time.time(),
    }

    await enqueue("run_background_agent", task_id=task_id, prompt=prompt, task_name=task_name)

    return json.dumps({
        "status": "started",
        "task_id": task_id,
        "task_name": task_name,
        "message": "Task is running in the background. You'll be notified when it completes.",
    })


async def handle_check_background(tool_input: dict[str, Any]) -> str:
    """Check the status of a background task."""
    task_id = tool_input.get("task_id", "")

    if not task_id:
        tasks = [
            {"task_id": tid, "name": t["name"], "status": t["status"]}
            for tid, t in _background_tasks.items()
        ]
        return json.dumps({"tasks": tasks})

    if task_id not in _background_tasks:
        return json.dumps({"error": f"Task '{task_id}' not found"})

    task = _background_tasks[task_id]
    return json.dumps({
        "task_id": task_id,
        "name": task["name"],
        "status": task["status"],
        "result": task["result"],
    })


# Register as a distributable job
register_job("run_background_agent", _run_background)


register_tool(
    name="BackgroundAgent",
    aliases=["background_agent"],
    description="Start a sub-task running in the background. Returns immediately. The user will be notified when it completes.",
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The task for the background agent to complete",
            },
            "task_name": {
                "type": "string",
                "description": "A short name for the task (shown in notifications)",
            },
        },
        "required": ["prompt", "task_name"],
    },
    handler=handle_background_agent,
)

register_tool(
    name="CheckBackground",
    aliases=["check_background"],
    description="Check the status of background tasks. Call with no task_id to list all, or with a task_id to check a specific one.",
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "Task ID to check (omit to list all)",
            },
        },
    },
    handler=handle_check_background,
)
