"""FastAPI + WebSocket server for Light CC — replaces Chainlit.

Run with: uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core import agent
from core.auth import decode_token, get_user_by_id
from core.config import settings
from core.database import get_db, init_db, shutdown_db
from core.job_queue import init_job_queue, shutdown_job_queue
from core.redis_store import init_redis, shutdown_redis
from core.telemetry import (
    setup_telemetry, record_request, record_tool_call,
    session_opened, session_closed,
)
from core.permissions import is_blocked, is_risky, summarize_tool_call
from core.rate_limit import check_rate_limit
from core.session import (
    create_session,
    destroy_session_async,
    load_conversation,
    save_conversation,
    session_get,
    session_set,
    set_current_session,
    sync_session_to_redis,
)
from memory.manager import ensure_user_dirs, load_memory, set_current_user
from routes.auth import router as auth_router
from routes.conversations import router as conversations_router
from routes.admin import router as admin_router
from routes.files import router as files_router
from routes.usage import router as usage_router
from commands.registry import get_command, list_commands, load_commands
from skills.registry import list_skills, load_skills, match_skill_by_intent, match_skill_by_name
from tools.registry import get_all_tool_schemas, get_tool_schemas

import tools  # noqa: F401 — triggers tool registration

logger = logging.getLogger(__name__)

# ─── Load skills and commands at startup ───
_PROJECT_ROOT = Path(__file__).resolve().parent
for skills_dir in settings.paths.skills_dirs:
    resolved = Path(skills_dir).expanduser()
    if not resolved.is_absolute():
        resolved = _PROJECT_ROOT / resolved
    load_skills(resolved)

# Load commands from configured directories
for commands_dir in settings.paths.commands_dirs:
    resolved = Path(commands_dir).expanduser()
    if not resolved.is_absolute():
        resolved = _PROJECT_ROOT / resolved
    load_commands(resolved)

# ─── System prompt ───
import platform as _platform
import sys as _sys

_os_info = f"{_platform.system()} {_platform.release()}"
_python_info = _sys.executable
_outputs_dir = _PROJECT_ROOT / "data" / "outputs"
_outputs_dir.mkdir(parents=True, exist_ok=True)

BASE_SYSTEM_PROMPT = f"""You are Light CC, a helpful AI assistant with tools for data processing, \
visualization, and general tasks.

Environment: {_os_info} | Python: {_python_info}
Output directory: {_outputs_dir} (always use this for saving generated files)

Guidelines:
- For Python code, prefer the python_exec tool — it runs scripts as .py files and avoids \
shell quoting issues.
- For simple charts, use the create_chart tool (supports bar, line, scatter, histogram, box, \
area, pie, heatmap, violin, treemap, sunburst, funnel, waterfall, radar, sankey, candlestick, gauge).
- For complex/custom charts via python_exec, save as *.plotly.json for interactive rendering \
(e.g. `fig.write_json(path)`) or *.png for static. Interactive is preferred.
- For D3.js or custom HTML visualizations, use `from tools.d3_theme import wrap_d3` to wrap D3 \
scripts in themed HTML, save as *.html, and print the path. The UI renders HTML files in sandboxed \
iframes inline. The wrap_d3() function provides: d3 v7, `colors` array, `width`/`height` vars, \
and CSS matching the dark UI theme.
- The UI is dark-themed. For python_exec charts use `from tools.chart_theme import apply_theme; \
apply_theme(fig)` or at minimum template='plotly_dark'. For matplotlib: plt.style.use('dark_background').
- The UI auto-renders images, Plotly charts, HTML files, and CSV files from tool output — \
print file paths to stdout and they'll render inline. Don't re-read or re-display files you just created.
- Always save output files to the output directory above. Never use /tmp/ or guess user directories.
- Keep responses concise unless the user asks for detail.
- Keep a professional tone. Do not use emojis in responses.

