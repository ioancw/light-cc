"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings
from core.db_models import Base

logger = logging.getLogger(__name__)

# 30s command timeout for asyncpg so a stuck DB connection can't hang the
# event loop indefinitely. SQLite/aiosqlite doesn't support this arg, so only
# set it when we're talking to Postgres.
_engine_kwargs: dict = {"echo": False}
if "postgresql" in settings.database_url or "postgres" in settings.database_url:
    _engine_kwargs["connect_args"] = {"command_timeout": 30}

_engine = create_async_engine(settings.database_url, **_engine_kwargs)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Initialize the database.

    For SQLite (dev): uses create_all for convenience.
    For PostgreSQL (prod): expects Alembic migrations to be run separately.
    """
    # Instrument SQLAlchemy with OpenTelemetry if available
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        SQLAlchemyInstrumentor().instrument(engine=_engine.sync_engine)
        logger.info("SQLAlchemy OpenTelemetry instrumentation enabled")
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"SQLAlchemy instrumentation failed: {e}")

    if "sqlite" in settings.database_url:
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database initialised (create_all): %s", settings.database_url)
    else:
        logger.info("Database engine ready (use 'alembic upgrade head' for migrations): %s", settings.database_url)


async def get_db() -> AsyncSession:
    """Return a new async session. Caller must close it."""
    return _session_factory()


async def shutdown_db() -> None:
    """Dispose the engine (call on app shutdown)."""
    await _engine.dispose()
