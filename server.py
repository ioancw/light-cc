"""FastAPI + WebSocket server for Light CC.

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
from core.rate_limit import check_rate_limit, check_ws_connect
from core.session import (
    create_session,
    destroy_session_async,
    fork_conversation,
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
from routes.schedules import router as schedules_router
from commands.registry import get_command, list_commands, load_commands
from core.project_config import load_project_config
from core.rules import load_rules, get_active_rules
from skills.registry import list_skills, load_skills, match_skill_by_intent, match_skill_by_name
from tools.registry import get_all_tool_schemas, get_tool_schemas

from core.hooks import load_hooks, fire_hooks, has_hooks
from core.models import resolve_dynamic_content
from core.plugin_loader import get_plugin_loader
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

BASE_SYSTEM_PROMPT = f"""You are Light CC, a helpful AI assistant with access to the local machine. \
You can execute shell commands, run Python scripts, read/write files, and perform data processing, \
visualization, and general tasks. You have real access to the file system — use your tools.

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
    project_config: str | None = None,
    rules_text: str | None = None,
) -> str:
    parts = [BASE_SYSTEM_PROMPT]
    if project_config:
        parts.append(f"\n## Project Instructions\n{project_config}")
    if rules_text:
        parts.append(f"\n## Project Rules\n{rules_text}")
    if user_system_prompt:
        parts.append(f"\n## User Instructions\n{user_system_prompt}")
    if skill_prompt:
        parts.append(f"\n## Active Skill\n{skill_prompt}")
    if memory_context:
        parts.append(
            f"\n## Your Memory\nThe following are things you remember about this user:\n{memory_context}"
        )
    # List available skills (user-invocable and auto-activated)
    # Skills with disable_model_invocation=True are hidden from Claude's context
    # entirely — they remain callable via /name but Claude won't see them.
    skills = list_skills()
    visible_skills = [s for s in skills if s.user_invocable and not s.disable_model_invocation]
    auto_activated = [s for s in skills if not s.disable_model_invocation]

    if visible_skills:
        lines = []
        for s in visible_skills:
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
app.include_router(schedules_router)
app.mount("/static", StaticFiles(directory=str(_PROJECT_ROOT / "static")), name="static")

# Svelte frontend: serve Vite build assets from frontend/dist/assets/
_SVELTE_DIST = _PROJECT_ROOT / "frontend" / "dist"
if settings.server.frontend == "svelte":
    if _SVELTE_DIST.exists() and (_SVELTE_DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(_SVELTE_DIST / "assets")), name="svelte-assets")
    else:
        logger.warning("frontend=svelte but frontend/dist/ not found — run 'npm run build' in frontend/")


@app.on_event("startup")
async def startup():
    setup_telemetry()
    await init_db()
    await init_redis()
    await init_job_queue()
    load_hooks(settings.hooks)

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

    # Start the scheduler
    from core.scheduler import start_scheduler
    await start_scheduler()


@app.on_event("shutdown")
async def shutdown():
    # Unload plugins and disconnect MCP servers
    from core.plugin_loader import get_plugin_loader
    await get_plugin_loader().unload_all()
    from core.mcp_client import get_mcp_manager
    await get_mcp_manager().disconnect_all()

    from core.scheduler import stop_scheduler
    await stop_scheduler()

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
    if settings.server.frontend == "svelte" and (_SVELTE_DIST / "index.html").exists():
        return FileResponse(str(_SVELTE_DIST / "index.html"))
    return FileResponse(str(_PROJECT_ROOT / "static" / "loom.html"))


@app.get("/login")
async def login_page():
    if settings.server.frontend == "svelte" and (_SVELTE_DIST / "index.html").exists():
        return FileResponse(str(_SVELTE_DIST / "index.html"))
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