Model: {settings.model}
"""


def _build_system_prompt(
    skill_prompt: str | None = None,
    memory_context: str | None = None,
    user_system_prompt: str | None = None,
) -> str:
    parts = [BASE_SYSTEM_PROMPT]
    if user_system_prompt:
        parts.append(f"\n## User Instructions\n{user_system_prompt}")
    if skill_prompt:
        parts.append(f"\n## Active Skill\n{skill_prompt}")
    if memory_context:
        parts.append(
            f"\n## Your Memory\nThe following are things you remember about this user:\n{memory_context}"
        )
    # List available skills (user-invocable and auto-activated)
    skills = list_skills()
    user_invocable = [s for s in skills if s.user_invocable]
    auto_activated = [s for s in skills if not s.disable_model_invocation]

    if user_invocable:
        lines = []
        for s in user_invocable:
            hint = f" {s.argument_hint}" if s.argument_hint else ""
            lines.append(f"- /{s.name}{hint}: {s.description}")
        parts.append(f"\n## Available Skills\nUsers can invoke these with /name:\n" + "\n".join(lines))

    if auto_activated:
        names = ", ".join(s.name for s in auto_activated)
        parts.append(
            f"\n## Auto-Activated Skills\nThese activate automatically based on conversation context: {names}"
        )

    # List commands (workflow orchestrators)
    commands = list_commands()
    if commands:
        cmd_lines = []
        for c in commands:
            hint = f" {c.argument_hint}" if c.argument_hint else ""
            cmd_lines.append(f"- /{c.name}{hint}: {c.description}")
        parts.append(f"\n## Available Commands\n" + "\n".join(cmd_lines))

    return "\n".join(parts)


# ─── FastAPI app ───
app = FastAPI(title="Light CC")
app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(admin_router)
app.include_router(files_router)
app.include_router(usage_router)
app.mount("/static", StaticFiles(directory=str(_PROJECT_ROOT / "static")), name="static")


@app.on_event("startup")
async def startup():
    setup_telemetry()
    await init_db()
    await init_redis()
    await init_job_queue()

    # Load plugins from configured directories
    from core.plugin_loader import get_plugin_loader
    loader = get_plugin_loader()
    for plugins_dir in settings.paths.plugins_dirs:
        resolved = Path(plugins_dir).expanduser()
        if not resolved.is_absolute():
            resolved = _PROJECT_ROOT / resolved
        await loader.load_plugins_from(resolved)

    # Also load project-level .mcp.json if present
    project_mcp = _PROJECT_ROOT / ".mcp.json"
    if project_mcp.exists():
        from core.mcp_client import load_mcp_config
        await load_mcp_config(str(project_mcp))


@app.on_event("shutdown")
async def shutdown():
    # Unload plugins and disconnect MCP servers
    from core.plugin_loader import get_plugin_loader
    await get_plugin_loader().unload_all()
    from core.mcp_client import get_mcp_manager
    await get_mcp_manager().disconnect_all()

    await shutdown_job_queue()
    await shutdown_redis()
    await shutdown_db()


@app.get("/health")
async def health():
    """Health check — verifies DB and Redis connectivity."""
    checks: dict[str, str] = {}

    # DB check
    try:
        from sqlalchemy import text
        db = await get_db()
        await db.execute(text("SELECT 1"))
        await db.close()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # Redis check
    try:
        from core.redis_store import is_available
        checks["redis"] = "ok" if is_available() else "not configured"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    healthy = checks["database"] == "ok"
    status_code = 200 if healthy else 503
    from fastapi.responses import JSONResponse
    return JSONResponse({"status": "healthy" if healthy else "unhealthy", "checks": checks}, status_code=status_code)


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    try:
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
        from fastapi.responses import Response
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
    except ImportError:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "prometheus_client not installed"}, status_code=501)


@app.get("/")
async def index():
    """Serve main app if authenticated, otherwise auth page."""
    return FileResponse(str(_PROJECT_ROOT / "static" / "loom.html"))


@app.get("/login")
async def login_page():
    return FileResponse(str(_PROJECT_ROOT / "static" / "auth.html"))


# ─── Image / chart helpers ───
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


async def _send_images_if_any(
    tool_id: str,
    result: str,
    send_event,
) -> None:
    """Detect file paths in tool stdout — send images as base64, Plotly JSON as interactive charts."""
    try:
        parsed = json.loads(result)
        stdout = parsed.get("stdout", "")
        for line in stdout.strip().splitlines():
            line = line.strip()
            p = Path(line)
            if not p.exists():
                continue

            # Interactive Plotly chart (*.plotly.json)
            if p.name.endswith(".plotly.json"):
                try:
                    chart_json = p.read_text(encoding="utf-8")
                    # Validate it's parseable
                    json.loads(chart_json)
                    await send_event("chart", {
                        "tool_id": tool_id,
                        "title": p.stem.replace(".plotly", ""),
                        "plotly_json": chart_json,
                    })
                except (json.JSONDecodeError, OSError):
                    pass
                continue

            # HTML embeds (D3, custom visualizations)
            if p.suffix.lower() == ".html":
                try:
                    html_content = p.read_text(encoding="utf-8")
                    await send_event("html_embed", {
                        "tool_id": tool_id,
                        "name": p.stem,
                        "html": html_content,
                    })
                except OSError:
                    pass
                continue

            # Static images
            if p.suffix.lower() in _IMAGE_EXTS:
                data = base64.b64encode(p.read_bytes()).decode()
                mime = _MIME_MAP.get(p.suffix.lower(), "image/png")
                await send_event("image", {
                    "tool_id": tool_id,
                    "name": p.stem,
                    "mime_type": mime,
                    "data_base64": data,
                })
    except (json.JSONDecodeError, ValueError):
        pass


async def _send_chart_if_any(
    tool_id: str,
    tool_name: str,
    result: str,
    send_event,
) -> None:
    """If the tool was CreateChart, send the Plotly figure JSON."""
    from tools.registry import resolve_tool_name
    if resolve_tool_name(tool_name) != "CreateChart":
        return
    try:
        parsed = json.loads(result)
        if parsed.get("inline"):
            from tools.chart import get_last_figure

            fig = get_last_figure()
            if fig is not None:
                await send_event("chart", {
                    "tool_id": tool_id,
                    "title": parsed.get("title", "Chart"),
                    "plotly_json": fig.to_json(),
                })
    except (json.JSONDecodeError, ImportError):
        pass


async def _send_tables_if_any(
    tool_id: str,
    tool_name: str,
    result: str,
    send_event,
) -> None:
    """Detect HTML tables in tool results and CSV files in stdout."""
    try:
        parsed = json.loads(result)

        # 1. Data tools return table HTML directly
        for key in ("head_html", "describe_html", "table_html"):
            html = parsed.get(key)
            if html:
                logger.info(f"[tables] Sending {key} table for tool {tool_id} ({len(html)} chars)")
                await send_event("table", {
                    "tool_id": tool_id,
                    "html": html,
                })

        # 2. Detect CSV files in stdout from any tool (python_exec, bash)
        stdout = parsed.get("stdout", "")
        if stdout:
            for line in stdout.strip().splitlines():
                line = line.strip()
                p = Path(line)
                if p.suffix.lower() == ".csv" and p.exists():
                    try:
                        import pandas as pd
                        from tools.data_tools import _df_to_html

                        df = pd.read_csv(p)
                        # Send preview table
                        preview_html = _df_to_html(df, title=p.stem)
                        await send_event("table", {
                            "tool_id": tool_id,
                            "html": preview_html,
                        })
                        # Send summary statistics table
                        desc = df.describe(include="all")
                        desc_html = _df_to_html(desc, max_rows=50, title="Summary Statistics")
                        await send_event("table", {
                            "tool_id": tool_id,
                            "html": desc_html,
                        })
                    except Exception:
                        pass

    except (json.JSONDecodeError, ValueError):
        pass


# ─── WebSocket endpoint ───
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    # ─── Authenticate via token query param ───
    token = ws.query_params.get("token")
    user_id = "default"
    user_email = ""
    user_display_name = "User"

    if token:
        payload = decode_token(token)
        if payload and payload.get("type") == "access":
            user_id = payload["sub"]
            user_email = payload.get("email", "")
            # Fetch display name
            db = await get_db()
            try:
                user = await get_user_by_id(db, user_id)
                if user:
                    user_display_name = user.display_name
            finally:
                await db.close()
        else:
            await ws.close(code=4001, reason="Invalid or expired token")
            return

    session_id = str(uuid.uuid4())
    session = create_session(session_id, user_id=user_id)
    session_opened()

    # Pending permission futures: request_id -> Future[bool]
    pending_permissions: dict[str, asyncio.Future] = {}

    # Agent task reference (so receive loop isn't blocked)
    agent_task: asyncio.Task | None = None

    async def send_event(event_type: str, data: dict[str, Any]) -> None:
        try:
            await ws.send_json({"type": event_type, "data": data})
        except Exception:
            pass  # connection may have closed

    # Register notification callbacks for background tasks and task list updates
    from tools.background import set_notification_callback
    from tools.tasks import set_task_notify_callback

    async def bg_notify(task_id: str, message: str) -> None:
        await send_event("notification", {"task_id": task_id, "message": message})

    async def task_notify(event_type: str, data: dict[str, Any]) -> None:
        await send_event(event_type, data)

    set_notification_callback(bg_notify)
    set_task_notify_callback(task_notify)

    # Send connected event with user info
    await send_event("connected", {
        "session_id": session_id,
        "model": settings.model,
        "available_models": settings.available_models,
        "user": {
            "id": user_id,
            "email": user_email,
            "display_name": user_display_name,
        },
    })

    try:
        while True:
            raw = await ws.receive_json()
            event_type = raw.get("type", "")
            data = raw.get("data", {})

            if event_type == "user_message":
                # Rate limit check
                allowed, reason = check_rate_limit(user_id, "message")
                if not allowed:
                    await send_event("error", {"message": reason})
                    continue

                # Run agent loop as a task so we can still receive permission responses
                agent_task = asyncio.create_task(
                    _handle_user_message(
                        session_id, data, send_event, pending_permissions,
                    )
                )

            elif event_type == "permission_response":
                req_id = data.get("request_id", "")
                future = pending_permissions.get(req_id)
                if future and not future.done():
                    future.set_result(data.get("allowed", False))

            elif event_type == "clear_conversation":
                # Save current conversation before clearing
                await save_conversation(session_id)
                session_set(session_id, "messages", [])
                session_set(session_id, "conversation_id", None)

            elif event_type == "resume_conversation":
                conv_id = data.get("conversation_id", "")
                if conv_id:
                    messages = await load_conversation(conv_id)
                    session_set(session_id, "messages", messages)
                    session_set(session_id, "conversation_id", conv_id)
                    # Restore conversation model
                    from core.db_models import Conversation
                    from sqlalchemy import select
                    db = await get_db()
                    try:
                        result = await db.execute(
                            select(Conversation.model).where(Conversation.id == conv_id)
                        )
                        conv_model = result.scalar_one_or_none()
                        if conv_model:
                            session_set(session_id, "active_model", conv_model)
                    finally:
                        await db.close()
                    # Build renderable messages for the frontend
                    render_messages = []
                    for msg in messages:
                        content = msg.get("content", "")
                        if isinstance(content, str):
                            render_messages.append({"role": msg["role"], "content": content})
                        elif isinstance(content, list):
                            # Extract text and tool_use/tool_result blocks
                            text_parts = []
                            for block in content:
                                if isinstance(block, dict):
                                    if block.get("type") == "text":
                                        text_parts.append(block["text"])
                                    elif block.get("type") == "tool_result":
                                        # Skip tool results in rendering
                                        pass
                            if text_parts:
                                render_messages.append({"role": msg["role"], "content": "\n".join(text_parts)})

                    await send_event("conversation_loaded", {
                        "conversation_id": conv_id,
                        "message_count": len(messages),
                        "model": conv_model or settings.model,
                        "messages": render_messages,
                    })

            elif event_type == "set_system_prompt":
                session_set(session_id, "user_system_prompt", data.get("text", ""))

            elif event_type == "set_model":
                model_id = data.get("model", "")
                if model_id in settings.available_models:
                    session_set(session_id, "active_model", model_id)
                    await send_event("model_changed", {"model": model_id})
                    await sync_session_to_redis(session_id)
                else:
                    await send_event("error", {"message": f"Unknown model: {model_id}"})

    except WebSocketDisconnect:
        logger.info(f"Session {session_id} disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Persist conversation before cleanup
        try:
            await save_conversation(session_id)
        except Exception:
            logger.error("Failed to save conversation on disconnect", exc_info=True)
        if agent_task and not agent_task.done():
            agent_task.cancel()
        await destroy_session_async(session_id)
        session_closed()


async def _handle_user_message(
    session_id: str,
    data: dict[str, Any],
    send_event,
    pending_permissions: dict[str, asyncio.Future],
) -> None:
    """Process a user message through the agentic loop."""
    set_current_session(session_id)
    user_id = session_get(session_id, "user_id") or "default"
    set_current_user(user_id)
    ensure_user_dirs(user_id)

    messages = session_get(session_id, "messages") or []
    messages.append({"role": "user", "content": data["text"]})

    memory_context = load_memory(user_id)
    user_system_prompt = session_get(session_id, "user_system_prompt") or ""

    # Set per-user output directory in the system prompt
    if user_id != "default":
        from core.sandbox import get_workspace
        workspace = get_workspace(user_id)
        user_outputs_dir = workspace.outputs
    else:
        user_outputs_dir = _outputs_dir

    # Skill and command matching
    # Priority: 1) /name -> skill, 2) /name -> command, 3) intent match
    from core.session import set_active_tool_filter
    active_prompt = None
    tool_schemas = get_all_tool_schemas()
    set_active_tool_filter(None)  # Reset tool filter

    msg_stripped = data["text"].strip()
    matched_skill = None
    matched_command = None

    if msg_stripped.startswith("/"):
        slash_name = msg_stripped.split()[0][1:]  # strip leading /
        parts = msg_stripped.split(None, 1)
        args = parts[1] if len(parts) > 1 else ""

        # 1. Try skill first (modern standard)
        matched_skill = match_skill_by_name(slash_name)
        if matched_skill:
            active_prompt = matched_skill.resolve_arguments(args)
        else:
            # 2. Check commands
            matched_command = get_command(slash_name)
            if matched_command:
                active_prompt = matched_command.resolve_arguments(args)
    else:
        # 3. Intent-based skill matching (natural language)
        matched_skill = match_skill_by_intent(data["text"])
        if matched_skill:
            active_prompt = matched_skill.prompt

    # Apply tool filter from matched skill
    if matched_skill and matched_skill.tools:
        filtered = get_tool_schemas(matched_skill.tools)
        if filtered:
            tool_schemas = filtered
            set_active_tool_filter(matched_skill.tools)

    # Notify UI
    if matched_skill:
        await send_event("skill_activated", {
            "name": matched_skill.name,
            "description": matched_skill.description,
            "type": "skill",
        })
    elif matched_command:
        await send_event("skill_activated", {
            "name": matched_command.name,
            "description": matched_command.description,
            "type": "command",
        })

    system = _build_system_prompt(
        active_prompt,
        memory_context or None,
        user_system_prompt or None,
    )

    # ─── Agent callbacks ───
    async def on_text(text: str) -> None:
        await send_event("text_delta", {"text": text})

    async def on_tool_start(name: str, tool_input: dict[str, Any]) -> str:
        tool_id = f"tool_{uuid.uuid4().hex[:8]}"
        await send_event("tool_start", {
            "tool_id": tool_id,
            "name": name,
            "input": tool_input,
        })
        return tool_id

    async def on_tool_end(tool_id: str, result: str) -> None:
        # Detect errors for the UI
        is_error = False
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and "error" in parsed:
                is_error = True
        except (json.JSONDecodeError, TypeError):
            pass

        await send_event("tool_end", {
            "tool_id": tool_id,
            "result": result,
            "is_error": is_error,
        })

        # Auto-render images from bash / python_exec
        await _send_images_if_any(tool_id, result, send_event)
        # Auto-render Plotly charts from create_chart
        # We need the tool name — extract from the result context
        # (tool_id was generated in on_tool_start, we don't have the name here)
        # We'll handle charts in a wrapper instead.

    # Wrap on_tool_start/end to also track names for chart detection
    _tool_names: dict[str, str] = {}

    async def on_tool_start_wrapper(name: str, tool_input: dict[str, Any]) -> str:
        tool_id = await on_tool_start(name, tool_input)
        _tool_names[tool_id] = name
        return tool_id

    async def on_tool_end_wrapper(tool_id: str, result: str) -> None:
        await on_tool_end(tool_id, result)
        tool_name = _tool_names.pop(tool_id, "")
        await _send_chart_if_any(tool_id, tool_name, result, send_event)
        await _send_tables_if_any(tool_id, tool_name, result, send_event)

    async def on_permission_check(name: str, tool_input: dict[str, Any]) -> bool | str:
        if is_blocked(name, tool_input):
            return "BLOCKED: This command is not allowed for safety reasons."
        if is_risky(name, tool_input):
            req_id = f"perm_{uuid.uuid4().hex[:8]}"
            future: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
            pending_permissions[req_id] = future

            await send_event("permission_request", {
                "request_id": req_id,
                "tool_name": name,
                "summary": summarize_tool_call(name, tool_input),
            })

            try:
                allowed = await asyncio.wait_for(future, timeout=120.0)
            except asyncio.TimeoutError:
                return "Timed out waiting for user permission"
            finally:
                pending_permissions.pop(req_id, None)

            return True if allowed else "User denied this action"
        return True

    # Get per-session model override
    active_model = session_get(session_id, "active_model") or None

    try:
        messages = await agent.run(
            messages=messages,
            tools=tool_schemas,
            system=system,
            on_text=on_text,
            on_tool_start=on_tool_start_wrapper,
            on_tool_end=on_tool_end_wrapper,
            on_permission_check=on_permission_check,
            model=active_model,
        )
        session_set(session_id, "messages", messages)
        # Persist conversation after each turn
        conv_id = await save_conversation(session_id)
        # Send usage stats with turn_complete
        usage_data = {}
        try:
            from core.usage import get_user_usage_summary
            _uid = session_get(session_id, "user_id") or "default"
            usage_data = await get_user_usage_summary(_uid)
        except Exception:
            pass
        await send_event("turn_complete", {
            "conversation_id": conv_id,
            "usage": usage_data,
        })
    except Exception as e:
        logger.error(f"Agent error: {e}", exc_info=True)
        await send_event("error", {"message": str(e)})


# ─── Run directly ───
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=True,
    )
