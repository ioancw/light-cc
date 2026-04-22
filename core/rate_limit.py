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
    # External trigger surface: caps how fast an API caller (API token or JWT)
    # can kick off agent runs. Cascaded minute/hour/day buckets -- the daily
    # one acts as a crude per-user quota to bound runaway cost.
    "agent_run": {"capacity": 10, "refill_rate": 10 / 60, "window": 60},           # 10 per minute
    "agent_run_hourly": {"capacity": 100, "refill_rate": 100 / 3600, "window": 3600},  # 100 per hour
    "agent_run_daily": {"capacity": 500, "refill_rate": 500 / 86400, "window": 86400}, # 500 per day
    # API token creation: low burst tolerance; most users create tokens rarely.
    "token_create": {"capacity": 5, "refill_rate": 5 / 3600, "window": 3600},     # 5 per hour
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

    elif action == "agent_run":
        minute_bucket = buckets["agent_run"]
        hourly_bucket = buckets["agent_run_hourly"]
        daily_bucket = buckets["agent_run_daily"]
        if not minute_bucket.consume():
            retry = minute_bucket.retry_after
            return False, f"Rate limit exceeded: too many agent runs. Try again in {retry:.0f}s."
        if not hourly_bucket.consume():
            retry = hourly_bucket.retry_after
            return False, f"Hourly agent run limit reached. Try again in {retry:.0f}s."
        if not daily_bucket.consume():
            retry = daily_bucket.retry_after
            return False, f"Daily agent run quota reached. Try again in {retry / 3600:.1f}h."
        return True, ""

    elif action == "token_create":
        bucket = buckets["token_create"]
        if not bucket.consume():
            retry = bucket.retry_after
            return False, f"Token creation limit reached. Try again in {retry / 60:.0f}m."
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

    elif action == "agent_run":
        for sub, fmt in (
            ("agent_run", "Rate limit exceeded: too many agent runs. Try again in {retry:.0f}s."),
            ("agent_run_hourly", "Hourly agent run limit reached. Try again in {retry:.0f}s."),
            ("agent_run_daily", "Daily agent run quota reached. Try again in {retry_hours:.1f}h."),
        ):
            cfg = _LIMITS[sub]
            key = f"lcc:rl:{user_id}:{sub}"
            allowed, retry = await _redis_check(key, cfg["capacity"], cfg["window"])
            if not allowed:
                return False, fmt.format(retry=retry, retry_hours=retry / 3600)
        return True, ""

    elif action == "token_create":
        cfg = _LIMITS["token_create"]
        key = f"lcc:rl:{user_id}:token_create"
        allowed, retry = await _redis_check(key, cfg["capacity"], cfg["window"])
        if not allowed:
            return False, f"Token creation limit reached. Try again in {retry / 60:.0f}m."
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


# ── Auth-endpoint rate limit ──────────────────────────────────────────
#
# Keyed on sha256(email_lower)+IP so a single attacker can't lock out a real
# user (IP component) and one user can't bypass by rotating IPs (email
# component). Hard-lock after 10 failures in 15 min with exponential-ish
# Retry-After hints. Uses Redis when available for cross-replica correctness.

import hashlib

_AUTH_CAPACITY = 10
_AUTH_WINDOW = 900  # 15 minutes
_auth_attempts_mem: dict[str, list[float]] = {}


def _auth_key_id(email: str, ip: str) -> str:
    """Identifier combining hashed email and IP — avoids storing email in key."""
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]
    return f"{digest}:{ip}"


async def check_auth_rate_limit(email: str, ip: str) -> tuple[bool, float]:
    """Check whether this (email, IP) pair may attempt another auth call.

    Returns (allowed, retry_after_seconds). When not allowed, retry_after is a
    hint derived from the oldest attempt in the window. The limit is
    intentionally shared across login/register/refresh — the goal is to bound
    total damage per actor regardless of which endpoint they probe.
    """
    ident = _auth_key_id(email or "anonymous", ip or "unknown")
    now = time.time()

    if _redis_available():
        key = f"lcc:auth_rl:{ident}"
        allowed, retry = await _redis_check(key, _AUTH_CAPACITY, _AUTH_WINDOW)
        return allowed, retry

    # In-memory fallback — single-process only.
    cutoff = now - _AUTH_WINDOW
    history = [t for t in _auth_attempts_mem.get(ident, []) if t > cutoff]
    if len(history) >= _AUTH_CAPACITY:
        retry = _AUTH_WINDOW - (now - history[0])
        _auth_attempts_mem[ident] = history
        return False, max(retry, 1.0)
    history.append(now)
    _auth_attempts_mem[ident] = history
    return True, 0.0


def _client_ip(request) -> str:
    """Extract the real client IP, honoring X-Forwarded-For's last hop.

    Caddy (per the project Caddyfile) sets X-Forwarded-For to the client IP;
    in multi-proxy setups we pick the LAST value since earlier hops are
    attacker-controlled.
    """
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"
