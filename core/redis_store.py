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


# ── Conversation session state (per-cid) ────────────────────────────

_CONV_PREFIX = "lcc:conv:"
_CONV_TTL = 14400  # 4 hours


def _serialize_conv_state(data: dict[str, Any]) -> dict[str, Any]:
    """Extract JSON-serializable fields from a conversation session dict.

    Non-serializable values (e.g. pandas DataFrames in 'datasets') are
    stripped with a warning so the rest of the state can be persisted.
    """
    slim: dict[str, Any] = {}
    for key, val in data.items():
        if key == "datasets":
            # DataFrames are not JSON-serializable — skip with warning
            if val:
                logger.debug("Skipping %d dataset(s) during Redis serialization (node-affine)", len(val))
            continue
        if key.startswith("_"):
            # Internal keys like _conn_id are local-only
            continue
        try:
            json.dumps(val)
            slim[key] = val
        except (TypeError, ValueError):
            logger.debug("Skipping non-serializable conv key '%s'", key)
    return slim


async def save_conv_session(cid: str, data: dict[str, Any]) -> None:
    """Persist a conversation session to Redis for cross-instance recovery."""
    if not _pool:
        return
    try:
        slim = _serialize_conv_state(data)
        await _pool.setex(
            _CONV_PREFIX + cid,
            _CONV_TTL,
            json.dumps(slim),
        )
    except Exception as e:
        logger.debug(f"Redis save_conv_session failed: {e}")


async def load_conv_session(cid: str) -> dict[str, Any] | None:
    """Load a conversation session from Redis."""
    if not _pool:
        return None
    try:
        raw = await _pool.get(_CONV_PREFIX + cid)
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.debug(f"Redis load_conv_session failed: {e}")
        return None


async def delete_conv_session(cid: str) -> None:
    """Remove a conversation session from Redis."""
    if not _pool:
        return
    try:
        await _pool.delete(_CONV_PREFIX + cid)
    except Exception as e:
        logger.debug(f"Redis delete_conv_session failed: {e}")


# ── Set helpers (for token revocation, etc.) ─────────────────────────

async def set_add(key: str, value: str, ttl: int | None = None) -> None:
    """Add a value to a Redis set. Optionally set TTL on the key."""
    if not _pool:
        return
    try:
        await _pool.sadd(key, value)
        if ttl:
            await _pool.expire(key, ttl)
    except Exception as e:
        logger.debug(f"Redis set_add failed: {e}")


async def set_check(key: str, value: str) -> bool:
    """Check if a value is in a Redis set."""
    if not _pool:
        return False
    try:
        return bool(await _pool.sismember(key, value))
    except Exception as e:
        logger.debug(f"Redis set_check failed: {e}")
        return False


async def set_remove(key: str, value: str) -> None:
    """Remove a value from a Redis set."""
    if not _pool:
        return
    try:
        await _pool.srem(key, value)
    except Exception as e:
        logger.debug(f"Redis set_remove failed: {e}")


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
