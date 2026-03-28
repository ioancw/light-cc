"""Abstracted job queue — uses arq (Redis) when available, asyncio fallback.

Usage:
    from core.job_queue import enqueue
    await enqueue("background_agent", prompt="...", task_name="...")
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from core.config import settings

logger = logging.getLogger(__name__)

_arq_pool = None

# Registry of job functions: name -> async callable
_job_registry: dict[str, Callable[..., Awaitable[Any]]] = {}


def register_job(name: str, func: Callable[..., Awaitable[Any]]) -> None:
    """Register an async function as a named job."""
    _job_registry[name] = func


async def init_job_queue() -> None:
    """Initialize the arq connection pool (no-op if Redis is unavailable)."""
    global _arq_pool
    if not settings.redis_url:
        logger.info("Job queue: using asyncio (no Redis)")
        return
    try:
        from arq import create_pool
        from arq.connections import RedisSettings

        redis_settings = RedisSettings.from_dsn(settings.redis_url)
        _arq_pool = await create_pool(redis_settings)
        logger.info("Job queue: arq connected to Redis")
    except Exception as e:
        logger.warning(f"Job queue: arq init failed, falling back to asyncio: {e}")
        _arq_pool = None


async def shutdown_job_queue() -> None:
    """Close the arq connection pool."""
    global _arq_pool
    if _arq_pool:
        await _arq_pool.aclose()
        _arq_pool = None


async def enqueue(job_name: str, **kwargs: Any) -> str | None:
    """Enqueue a job. Uses arq if available, otherwise asyncio.create_task.

    Returns a job ID string, or None if the job was started via asyncio.
    """
    if _arq_pool:
        try:
            job = await _arq_pool.enqueue_job(job_name, **kwargs)
            return job.job_id if job else None
        except Exception as e:
            logger.warning(f"arq enqueue failed, falling back to asyncio: {e}")

    # Asyncio fallback
    func = _job_registry.get(job_name)
    if func:
        asyncio.create_task(func(**kwargs))
    else:
        logger.error(f"Job '{job_name}' not found in registry")
    return None


def get_arq_worker_functions() -> list:
    """Return arq-compatible function list for the worker process.

    Usage in a worker entry point:
        from core.job_queue import get_arq_worker_functions
        class WorkerSettings:
            functions = get_arq_worker_functions()
            redis_settings = RedisSettings.from_dsn(...)
    """
    from arq import func as arq_func
    return [arq_func(fn, name=name) for name, fn in _job_registry.items()]
