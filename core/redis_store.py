"""Redis connection pool and helpers for cross-instance state.

When redis_url is not configured (local dev), all operations gracefully
return None / no-op so the app falls back to in-memory-only sessions.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.config import settings

logger = logging.getLogger(__name__)

_pool = None


async def init_redis() -> None:
    """Initialize the Redis connection pool (no-op if redis_url is unset)."""
    global _pool
    if not settings.redis_url:
        logger.info("Redis not configured — using in-memory sessions only")
        return
    try:
        import redis.asyncio as aioredis
        _pool = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=20,
        )
        await _pool.ping()
        logger.info("Redis connected")
    except Exception as e:
        logger.warning(f"Redis connection failed, falling back to in-memory: {e}")
        _pool = None


async def shutdown_redis() -> None:
    """Close the Redis connection pool."""
    global _pool
    if _pool:
        await _pool.aclose()
        _pool = None


def is_available() -> bool:
    """Check if Redis is connected."""
    return _pool is not None


# ── Key-value helpers with 1-hour TTL ────────────────────────────────

_SESSION_PREFIX = "lcc:session:"
_SESSION_TTL = 3600  # 1 hour


async def save_session_state(session_id: str, data: dict[str, Any]) -> None:
    """Persist serializable session state to Redis."""
    if not _pool:
        return
    try:
        # Only store serializable fields (skip messages — those go to DB)
        slim = {
            "user_id": data.get("user_id", "default"),
            "conversation_id": data.get("conversation_id"),
            "active_model": data.get("active_model"),
            "user_system_prompt": data.get("user_system_prompt", ""),
        }
        await _pool.setex(
            _SESSION_PREFIX + session_id,
            _SESSION_TTL,
            json.dumps(slim),
        )
    except Exception as e:
        logger.debug(f"Redis save_session_state failed: {e}")


async def load_session_state(session_id: str) -> dict[str, Any] | None:
    """Load session state from Redis."""
    if not _pool:
        return None
    try:
        raw = await _pool.get(_SESSION_PREFIX + session_id)
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.debug(f"Redis load_session_state failed: {e}")
        return None


async def delete_session_state(session_id: str) -> None:
    """Remove session state from Redis."""
    if not _pool:
        return
    try:
        await _pool.delete(_SESSION_PREFIX + session_id)
    except Exception as e:
        logger.debug(f"Redis delete_session_state failed: {e}")


# ── Pub/Sub for cross-instance notifications ─────────────────────────

_NOTIFY_CHANNEL = "lcc:notifications"


async def publish_notification(user_id: str, message: str) -> None:
    """Publish a notification to all app instances."""
    if not _pool:
        return
    try:
        payload = json.dumps({"user_id": user_id, "message": message})
        await _pool.publish(_NOTIFY_CHANNEL, payload)
    except Exception as e:
        logger.debug(f"Redis publish failed: {e}")
