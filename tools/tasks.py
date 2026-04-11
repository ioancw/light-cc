"""Task tracking tools — framework-agnostic task list."""

from __future__ import annotations

import json
import uuid
from typing import Any, Awaitable, Callable

from tools.registry import register_tool
from core.session import current_session_get, current_session_set

# Per-session callbacks for pushing task updates to the UI (set by ws_router.py)
_notify_callbacks: dict[str, Callable[[str, dict[str, Any]], Awaitable[None]]] = {}


def set_task_notify_callback(cb: Callable[[str, dict[str, Any]], Awaitable[None]], *, session_id: str | None = None) -> None:
    if session_id:
        _notify_callbacks[session_id] = cb
    else:
        _notify_callbacks["_default"] = cb


def remove_task_notify_callback(session_id: str) -> None:
    _notify_callbacks.pop(session_id, None)


def _get_notify_callback() -> Callable[[str, dict[str, Any]], Awaitable[None]] | None:
    from core.session import _current_session_id
    sid = _current_session_id.get(None)
    if sid and sid in _notify_callbacks:
        return _notify_callbacks[sid]
    return _notify_callbacks.get("_default")


def _get_tasks() -> dict[str, dict[str, str]]:
    """Get session-scoped tasks dict."""
    tasks = current_session_get("tasks")
    if tasks is None:
        tasks = {}
        current_session_set("tasks", tasks)
    return tasks


async def handle_create_task(tool_input: dict[str, Any]) -> str:
    """Create a task in the task list."""
    title = tool_input.get("title", "Untitled task")
    status = tool_input.get("status", "running")

    tasks = _get_tasks()
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"title": title, "status": status}

    _notify_callback = _get_notify_callback()
    if _notify_callback:
        await _notify_callback("task_update", {
            "task_id": task_id, "title": title, "status": status,
        })

    return json.dumps({"task_id": task_id, "title": title, "status": status})


async def handle_update_task(tool_input: dict[str, Any]) -> str:
    """Update a task's status."""
    task_id = tool_input.get("task_id", "")
    status = tool_input.get("status", "")

    tasks = _get_tasks()

    if task_id not in tasks:
        return json.dumps({"error": f"Task '{task_id}' not found"})

    tasks[task_id]["status"] = status

    _notify_callback = _get_notify_callback()
    if _notify_callback:
        await _notify_callback("task_update", {
            "task_id": task_id,
            "title": tasks[task_id]["title"],
            "status": status,
        })

    return json.dumps({"task_id": task_id, "status": status})


async def handle_list_tasks(tool_input: dict[str, Any]) -> str:
    """List all current tasks and their statuses."""
    tasks = _get_tasks()
    task_list = [
        {"task_id": tid, "title": t["title"], "status": t["status"]}
        for tid, t in tasks.items()
    ]
    return json.dumps({"tasks": task_list, "count": len(task_list)})


register_tool(
    name="TaskCreate",
    aliases=["create_task"],
    description=(
        "Create a visible task in the UI task list to show progress on multi-step work. "
        "Use this at the start of complex operations to give the user visibility into what you're doing. "
        "Update each task with TaskUpdate as you complete steps. "
        "Returns a task_id for use with TaskUpdate."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Task title displayed in the UI (e.g. 'Analyzing dataset', 'Searching codebase')",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "running", "done", "failed"],
                "description": "Initial status (default: running)",
            },
        },
        "required": ["title"],
    },
    handler=handle_create_task,
)

register_tool(
    name="TaskUpdate",
    aliases=["update_task"],
    description=(
        "Update a task's status in the UI task list. "
        "Call with status 'done' when a task completes, or 'failed' if it errors."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task ID returned by TaskCreate",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "running", "done", "failed"],
                "description": "New status for the task",
            },
        },
        "required": ["task_id", "status"],
    },
    handler=handle_update_task,
)

register_tool(
    name="TaskList",
    aliases=["list_tasks"],
    description="List all tasks and their current statuses. Use to check progress of previously created tasks.",
    input_schema={
        "type": "object",
        "properties": {},
    },
    handler=handle_list_tasks,
)
