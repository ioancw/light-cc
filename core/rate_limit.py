"""Token-bucket rate limiter — per-user limits for messages and tool calls."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


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


# Per-user rate limit state
_user_limits: dict[str, dict[str, _Bucket]] = {}

# Default limits (configurable via config.yaml later)
_LIMITS = {
    "message": {"capacity": 15, "refill_rate": 15 / 60},       # 15 per minute
    "message_hourly": {"capacity": 200, "refill_rate": 200 / 3600},  # 200 per hour
    "tool_call": {"capacity": 60, "refill_rate": 60 / 60},     # 60 per minute
}


def _get_buckets(user_id: str) -> dict[str, _Bucket]:
    """Get or create rate limit buckets for a user."""
    if user_id not in _user_limits:
        _user_limits[user_id] = {
            name: _Bucket(capacity=cfg["capacity"], refill_rate=cfg["refill_rate"])
            for name, cfg in _LIMITS.items()
        }
    return _user_limits[user_id]


def check_rate_limit(user_id: str, action: str) -> tuple[bool, str]:
    """Check if a user action is within rate limits.

    Args:
        user_id: The user's ID.
        action: "message" or "tool_call".

    Returns:
        (allowed, reason) — allowed is True if OK, reason is empty string if allowed.
    """
    if not user_id or user_id == "default":
        return True, ""

    buckets = _get_buckets(user_id)

    if action == "message":
        # Check both per-minute and per-hour buckets
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
        bucket = buckets.get("tool_call")
        if bucket and not bucket.consume():
            retry = bucket.retry_after
            return False, f"Rate limit exceeded: too many tool calls. Try again in {retry:.0f}s."
        return True, ""

    return True, ""


# Per-IP WebSocket connection rate limits
_WS_LIMIT = {"capacity": 5, "refill_rate": 5 / 60}  # 5 connections per minute per IP
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
