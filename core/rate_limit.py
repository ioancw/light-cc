"""Token-bucket rate limiter — per-user limits for messages and tool calls.

Uses Redis (sliding window counter) when available for cross-replica
consistency, falls back to in-memory token buckets otherwise.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── In-memory token bucket (fallback) ──────────────────────────────────

@dataclass
class _Bucket:
    """A simple token bucket."""
    capacity: int
    refill_rate: float  # tokens per second
    tokens: float = 0.0
    last_refill: float = field(default_factory=time.monotonic)

    def __post_init__(self):
        self.tokens = float(self.capacity)

    def consume(self) -> bool:
        """Try to consume one token. Returns True if allowed."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    @property
    def retry_after(self) -> float:
        """Seconds until the next token is available."""
        if self.tokens >= 1.0:
            return 0.0
        return (1.0 - self.tokens) / self.refill_rate


# Per-user rate limit state (in-memory fallback)
_user_limits: dict[str, dict[str, _Bucket]] = {}

# Default limits (configurable via config.yaml later)
_LIMITS = {
    "message": {"capacity": 15, "refill_rate": 15 / 60, "window": 60},       # 15 per minute
    "message_hourly": {"capacity": 200, "refill_rate": 200 / 3600, "window": 3600},  # 200 per hour
    "tool_call": {"capacity": 60, "refill_rate": 60 / 60, "window": 60},     # 60 per minute
}

# Per-tool rate limits — tools not listed here use the default "tool_call" bucket
_TOOL_LIMITS = {
    "Bash": {"capacity": 30, "refill_rate": 30 / 60, "window": 60},          # 30 per minute
    "PythonExec": {"capacity": 30, "refill_rate": 30 / 60, "window": 60},    # 30 per minute
    "WebFetch": {"capacity": 20, "refill_rate": 20 / 60, "window": 60},      # 20 per minute
}


# ── Redis sliding window helpers ───────────────────────────────────────

def _redis_available() -> bool:
    from core.redis_store import is_available
    return is_available()


async def _redis_check(key: str, capacity: int, window: int) -> tuple[bool, float]:
    """Sliding window counter via Redis MULTI/EXEC. Returns (allowed, retry_after)."""
    from core.redis_store import _pool
    if not _pool:
        return True, 0.0
    try:
        now = time.time()
        window_start = now - window

        pipe = _pool.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, window + 1)
        results = await pipe.execute()

        current_count = results[1]
        if current_count >= capacity:
            # Over limit -- remove the entry we just added
            await _pool.zrem(key, str(now))
            # Estimate retry_after from oldest entry
            oldest = await _pool.zrange(key, 0, 0, withscores=True)
            if oldest:
                retry = window - (now - oldest[0][1])
                return False, max(retry, 1.0)
            return False, float(window)
        return True, 0.0
    except Exception as e:
        logger.debug(f"Redis rate limit check failed, falling back to in-memory: {e}")
        return True, 0.0


def _get_buckets(user_id: str) -> dict[str, _Bucket]:
    """Get or create rate limit buckets for a user."""
    if user_id not in _user_limits:
        _user_limits[user_id] = {
            name: _Bucket(capacity=cfg["capacity"], refill_rate=cfg["refill_rate"])
            for name, cfg in _LIMITS.items()
        }
    return _user_limits[user_id]


