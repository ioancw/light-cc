"""Tests for rate limiting."""

from __future__ import annotations

import pytest
from core.rate_limit import check_rate_limit, reset_limits


@pytest.fixture(autouse=True)
def clean_limits():
    """Reset all rate limits between tests."""
    yield
    reset_limits("testuser")


class TestRateLimit:
    def test_within_limits(self):
        for _ in range(10):
            allowed, reason = check_rate_limit("testuser", "message")
            assert allowed, reason

    def test_exceeds_per_minute_limit(self):
        # Consume all 15 tokens
        for _ in range(15):
            allowed, _ = check_rate_limit("testuser", "message")
            assert allowed

        # 16th should be rejected
        allowed, reason = check_rate_limit("testuser", "message")
        assert not allowed
        assert "too many messages" in reason.lower()

    def test_tool_call_limit(self):
        # Consume all 60 tokens
        for _ in range(60):
            allowed, _ = check_rate_limit("testuser", "tool_call")
            assert allowed

        # 61st should be rejected
        allowed, reason = check_rate_limit("testuser", "tool_call")
        assert not allowed
        assert "tool calls" in reason.lower()

    def test_default_user_not_limited(self):
        """The 'default' user (legacy) should not be rate limited."""
        for _ in range(100):
            allowed, _ = check_rate_limit("default", "message")
            assert allowed

    def test_empty_user_not_limited(self):
        for _ in range(100):
            allowed, _ = check_rate_limit("", "message")
            assert allowed

    def test_reset_limits(self):
        # Exhaust limits
        for _ in range(15):
            check_rate_limit("testuser", "message")
        allowed, _ = check_rate_limit("testuser", "message")
        assert not allowed

        # Reset
        reset_limits("testuser")
        allowed, _ = check_rate_limit("testuser", "message")
        assert allowed

    def test_separate_users(self):
        """Different users should have independent limits."""
        for _ in range(15):
            check_rate_limit("user_a", "message")

        # user_a is exhausted
        allowed, _ = check_rate_limit("user_a", "message")
        assert not allowed

        # user_b should be fine
        allowed, _ = check_rate_limit("user_b", "message")
        assert allowed

        # Cleanup
        reset_limits("user_a")
        reset_limits("user_b")
