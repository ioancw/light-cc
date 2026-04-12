"""FastAPI + WebSocket server for Light CC.

Run with: uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import logging
from pathlib import Path

import fastapi
from fastapi import FastAPI, Request, WebSocket
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
from routes.agents import router as agents_router
from routes.memory import router as memory_router

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
- Prior tool results (web_fetch, python_exec, etc.) are in the conversation history. \
When the user asks follow-up questions, check your prior tool results before claiming \
you have no data. If you previously fetched or processed data, reuse it or run python_exec \
to query it — do not ask the user to re-provide it.
- For data analysis follow-ups (filtering, counting, aggregating), use python_exec to \
compute the answer rather than trying to parse raw text in context.

Tool selection guide (use the right tool for the job):
- Read a file: use Read (not bash cat/head/tail)
- Edit a file: use Edit for targeted changes, Write only for new files or complete rewrites
- Search file contents: use Grep (not bash grep/rg)
- Find files by name/pattern: use Glob (not bash find/ls)
- Run Python code: use PythonExec (not bash python -c) — avoids shell quoting issues
- Fetch a web page: use WebFetch (external URLs only, never localhost)
- Search the web: use WebSearch, then WebFetch to read full pages from results
- Run shell commands (git, curl, npm, etc.): use Bash
- Multi-step complex tasks: use Agent to spawn a sub-agent
- Iterative quality improvement: use EvalOptimize (generator-evaluator loop)
- Data analysis: use LoadData to load files, then QueryData for pandas operations, \
or CreateChart for quick visualizations
When multiple tools could work, prefer the specialized tool over Bash — specialized tools \
provide better structured output and are safer (sandboxed, validated).

Tool usage rules:
- WebFetch is ONLY for external HTTP/HTTPS URLs on the public internet. NEVER use WebFetch \
for local files (file://), localhost, or 127.0.0.1 — it will be blocked. Use Read or \
Bash with curl to access local files and local services.
- Scheduled tasks are managed via the /schedule command, NOT via the OS task scheduler. \
Use `/schedule list` to view (shows short IDs), `/schedule delete <name|id>` to remove, \
`/schedule enable|disable <name|id>` to toggle, `/schedule run <name|id>` to trigger immediately. \
You can reference schedules by name or short ID prefix. Never suggest Windows Task Scheduler, \
cron, or other OS-level scheduling — all scheduling is handled internally.
- For local API endpoints or services, use Bash with curl, not WebFetch.

Error handling:
- If a tool returns an error, read the error message carefully before retrying.
- If a file doesn't exist, check the path with Glob before assuming it was deleted.
- If Edit fails with "not found", verify the exact content with Read first.
- If WebFetch fails, try WebSearch to find an alternative URL.
- Do not retry the same failing command more than twice — diagnose the issue first.

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
app.include_router(agents_router)
app.include_router(memory_router)
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

    # Instrument FastAPI with OpenTelemetry if available
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
        logger.info("FastAPI OpenTelemetry instrumentation enabled")
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"FastAPI instrumentation failed: {e}")

    # Auto-run Alembic migrations. On PostgreSQL we take an advisory lock so
    # multi-replica deploys don't race. On SQLite we just run the upgrade —
    # single-process, no lock needed.
    if "postgresql" in settings.database_url:
        try:
            from sqlalchemy import text, create_engine
            sync_url = settings.database_url.replace("+asyncpg", "")
            sync_engine = create_engine(sync_url)
            with sync_engine.connect() as conn:
                # pg_advisory_lock with a fixed key prevents concurrent migrations
                conn.execute(text("SELECT pg_advisory_lock(42)"))
                try:
                    from alembic.config import Config as AlembicConfig
                    from alembic import command as alembic_command
                    alembic_cfg = AlembicConfig(str(_PROJECT_ROOT / "alembic.ini"))
                    alembic_cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
                    alembic_command.upgrade(alembic_cfg, "head")
                    logger.info("Alembic migrations applied successfully")
                finally:
                    conn.execute(text("SELECT pg_advisory_unlock(42)"))
                    conn.commit()
            sync_engine.dispose()
        except Exception as e:
            logger.warning(f"Auto-migration failed (run 'alembic upgrade head' manually): {e}")
    elif "sqlite" in settings.database_url:
        try:
            from alembic.config import Config as AlembicConfig
            from alembic import command as alembic_command
            alembic_cfg = AlembicConfig(str(_PROJECT_ROOT / "alembic.ini"))
            alembic_cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
            alembic_command.upgrade(alembic_cfg, "head")
            logger.info("Alembic migrations applied successfully (sqlite)")
        except Exception as e:
            logger.warning(f"Auto-migration failed (run 'alembic upgrade head' manually): {e}")

    await init_db()
    await init_redis()
    await init_job_queue()
    # Register background jobs so the asyncio fallback can find them by name.
    import core.agent_runner  # noqa: F401 — registers "execute_agent_run"
    import core.memory_extractor  # noqa: F401 — registers "extract_memories_from_conversation"
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

    # Sync YAML-defined agents into the DB for every existing user, so
    # agent definitions committed to the repo show up in AgentPanel
    # automatically. New users get their copy when they sign up.
    try:
        from core.agent_loader import sync_agents_to_db
        from core.database import get_db
        from core.db_models import User
        from sqlalchemy import select as _select

        user_rows: list[str] = []
        _db = await get_db()
        try:
            _res = await _db.execute(_select(User.id))
            user_rows = list(_res.scalars().all())
        finally:
            await _db.close()

        for agents_dir in settings.paths.agents_dirs:
            resolved = Path(agents_dir).expanduser()
            if not resolved.is_absolute():
                resolved = _PROJECT_ROOT / resolved
            if not resolved.exists():
                continue
            total = 0
            for uid in user_rows:
                total += await sync_agents_to_db(resolved, uid)
            logger.info(f"Agent YAML sync: {total} agent(s) upserted from {resolved}")
    except Exception as e:
        logger.warning(f"Agent YAML sync failed: {e}")

    from core.scheduler import start_scheduler
    await start_scheduler()

    from core.session import start_session_flush
    await start_session_flush()

    from core.sandbox_exec import check_sandbox_warnings
    check_sandbox_warnings()


@app.on_event("shutdown")
async def shutdown():
    from core.session import stop_session_flush
    await stop_session_flush()

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
    """Full health check -- verifies DB and Redis connectivity."""
    from fastapi.responses import JSONResponse
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

    # Active session and memory info
    import psutil
    try:
        proc = psutil.Process()
        checks["memory_mb"] = str(round(proc.memory_info().rss / 1024 / 1024, 1))
    except Exception:
        checks["memory_mb"] = "unknown"

    try:
        from core.session import _connections
        checks["active_sessions"] = str(len(_connections))
    except Exception:
        checks["active_sessions"] = "unknown"

    healthy = checks["database"] == "ok"
    status_code = 200 if healthy else 503
    return JSONResponse({"status": "healthy" if healthy else "unhealthy", "checks": checks}, status_code=status_code)


@app.get("/health/live")
async def health_live():
    """Liveness probe -- always returns 200 if the process is running."""
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready():
    """Readiness probe -- returns 200 only if DB and Redis are reachable."""
    from fastapi.responses import JSONResponse
    try:
        from sqlalchemy import text
        db = await get_db()
        await db.execute(text("SELECT 1"))
        await db.close()
    except Exception as e:
        return JSONResponse({"status": "not_ready", "reason": f"database: {e}"}, status_code=503)

    try:
        from core.redis_store import is_available, _pool
        if _pool:
            await _pool.ping()
    except Exception as e:
        return JSONResponse({"status": "not_ready", "reason": f"redis: {e}"}, status_code=503)

    return {"status": "ready"}


@app.get("/metrics")
async def metrics(request: Request):
    """Prometheus metrics endpoint. Restricted to localhost unless metrics_public is set."""
    client_ip = request.client.host if request.client else ""
    is_local = client_ip in ("127.0.0.1", "::1", "localhost")
    metrics_public = getattr(settings.server, "metrics_public", False)
    if not is_local and not metrics_public:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Forbidden"}, status_code=403)
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