def check_rate_limit(user_id: str, action: str, *, tool_name: str | None = None) -> tuple[bool, str]:
    """Check if a user action is within rate limits (in-memory only, sync).

    Args:
        user_id: The user's ID.
        action: "message" or "tool_call".
        tool_name: Optional tool name for per-tool rate limiting.

    Returns:
        (allowed, reason) -- allowed is True if OK, reason is empty string if allowed.
    """
    if not user_id or user_id == "default":
        return True, ""

    buckets = _get_buckets(user_id)

    if action == "message":
        minute_bucket = buckets["message"]
        hourly_bucket = buckets["message_hourly"]

        if not minute_bucket.consume():
            retry = minute_bucket.retry_after
            return False, f"Rate limit exceeded: too many messages. Try again in {retry:.0f}s."

        if not hourly_bucket.consume():
            retry = hourly_bucket.retry_after
            return False, f"Hourly message limit reached. Try again in {retry:.0f}s."

        return True, ""

    elif action == "tool_call":
        if tool_name and tool_name in _TOOL_LIMITS:
            tool_key = f"tool:{tool_name}"
            if tool_key not in buckets:
                cfg = _TOOL_LIMITS[tool_name]
                buckets[tool_key] = _Bucket(capacity=cfg["capacity"], refill_rate=cfg["refill_rate"])
            tool_bucket = buckets[tool_key]
            if not tool_bucket.consume():
                retry = tool_bucket.retry_after
                return False, f"Rate limit exceeded for {tool_name}. Try again in {retry:.0f}s."

        bucket = buckets.get("tool_call")
        if bucket and not bucket.consume():
            retry = bucket.retry_after
            return False, f"Rate limit exceeded: too many tool calls. Try again in {retry:.0f}s."
        return True, ""

    return True, ""


async def check_rate_limit_async(user_id: str, action: str, *, tool_name: str | None = None) -> tuple[bool, str]:
    """Async rate limit check -- uses Redis when available, falls back to in-memory."""
    if not user_id or user_id == "default":
        return True, ""

    if not _redis_available():
        return check_rate_limit(user_id, action, tool_name=tool_name)

    if action == "message":
        key = f"lcc:rl:{user_id}:message"
        cfg = _LIMITS["message"]
        allowed, retry = await _redis_check(key, cfg["capacity"], cfg["window"])
        if not allowed:
            return False, f"Rate limit exceeded: too many messages. Try again in {retry:.0f}s."

        key_h = f"lcc:rl:{user_id}:message_hourly"
        cfg_h = _LIMITS["message_hourly"]
        allowed, retry = await _redis_check(key_h, cfg_h["capacity"], cfg_h["window"])
        if not allowed:
            return False, f"Hourly message limit reached. Try again in {retry:.0f}s."

        return True, ""

    elif action == "tool_call":
        if tool_name and tool_name in _TOOL_LIMITS:
            cfg_t = _TOOL_LIMITS[tool_name]
            key_t = f"lcc:rl:{user_id}:tool:{tool_name}"
            allowed, retry = await _redis_check(key_t, cfg_t["capacity"], cfg_t["window"])
            if not allowed:
                return False, f"Rate limit exceeded for {tool_name}. Try again in {retry:.0f}s."

        cfg = _LIMITS["tool_call"]
        key = f"lcc:rl:{user_id}:tool_call"
        allowed, retry = await _redis_check(key, cfg["capacity"], cfg["window"])
        if not allowed:
            return False, f"Rate limit exceeded: too many tool calls. Try again in {retry:.0f}s."
        return True, ""

    return True, ""


# Per-IP WebSocket connection rate limits
_WS_LIMIT = {"capacity": 5, "refill_rate": 5 / 60, "window": 60}
_ws_buckets: dict[str, _Bucket] = {}


def check_ws_connect(ip: str) -> tuple[bool, str]:
    """Check if an IP can open a new WebSocket connection."""
    if ip not in _ws_buckets:
        _ws_buckets[ip] = _Bucket(
            capacity=_WS_LIMIT["capacity"],
            refill_rate=_WS_LIMIT["refill_rate"],
        )
    bucket = _ws_buckets[ip]
    if not bucket.consume():
        return False, f"Too many connections. Try again in {bucket.retry_after:.0f}s."
    return True, ""


def reset_limits(user_id: str) -> None:
    """Reset rate limits for a user (e.g., admin action)."""
    _user_limits.pop(user_id, None)
