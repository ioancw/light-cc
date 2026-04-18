"""FastAPI + WebSocket server for Light CC.

Run with: uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
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
from core.system_prompt import DEFAULT_OUTPUTS_DIR, build_system_prompt
from commands.registry import load_commands
from skills.registry import load_skills
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
from routes.plugins import router as plugins_router
from routes.api_tokens import router as api_tokens_router

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

# ─── FastAPI lifespan (startup + shutdown) ───
@asynccontextmanager
async def lifespan(app: FastAPI):
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
    # alembic/env.py uses asyncio.run() internally, which cannot nest inside
    # this async startup handler's loop — so we push the sync call to a thread.
    def _run_pg_migrations() -> None:
        from sqlalchemy import text, create_engine
        from alembic.config import Config as AlembicConfig
        from alembic import command as alembic_command
        sync_url = settings.database_url.replace("+asyncpg", "")
        sync_engine = create_engine(sync_url)
        with sync_engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(42)"))
            try:
                alembic_cfg = AlembicConfig(str(_PROJECT_ROOT / "alembic.ini"))
                alembic_cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
                alembic_command.upgrade(alembic_cfg, "head")
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(42)"))
                conn.commit()
        sync_engine.dispose()

    def _run_sqlite_migrations() -> None:
        from alembic.config import Config as AlembicConfig
        from alembic import command as alembic_command
        alembic_cfg = AlembicConfig(str(_PROJECT_ROOT / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
        alembic_command.upgrade(alembic_cfg, "head")

    if "postgresql" in settings.database_url:
        try:
            await asyncio.to_thread(_run_pg_migrations)
            logger.info("Alembic migrations applied successfully")
        except Exception as e:
            logger.warning(f"Auto-migration failed (run 'alembic upgrade head' manually): {e}")
    elif "sqlite" in settings.database_url:
        try:
            await asyncio.to_thread(_run_sqlite_migrations)
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

    yield

    # ── shutdown ──
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


# ─── FastAPI app ───
app = FastAPI(title="Light CC", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(admin_router)
app.include_router(files_router)
app.include_router(usage_router)
app.include_router(schedules_router)
app.include_router(agents_router)
app.include_router(memory_router)
app.include_router(plugins_router)
app.include_router(api_tokens_router)
app.mount("/static", StaticFiles(directory=str(_PROJECT_ROOT / "static")), name="static")

_SVELTE_DIST = _PROJECT_ROOT / "frontend" / "dist"
if settings.server.frontend == "svelte":
    if _SVELTE_DIST.exists() and (_SVELTE_DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(_SVELTE_DIST / "assets")), name="svelte-assets")
    else:
        logger.warning("frontend=svelte but frontend/dist/ not found — run 'npm run build' in frontend/")


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
        build_system_prompt=build_system_prompt,
        outputs_dir=DEFAULT_OUTPUTS_DIR,
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
