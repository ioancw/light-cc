"""FastAPI + WebSocket server for Light CC.

Run with: uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.database import get_db, init_db, shutdown_db
from core.job_queue import init_job_queue, shutdown_job_queue
from core.redis_store import init_redis, shutdown_redis
from core.log_context import setup_logging
from core.telemetry import setup_telemetry
from core.hooks import load_hooks
from commands.registry import list_commands, load_commands
from skills.registry import list_skills, load_skills
from handlers.commands import set_project_root
from handlers.ws_router import websocket_endpoint

from routes.auth import router as auth_router
from routes.conversations import router as conversations_router
from routes.admin import router as admin_router
from routes.files import router as files_router
from routes.usage import router as usage_router
from routes.schedules import router as schedules_router

import tools  # noqa: F401 — triggers tool registration

logger = logging.getLogger(__name__)

# ─── Load skills and commands at startup ───
_PROJECT_ROOT = Path(__file__).resolve().parent
set_project_root(_PROJECT_ROOT)

for skills_dir in settings.paths.skills_dirs:
    resolved = Path(skills_dir).expanduser()
    if not resolved.is_absolute():
        resolved = _PROJECT_ROOT / resolved
    load_skills(resolved)

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

Tool usage rules:
- web_fetch is ONLY for external HTTP/HTTPS URLs on the public internet. NEVER use web_fetch \
for local files (file://), localhost, or 127.0.0.1 — it will be blocked. Use read_file or \
bash_exec to access local files and local services.
- Scheduled tasks are managed via the /schedule command, NOT via the OS task scheduler. \
Use `/schedule list` to view (shows short IDs), `/schedule delete <name|id>` to remove, \
`/schedule enable|disable <name|id>` to toggle, `/schedule run <name|id>` to trigger immediately. \
You can reference schedules by name or short ID prefix. Never suggest Windows Task Scheduler, \
cron, or other OS-level scheduling — all scheduling is handled internally.
- For local API endpoints or services, use bash_exec with curl, not web_fetch.

Model: {settings.model}
"""


def _build_system_prompt(
    skill_prompt: str | None = None,
    memory_context: str | None = None,
    user_system_prompt: str | None = None,
    project_config: str | None = None,
    rules_text: str | None = None,
    outputs_dir: str | None = None,
) -> str:
    base = BASE_SYSTEM_PROMPT
    if outputs_dir:
        base = base.replace(str(_outputs_dir), str(outputs_dir))
    parts = [base]
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

_SVELTE_DIST = _PROJECT_ROOT / "frontend" / "dist"
if settings.server.frontend == "svelte":
    if _SVELTE_DIST.exists() and (_SVELTE_DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(_SVELTE_DIST / "assets")), name="svelte-assets")
    else:
        logger.warning("frontend=svelte but frontend/dist/ not found — run 'npm run build' in frontend/")


@app.on_event("startup")
async def startup():
    setup_logging()
    setup_telemetry()
    await init_db()
    await init_redis()
    await init_job_queue()
    load_hooks(settings.hooks)

    from core.plugin_loader import get_plugin_loader
    loader = get_plugin_loader()
    for plugins_dir in settings.paths.plugins_dirs:
        resolved = Path(plugins_dir).expanduser()
        if not resolved.is_absolute():
            resolved = _PROJECT_ROOT / resolved
        await loader.load_plugins_from(resolved)

    project_mcp = _PROJECT_ROOT / ".mcp.json"
    if project_mcp.exists():
        from core.mcp_client import load_mcp_config
        await load_mcp_config(str(project_mcp))

    from core.scheduler import start_scheduler
    await start_scheduler()


@app.on_event("shutdown")
async def shutdown():
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
    """Health check -- verifies DB and Redis connectivity."""
    checks: dict[str, str] = {}
    try:
        from sqlalchemy import text
        db = await get_db()
        await db.execute(text("SELECT 1"))
        await db.close()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
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
    if settings.server.frontend == "svelte" and (_SVELTE_DIST / "index.html").exists():
        return FileResponse(str(_SVELTE_DIST / "index.html"))
    return FileResponse(str(_PROJECT_ROOT / "static" / "loom.html"))


@app.get("/login")
async def login_page():
    if settings.server.frontend == "svelte" and (_SVELTE_DIST / "index.html").exists():
        return FileResponse(str(_SVELTE_DIST / "index.html"))
    return FileResponse(str(_PROJECT_ROOT / "static" / "auth.html"))


# ─── WebSocket endpoint ───
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await websocket_endpoint(
        ws,
        build_system_prompt=_build_system_prompt,
        outputs_dir=_outputs_dir,
    )


# ─── Run directly ───
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=True,
    )