def _rebuild_render_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild frontend-renderable messages from stored API messages.

    Extracts text, tool_use, and tool_result blocks so the frontend can
    reconstruct tool call panels with images/charts.
    """
    # First pass: collect tool_result by tool_use_id
    tool_results: dict[str, dict[str, Any]] = {}
    for msg in messages:
        content = msg.get("content", "")
        if msg.get("role") == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id")
                    if tid:
                        tool_results[tid] = block

    # Second pass: build render messages
    render_messages: list[dict[str, Any]] = []
    for msg in messages:
        try:
            content = msg.get("content", "")

            if msg["role"] == "user":
                if isinstance(content, str):
                    render_messages.append({"role": "user", "content": content})
                # Skip user messages that are just tool_result arrays
                elif isinstance(content, list):
                    text_parts = [b.get("text", "") for b in content
                                  if isinstance(b, dict) and b.get("type") == "text"]
                    if text_parts:
                        render_messages.append({"role": "user", "content": "\n".join(text_parts)})

            elif msg["role"] == "assistant":
                if isinstance(content, str):
                    render_messages.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    text_parts = []
                    tool_calls = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            tc_id = block.get("id", "")
                            tc: dict[str, Any] = {
                                "id": tc_id,
                                "name": block.get("name", "tool"),
                                "input": block.get("input", {}),
                                "status": "done",
                            }
                            # Pair with result
                            tr = tool_results.get(tc_id)
                            if tr:
                                result_content = tr.get("content", "")
                                tc["result"] = result_content
                                tc["is_error"] = tr.get("is_error", False)
                                if tc["is_error"]:
                                    tc["status"] = "error"
                                # Detect images from file paths in stdout
                                try:
                                    tc["images"] = _extract_images_from_result(result_content)
                                except Exception:
                                    tc["images"] = []
                                # Detect Plotly charts
                                try:
                                    chart = _extract_chart_from_result(result_content)
                                    if chart:
                                        tc["chart"] = chart
                                except Exception:
                                    pass
                            tool_calls.append(tc)

                    rm: dict[str, Any] = {
                        "role": "assistant",
                        "content": "\n".join(text_parts) if text_parts else "",
                    }
                    if tool_calls:
                        rm["toolCalls"] = tool_calls
                    render_messages.append(rm)
        except Exception as e:
            logger.warning(f"Skipping malformed message during rebuild: {e}")
            # Still include the message as plain text if possible
            try:
                content = msg.get("content", "")
                if isinstance(content, str) and content:
                    render_messages.append({"role": msg.get("role", "assistant"), "content": content})
            except Exception:
                pass

    return render_messages


def _extract_images_from_result(result) -> list[dict[str, str]]:
    """Extract base64-encoded images from file paths found in tool result stdout."""
    images = []
    try:
        # Handle list content blocks (API format)
        if isinstance(result, list):
            text_parts = [b.get("text", "") for b in result if isinstance(b, dict) and b.get("type") == "text"]
            result = "\n".join(text_parts)
        parsed = json.loads(result) if isinstance(result, str) else result
        stdout = parsed.get("stdout", "") if isinstance(parsed, dict) else (result if isinstance(result, str) else "")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return images

    if not stdout:
        return images

    for line in stdout.strip().splitlines():
        line = line.strip()
        try:
            p = Path(line)
            if not p.exists():
                continue
            ext = p.suffix.lower()
            mime = _IMAGE_EXTENSIONS.get(ext)
            if mime and p.stat().st_size < 10 * 1024 * 1024:
                import base64
                data = base64.b64encode(p.read_bytes()).decode("ascii")
                images.append({"mime": mime, "data": data, "name": p.name})
        except (OSError, ValueError):
            continue
    return images


def _extract_chart_from_result(result) -> dict[str, str] | None:
    """Extract Plotly chart JSON from file paths found in tool result stdout."""
    try:
        # Handle list content blocks (API format)
        if isinstance(result, list):
            text_parts = [b.get("text", "") for b in result if isinstance(b, dict) and b.get("type") == "text"]
            result = "\n".join(text_parts)
        parsed = json.loads(result) if isinstance(result, str) else result
        stdout = parsed.get("stdout", "") if isinstance(parsed, dict) else (result if isinstance(result, str) else "")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None

    if not stdout:
        return None

    for line in stdout.strip().splitlines():
        line = line.strip()
        try:
            p = Path(line)
            if p.exists() and p.name.endswith(".plotly.json"):
                chart_json = p.read_text(encoding="utf-8")
                json.loads(chart_json)  # validate
                return {"title": p.stem.replace(".plotly", ""), "plotlyJson": chart_json}
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    return None


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
    # Rate limit connections per IP before accepting
    client_ip = ws.client.host if ws.client else "unknown"
    allowed, reason = check_ws_connect(client_ip)
    if not allowed:
        await ws.close(code=4029, reason=reason)
        return
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

    # Fire SessionStart hooks
    if has_hooks("SessionStart"):
        await fire_hooks("SessionStart", {"session_id": session_id, "user_id": user_id})

    # Pending permission futures: request_id -> Future[bool]
    pending_permissions: dict[str, asyncio.Future] = {}

    # Agent task reference (so receive loop isn't blocked)
    agent_task: asyncio.Task | None = None

    async def send_event(event_type: str, data: dict[str, Any]) -> None:
        try:
            await ws.send_json({"type": event_type, "data": data})
        except Exception:
            pass  # connection may have closed

    # Register this session for scheduler notifications
    from core.scheduler import register_user_sender, unregister_user_sender
    register_user_sender(user_id, send_event)

    # Register notification callbacks for background tasks and task list updates
    from tools.background import set_notification_callback
    from tools.tasks import set_task_notify_callback

    async def bg_notify(task_id: str, message: str) -> None:
        await send_event("notification", {"task_id": task_id, "message": message})

    async def task_notify(event_type: str, data: dict[str, Any]) -> None:
        await send_event(event_type, data)

    set_notification_callback(bg_notify)
    set_task_notify_callback(task_notify)

    # Send connected event with user info + available skills/commands
    skills_for_client = [
        {"name": s.name, "description": s.description, "argument_hint": s.argument_hint}
        for s in list_skills() if s.user_invocable
    ] + [
        {"name": c.name, "description": c.description, "argument_hint": c.argument_hint}
        for c in list_commands()
    ] + [
        {"name": "context", "description": "Show context window usage breakdown", "argument_hint": ""},
        {"name": "plugin", "description": "Install, list, update, or uninstall plugins", "argument_hint": "install|list|update|uninstall <name-or-url>"},
        {"name": "schedule", "description": "Create, list, enable, disable, or delete scheduled agent tasks", "argument_hint": "create|list|enable|disable|delete|runs|run"},
    ]
    await send_event("connected", {
        "session_id": session_id,
        "model": settings.model,
        "available_models": settings.available_models,
        "skills": skills_for_client,
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

            elif event_type == "cancel_generation":
                if agent_task and not agent_task.done():
                    agent_task.cancel()

            elif event_type == "clear_conversation":
                # Save current conversation before clearing
                await save_conversation(session_id)
                session_set(session_id, "messages", [])
                session_set(session_id, "conversation_id", None)

            elif event_type == "resume_conversation":
                conv_id = data.get("conversation_id", "")
                if conv_id:
                    try:
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
                        # Build renderable messages with tool calls for the frontend
                        render_messages = _rebuild_render_messages(messages)

                        # Count context tokens so the UI shows accurate window usage
                        ctx_tokens = 0
                        try:
                            from core.context import count_message_tokens
                            ctx_tokens = await count_message_tokens(messages, "", None)
                        except Exception:
                            pass

                        await send_event("conversation_loaded", {
                            "conversation_id": conv_id,
                            "message_count": len(messages),
                            "model": conv_model or settings.model,
                            "messages": render_messages,
                            "context_tokens": ctx_tokens,
                        })
                    except Exception as e:
                        logger.error(f"Failed to load conversation {conv_id}: {e}", exc_info=True)
                        await send_event("error", {"message": f"Failed to load conversation: {e}"})

            elif event_type == "revert_checkpoint":
                from core.checkpoints import revert_last, revert_to_turn, list_checkpoints
                turn = data.get("turn")
                if turn is not None:
                    reverted = revert_to_turn(session_id, int(turn))
                else:
                    reverted = revert_last(session_id)
                await send_event("checkpoint_reverted", {
                    "reverted_files": reverted,
                    "remaining": len(list_checkpoints(session_id)),
                })

            elif event_type == "list_checkpoints":
                from core.checkpoints import list_checkpoints
                cps = list_checkpoints(session_id)
                await send_event("checkpoints", {
                    "entries": [
                        {"file_path": cp.file_path, "turn": cp.turn, "size": cp.size, "existed": cp.existed}
                        for cp in cps
                    ],
                })

            elif event_type == "fork_conversation":
                conv_id = data.get("conversation_id", "")
                if conv_id:
                    try:
                        new_conv_id, messages = await fork_conversation(conv_id, user_id)
                        session_set(session_id, "messages", messages)
                        session_set(session_id, "conversation_id", new_conv_id)
                        await send_event("conversation_forked", {
                            "source_conversation_id": conv_id,
                            "conversation_id": new_conv_id,
                            "message_count": len(messages),
                        })
                    except ValueError as e:
                        await send_event("error", {"message": str(e)})

            elif event_type == "set_system_prompt":
                session_set(session_id, "user_system_prompt", data.get("text", ""))

            elif event_type == "set_permission_mode":
                from core.permission_modes import PermissionMode
                mode_str = data.get("mode", "")
                try:
                    mode = PermissionMode(mode_str)
                    session_set(session_id, "permission_mode", mode.value)
                    await send_event("permission_mode_changed", {"mode": mode.value})
                except ValueError:
                    await send_event("error", {"message": f"Unknown permission mode: {mode_str}"})

            elif event_type == "cycle_permission_mode":
                from core.permission_modes import PermissionMode
                current = PermissionMode(session_get(session_id, "permission_mode") or "default")
                new_mode = current.next()
                session_set(session_id, "permission_mode", new_mode.value)
                await send_event("permission_mode_changed", {"mode": new_mode.value})

            elif event_type == "generate_title":
                conv_id = session_get(session_id, "conversation_id")
                msgs = session_get(session_id, "messages") or []
                if msgs and len(msgs) >= 2:
                    title = await _generate_title(msgs[:4])
                    if title and conv_id:
                        from core.db_models import Conversation
                        from sqlalchemy import update as sql_update
                        db = await get_db()
                        try:
                            await db.execute(
                                sql_update(Conversation).where(Conversation.id == conv_id).values(title=title)
                            )
                            await db.commit()
                        finally:
                            await db.close()
                    await send_event("title_updated", {"conversation_id": conv_id or "", "title": title or ""})

            elif event_type == "summarize_context":
                msgs = session_get(session_id, "messages") or []
                if len(msgs) < 6:
                    await send_event("error", {"message": "Not enough messages to summarize"})
                else:
                    try:
                        summary = await _summarize_messages(msgs)
                        # Replace old messages with summary + recent messages
                        recent = msgs[-4:]  # keep last 2 exchanges
                        new_msgs = [{"role": "user", "content": f"[Previous conversation summary: {summary}]"}] + recent
                        session_set(session_id, "messages", new_msgs)
                        await send_event("context_summarized", {
                            "original_count": len(msgs),
                            "new_count": len(new_msgs),
                            "summary": summary,
                        })
                    except Exception as e:
                        logger.error(f"Context summarization failed: {e}", exc_info=True)
                        await send_event("error", {"message": f"Summarization failed: {e}"})

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
        # Fire SessionEnd hooks
        if has_hooks("SessionEnd"):
            try:
                await fire_hooks("SessionEnd", {"session_id": session_id, "user_id": user_id})
            except Exception:
                pass
        # Unregister scheduler notifications
        unregister_user_sender(user_id, send_event)
        # Clean up checkpoints
        from core.checkpoints import clear_checkpoints
        clear_checkpoints(session_id)
        await destroy_session_async(session_id)
        session_closed()


async def _handle_plugin_command(args: str) -> str:
    """Handle /plugin install|list|uninstall commands."""
    import shutil
    import subprocess as _sp

    parts = args.strip().split(None, 1)
    subcommand = parts[0].lower() if parts else ""
    sub_args = parts[1].strip() if len(parts) > 1 else ""
    loader = get_plugin_loader()
    plugins_dir = _PROJECT_ROOT / "plugins"
    plugins_dir.mkdir(exist_ok=True)

    if subcommand == "install":
        if not sub_args:
            return "Usage: `/plugin install <github-url-or-path>`"

        source = sub_args
        # Local path
        local_path = Path(source).expanduser()
        if local_path.exists() and local_path.is_dir():
            manifest = local_path / ".claude-plugin" / "plugin.json"
            if not manifest.exists():
                return f"No `.claude-plugin/plugin.json` found in `{source}`"
            info = await loader.load_plugin(local_path)
            if info:
                return (
                    f"**Plugin installed from local path**\n\n"
                    f"- **Name:** {info.name}\n"
                    f"- **Version:** {info.version}\n"
                    f"- **Description:** {info.description}\n"
                    f"- **Skills:** {', '.join(info.skills) or 'none'}\n"
                    f"- **Commands:** {', '.join(info.commands) or 'none'}\n"
                    f"- **MCP servers:** {', '.join(info.mcp_servers) or 'none'}"
                )
            return f"Failed to load plugin from `{source}`"

        # Git URL — clone into plugins/
        # Accept github shorthand: owner/repo
        url = source
        if "/" in source and not source.startswith(("http://", "https://", "git@")):
            url = f"https://github.com/{source}.git"
        elif source.startswith(("http://", "https://")) and not source.endswith(".git"):
            url = source + ".git"

        # Derive directory name from URL
        repo_name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        target_dir = plugins_dir / repo_name

        if target_dir.exists():
            # Pull latest
            try:
                proc = _sp.run(
                    ["git", "-C", str(target_dir), "pull", "--ff-only"],
                    capture_output=True, text=True, timeout=60,
                )
                if proc.returncode != 0:
                    return f"Failed to update `{repo_name}`:\n```\n{proc.stderr.strip()}\n```"
            except Exception as e:
                return f"Failed to update `{repo_name}`: {e}"
        else:
            # Clone
            try:
                proc = _sp.run(
                    ["git", "clone", "--depth", "1", url, str(target_dir)],
                    capture_output=True, text=True, timeout=120,
                )
                if proc.returncode != 0:
                    return f"Failed to clone `{url}`:\n```\n{proc.stderr.strip()}\n```"
            except Exception as e:
                return f"Failed to clone `{url}`: {e}"

        # Validate manifest exists
        manifest = target_dir / ".claude-plugin" / "plugin.json"
        if not manifest.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
            return f"Cloned `{repo_name}` but no `.claude-plugin/plugin.json` found. Removed."

        # Load the plugin
        info = await loader.load_plugin(target_dir)
        if not info:
            return f"Cloned `{repo_name}` but failed to load plugin."

        return (
            f"**Plugin installed**\n\n"
            f"- **Name:** {info.name}\n"
            f"- **Version:** {info.version}\n"
            f"- **Description:** {info.description}\n"
            f"- **Skills:** {', '.join(info.skills) or 'none'}\n"
            f"- **Commands:** {', '.join(info.commands) or 'none'}\n"
            f"- **MCP servers:** {', '.join(info.mcp_servers) or 'none'}"
        )

    elif subcommand == "list":
        plugins = loader.list_plugins()
        if not plugins:
            return "No plugins installed."
        lines = ["**Installed plugins**\n"]
        for p in plugins:
            lines.append(
                f"- **{p.name}** v{p.version} — {p.description or 'no description'}\n"
                f"  Skills: {', '.join(p.skills) or 'none'} | "
                f"Commands: {', '.join(p.commands) or 'none'} | "
                f"MCP: {', '.join(p.mcp_servers) or 'none'}"
            )
        return "\n".join(lines)

    elif subcommand in ("uninstall", "remove"):
        if not sub_args:
            return "Usage: `/plugin uninstall <name>`"

        name = sub_args
        info = loader.get_plugin(name)
        if not info:
            return f"Plugin `{name}` not found. Use `/plugin list` to see installed plugins."

        plugin_path = info.path
        await loader.unload_plugin(name)

        # Remove directory if it's inside our plugins folder
        try:
            if plugin_path.is_relative_to(plugins_dir):
                shutil.rmtree(plugin_path, ignore_errors=True)
                return f"Plugin `{name}` uninstalled and removed from disk."
            else:
                return f"Plugin `{name}` unloaded. Directory `{plugin_path}` left in place (external path)."
        except Exception as e:
            return f"Plugin `{name}` unloaded but failed to remove directory: {e}"

    elif subcommand == "update":
        if not sub_args:
            return "Usage: `/plugin update <name>`"

        name = sub_args
        info = loader.get_plugin(name)
        if not info:
            return f"Plugin `{name}` not found."

        plugin_path = info.path
        if not (plugin_path / ".git").exists():
            return f"Plugin `{name}` is not a git repo, cannot update."

        # Pull latest
        try:
            proc = _sp.run(
                ["git", "-C", str(plugin_path), "pull", "--ff-only"],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode != 0:
                return f"Failed to update `{name}`:\n```\n{proc.stderr.strip()}\n```"
        except Exception as e:
            return f"Failed to update `{name}`: {e}"

        # Reload
        await loader.unload_plugin(name)
        new_info = await loader.load_plugin(plugin_path)
        if not new_info:
            return f"Updated `{name}` but failed to reload."

        return (
            f"**Plugin updated**\n\n"
            f"- **Name:** {new_info.name}\n"
            f"- **Version:** {new_info.version}\n"
            f"- **Description:** {new_info.description}\n"
            f"- **Skills:** {', '.join(new_info.skills) or 'none'}\n"
            f"- **Commands:** {', '.join(new_info.commands) or 'none'}\n"
            f"- **MCP servers:** {', '.join(new_info.mcp_servers) or 'none'}"
        )

    else:
        return (
            "**Plugin commands**\n\n"
            "- `/plugin install <github-url-or-owner/repo>` — install from GitHub\n"
            "- `/plugin install <local-path>` — install from local directory\n"
            "- `/plugin list` — show installed plugins\n"
            "- `/plugin update <name>` — pull latest and reload\n"
            "- `/plugin uninstall <name>` — remove a plugin"
        )


async def _handle_schedule_command(args: str, user_id: str) -> str:
    """Handle /schedule create|list|enable|disable|delete|runs|run commands."""
    from croniter import croniter
    from core.schedule_crud import (
        create_schedule, list_schedules, resolve_schedule,
        update_schedule, delete_schedule, get_schedule_runs,
        trigger_schedule_now,
    )

    parts = args.strip().split(None, 1)
    subcommand = parts[0].lower() if parts else ""
    sub_args = parts[1].strip() if len(parts) > 1 else ""

    if subcommand == "create":
        # Parse: "name" "cron" prompt text
        # Or: name cron prompt text
        import shlex
        try:
            tokens = shlex.split(sub_args)
        except ValueError:
            tokens = sub_args.split()

        if len(tokens) < 3:
            return (
                "Usage: `/schedule create <name> <cron> <prompt>`\n\n"
                "Examples:\n"
                "- `/schedule create \"Morning Brief\" \"0 9 * * *\" Summarize overnight market moves`\n"
                "- `/schedule create \"Hourly Check\" \"0 * * * *\" Check portfolio risk metrics`"
            )

        name = tokens[0]
        cron_expr = tokens[1]
        prompt = " ".join(tokens[2:])

        if not croniter.is_valid(cron_expr):
            return (
                f"Invalid cron expression: `{cron_expr}`\n\n"
                "Format: `minute hour day month weekday`\n"
                "Examples: `0 9 * * *` (daily 9am), `*/30 * * * *` (every 30 min), `0 9 * * 1` (Mondays 9am)"
            )

        try:
            sched = await create_schedule(user_id, name, cron_expr, prompt)
        except Exception as e:
            return f"Failed to create schedule: {e}"

        next_run = sched.next_run_at.strftime("%Y-%m-%d %H:%M UTC") if sched.next_run_at else "unknown"
        return (
            f"**Schedule created**\n\n"
            f"- **Name:** {sched.name}\n"
            f"- **Cron:** `{sched.cron_expression}`\n"
            f"- **Prompt:** {sched.prompt}\n"
            f"- **Next run:** {next_run}"
        )

    elif subcommand == "list":
        schedules = await list_schedules(user_id)
        if not schedules:
            return "No schedules. Use `/schedule create` to add one."
        lines = ["**Scheduled tasks**\n"]
        for s in schedules:
            status = "enabled" if s.enabled else "disabled"
            next_run = s.next_run_at.strftime("%Y-%m-%d %H:%M UTC") if s.next_run_at else "n/a"
            last_run = s.last_run_at.strftime("%Y-%m-%d %H:%M UTC") if s.last_run_at else "never"
            lines.append(
                f"- **{s.name}** ({status})\n"
                f"  Cron: `{s.cron_expression}` | Next: {next_run} | Last: {last_run}\n"
                f"  Prompt: {s.prompt[:100]}{'...' if len(s.prompt) > 100 else ''}"
            )
        return "\n".join(lines)

    elif subcommand == "enable":
        if not sub_args:
            return "Usage: `/schedule enable <name>`"
        sched = await resolve_schedule(sub_args, user_id)
        if not sched:
            return f"Schedule `{sub_args}` not found."
        await update_schedule(sched.id, user_id, enabled=True)
        return f"Schedule `{sched.name}` enabled."

    elif subcommand == "disable":
        if not sub_args:
            return "Usage: `/schedule disable <name>`"
        sched = await resolve_schedule(sub_args, user_id)
        if not sched:
            return f"Schedule `{sub_args}` not found."
        await update_schedule(sched.id, user_id, enabled=False)
        return f"Schedule `{sched.name}` disabled."

    elif subcommand == "delete":
        if not sub_args:
            return "Usage: `/schedule delete <name>`"
        sched = await resolve_schedule(sub_args, user_id)
        if not sched:
            return f"Schedule `{sub_args}` not found."
        await delete_schedule(sched.id, user_id)
        return f"Schedule `{sched.name}` deleted."

    elif subcommand == "runs":
        if not sub_args:
            return "Usage: `/schedule runs <name>`"
        sched = await resolve_schedule(sub_args, user_id)
        if not sched:
            return f"Schedule `{sub_args}` not found."
        runs = await get_schedule_runs(sched.id, user_id, limit=10)
        if not runs:
            return f"No runs yet for `{sched.name}`."
        lines = [f"**Recent runs for {sched.name}**\n"]
        for r in runs:
            started = r.started_at.strftime("%Y-%m-%d %H:%M UTC")
            duration = ""
            if r.finished_at:
                secs = (r.finished_at - r.started_at).total_seconds()
                duration = f" ({secs:.0f}s)"
            preview = ""
            if r.result:
                preview = f"\n  Result: {r.result[:120]}{'...' if len(r.result) > 120 else ''}"
            elif r.error:
                preview = f"\n  Error: {r.error[:120]}{'...' if len(r.error) > 120 else ''}"
            lines.append(f"- **{r.status}** at {started}{duration}{preview}")
        return "\n".join(lines)

    elif subcommand == "run":
        if not sub_args:
            return "Usage: `/schedule run <name>`"
        sched = await resolve_schedule(sub_args, user_id)
        if not sched:
            return f"Schedule `{sub_args}` not found."
        await trigger_schedule_now(sched.id, user_id)
        return f"Triggered `{sched.name}`. You'll get a notification when it completes."

    else:
        return (
            "**Schedule commands**\n\n"
            "- `/schedule create <name> <cron> <prompt>` -- create a scheduled task\n"
            "- `/schedule list` -- show all schedules\n"
            "- `/schedule enable <name>` -- enable a schedule\n"
            "- `/schedule disable <name>` -- disable a schedule\n"
            "- `/schedule delete <name>` -- delete a schedule\n"
            "- `/schedule runs <name>` -- show recent run history\n"
            "- `/schedule run <name>` -- trigger immediately\n\n"
            "**Cron format:** `minute hour day month weekday`\n\n"
            "| Pattern | Meaning |\n"
            "|---|---|\n"
            "| `0 9 * * *` | Daily at 9:00 AM |\n"
            "| `*/30 * * * *` | Every 30 minutes |\n"
            "| `0 9 * * 1-5` | Weekdays at 9:00 AM |\n"
            "| `0 9 * * 1` | Mondays at 9:00 AM |\n"
            "| `0 */6 * * *` | Every 6 hours |\n"
            "| `0 0 1 * *` | First of each month |\n\n"
            "**Examples**\n\n"
            "```\n"
            "/schedule create \"Morning Brief\" \"0 9 * * 1-5\" Summarize overnight market moves, key economic data releases, and any notable earnings\n"
            "/schedule create \"Risk Report\" \"0 */6 * * *\" Run VaR and CVaR on current portfolio positions and flag any breaches\n"
            "/schedule create \"Paper Digest\" \"0 8 * * 1\" Search for new research papers on stochastic volatility and summarize findings\n"
            "/schedule create \"Data Refresh\" \"0 7 * * 1-5\" /analyze portfolio.csv\n"
            "/schedule create \"Weekly Charts\" \"0 9 * * 1\" /chart weekly performance across all tracked indices\n"
            "```\n\n"
            "Prompts starting with `/` will use the matching skill or command. "
            "Plain text prompts run a general-purpose agent with full tool access."
        )


async def _generate_title(messages: list[dict[str, Any]]) -> str:
    """Generate a short conversation title from the first few messages."""
    from core.client import get_client
    client = get_client()
    text_parts = []
    for m in messages[:4]:
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
        if isinstance(content, str):
            text_parts.append(f"{m.get('role', 'unknown')}: {content[:200]}")
    prompt = (
        "Summarize this conversation in 4-6 words for a sidebar title. "
        "Return only the title, no quotes or punctuation.\n\n"
        + "\n".join(text_parts)
    )
    try:
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=30,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.warning(f"Title generation failed: {e}")
        return ""


async def _summarize_messages(messages: list[dict[str, Any]]) -> str:
    """Summarize older messages to reduce context size."""
    from core.client import get_client
    client = get_client()

    # Extract text from messages to summarize (all except the last 4)
    to_summarize = messages[:-4]
    text_parts = []
    for m in to_summarize:
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
        if isinstance(content, str) and content.strip():
            role = m.get("role", "unknown")
            text_parts.append(f"{role}: {content[:500]}")

    combined = "\n".join(text_parts)[:8000]
    prompt = (
        "Summarize this conversation history concisely, preserving key facts, "
        "decisions, code snippets mentioned, and context needed to continue the conversation. "
        "Be thorough but compact.\n\n" + combined
    )
    resp = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


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

    # Fire PromptSubmit hooks
    if has_hooks("PromptSubmit"):
        await fire_hooks("PromptSubmit", {"text": data["text"], "user_id": user_id})

    messages.append({"role": "user", "content": data["text"]})

    memory_context = load_memory(user_id)
    user_system_prompt = session_get(session_id, "user_system_prompt") or ""

    # Load project config (CLAUDE.md) and rules — cached per session
    project_config = session_get(session_id, "project_config")
    if project_config is None:
        project_dir = Path(settings.project_dir) if settings.project_dir else Path.cwd()
        project_config = load_project_config(project_dir)
        session_set(session_id, "project_config", project_config)
    project_rules = session_get(session_id, "project_rules")
    if project_rules is None:
        project_dir = Path(settings.project_dir) if settings.project_dir else Path.cwd()
        project_rules = load_rules(project_dir)
        session_set(session_id, "project_rules", project_rules)

    # Compute active rules based on files touched so far
    active_files: list[str] = session_get(session_id, "active_files") or []
    rules_text = get_active_rules(project_rules, active_files) if project_rules else ""

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

        # Built-in: /context — show context window breakdown
        if slash_name == "context":
            from core.context import get_context_breakdown
            breakdown = await get_context_breakdown(
                messages, system="(pending)",
                tools=tool_schemas,
                project_config=session_get(session_id, "project_config") or "",
                rules_text=get_active_rules(session_get(session_id, "project_rules") or [], session_get(session_id, "active_files") or []),
                memory_context=load_memory(user_id),
            )
            lines = [
                "**Context Window Usage**",
                f"- System prompt: ~{breakdown['system_prompt_tokens']:,} tokens",
                f"- Project config (CLAUDE.md): ~{breakdown['project_config_tokens']:,} tokens",
                f"- Project rules: ~{breakdown['rules_tokens']:,} tokens",
                f"- Memory: ~{breakdown['memory_tokens']:,} tokens",
                f"- Skills: ~{breakdown['skill_tokens']:,} tokens",
                f"- Tool schemas: ~{breakdown['tools_tokens']:,} tokens",
                f"- Messages: ~{breakdown['messages_tokens']:,} tokens",
                f"- **Total: ~{breakdown['total_tokens']:,} / {breakdown['max_tokens']:,} ({breakdown['usage_pct']}%)**",
            ]
            await send_event("text_delta", {"text": "\n".join(lines)})
            await send_event("response_end", {})
            return

        # Built-in: /plugin — install, list, uninstall plugins
        if slash_name == "plugin":
            result = await _handle_plugin_command(args)
            await send_event("text_delta", {"text": result})
            # Refresh skills list in client after install/uninstall/update
            sub = args.strip().split()[0].lower() if args.strip() else ""
            if sub in ("install", "uninstall", "remove", "update"):
                refreshed = [
                    {"name": s.name, "description": s.description, "argument_hint": s.argument_hint}
                    for s in list_skills() if s.user_invocable
                ] + [
                    {"name": c.name, "description": c.description, "argument_hint": c.argument_hint}
                    for c in list_commands()
                ] + [
                    {"name": "context", "description": "Show context window usage breakdown", "argument_hint": ""},
                    {"name": "plugin", "description": "Install, list, update, or uninstall plugins", "argument_hint": "install|list|update|uninstall <name-or-url>"},
                ]
                await send_event("skills_updated", {"skills": refreshed})
            await send_event("turn_complete", {})
            return

        # Built-in: /schedule -- manage scheduled agent tasks
        if slash_name == "schedule":
            result = await _handle_schedule_command(args, user_id)
            await send_event("text_delta", {"text": result})
            await send_event("turn_complete", {})
            return

        # 1. Try skill first (modern standard)
        matched_skill = match_skill_by_name(slash_name)
        if matched_skill:
            active_prompt = matched_skill.resolve_arguments(args, session_id=session_id)
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

    # Handle skill context=fork: run in isolated subagent instead of main loop
    if matched_skill and matched_skill.context == "fork":
        from tools.subagent import run_subagent
        await send_event("text_delta", {"text": ""})  # signal response start
        fork_system = active_prompt or matched_skill.prompt
        fork_prompt = data["text"]
        if msg_stripped.startswith("/"):
            parts = msg_stripped.split(None, 1)
            fork_prompt = parts[1] if len(parts) > 1 else matched_skill.description
        result = await run_subagent(
            prompt=fork_prompt,
            system=fork_system,
            tool_names=matched_skill.tools or None,
        )
        await send_event("text_delta", {"text": result})
        await send_event("response_end", {})
        messages.append({"role": "assistant", "content": [{"type": "text", "text": result}]})
        session_set(session_id, "messages", messages)
        await save_conversation(session_id)
        return

    # Resolve !`command` dynamic content in skill/command prompts
    if active_prompt:
        active_prompt = await resolve_dynamic_content(active_prompt)

    system = _build_system_prompt(
        active_prompt,
        memory_context or None,
        user_system_prompt or None,
        project_config or None,
        rules_text or None,
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
        # Track active files for path-scoped rules
        _file_keys = ("file_path", "path", "pattern")
        for key in _file_keys:
            val = tool_input.get(key)
            if val and isinstance(val, str):
                af = session_get(session_id, "active_files") or []
                if val not in af:
                    af.append(val)
                    session_set(session_id, "active_files", af)
                break
        return tool_id

    async def on_tool_end_wrapper(tool_id: str, result: str) -> None:
        await on_tool_end(tool_id, result)
        tool_name = _tool_names.pop(tool_id, "")
        await _send_chart_if_any(tool_id, tool_name, result, send_event)
        await _send_tables_if_any(tool_id, tool_name, result, send_event)

    async def on_permission_check(name: str, tool_input: dict[str, Any]) -> bool | str:
        from core.permission_modes import PermissionMode, check_permission

        mode_str = session_get(session_id, "permission_mode") or "default"
        mode = PermissionMode(mode_str)
        result = check_permission(mode, name, tool_input)

        if result is True:
            return True
        if isinstance(result, str):
            return result
        # result is None — ask the user
        req_id = f"perm_{uuid.uuid4().hex[:8]}"
        future: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
        pending_permissions[req_id] = future

        await send_event("permission_request", {
            "request_id": req_id,
            "tool_name": name,
            "summary": summarize_tool_call(name, tool_input),
            "permission_mode": mode_str,
        })

        try:
            allowed = await asyncio.wait_for(future, timeout=120.0)
        except asyncio.TimeoutError:
            return "Timed out waiting for user permission"
        finally:
            pending_permissions.pop(req_id, None)

        return True if allowed else "User denied this action"

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
        context_tokens = 0
        try:
            from core.usage import get_user_usage_summary
            from core.context import count_message_tokens
            _uid = session_get(session_id, "user_id") or "default"
            usage_data = await get_user_usage_summary(_uid)
            context_tokens = await count_message_tokens(messages, system, tool_schemas)
        except Exception:
            pass
        await send_event("turn_complete", {
            "conversation_id": conv_id,
            "usage": usage_data,
            "context_tokens": context_tokens,
        })
    except asyncio.CancelledError:
        logger.info("Agent generation cancelled by user")
        await save_conversation(session_id)
        await send_event("generation_cancelled", {})
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
