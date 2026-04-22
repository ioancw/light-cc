"""Auth-endpoint rate limit tests (S6).

Covers the shared limiter in core.rate_limit.check_auth_rate_limit and its
wiring into /api/auth/{login,register,refresh} via _enforce_auth_rate_limit.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _reset_mem_store():
    """Clear in-memory auth attempt history between tests."""
    from core import rate_limit as rl
    rl._auth_attempts_mem.clear()
    yield
    rl._auth_attempts_mem.clear()


@pytest_asyncio.fixture
async def auth_api(monkeypatch):
    """FastAPI client with the real /api/auth router, DB stubbed to always fail login."""
    monkeypatch.setattr("core.config.settings.redis_url", None)

    from routes.auth import router as auth_router

    # Force the DB to return "no user" so login always 401s — we're testing
    # the rate limiter itself, not the happy-path credential check.
    async def _no_user(*args, **kwargs):
        return None

    monkeypatch.setattr("routes.auth.authenticate_user", _no_user)

    # Register path uses get_user_by_email; returning a truthy object makes it
    # short-circuit on "already registered". That's enough to count attempts
    # without touching the DB.
    monkeypatch.setattr("routes.auth.get_user_by_email", AsyncMock(return_value=object()))

    # Stub out the DB session factory so /register doesn't try to open a real
    # connection before the rate-limit check fires.
    class _FakeDB:
        def add(self, *a, **kw): pass
        async def commit(self): pass
        async def refresh(self, *a, **kw): pass
        async def close(self): pass

    @asynccontextmanager
    async def _get_db():
        yield _FakeDB()

    monkeypatch.setattr("routes.auth.get_db", _get_db)

    app = FastAPI()
    app.include_router(auth_router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestAuthRateLimitUnit:
    @pytest.mark.asyncio
    async def test_allows_under_capacity(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.redis_url", None)
        from core.rate_limit import check_auth_rate_limit

        for _ in range(10):
            allowed, retry = await check_auth_rate_limit("a@example.com", "1.2.3.4")
            assert allowed is True
            assert retry == 0.0

    @pytest.mark.asyncio
    async def test_blocks_at_capacity(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.redis_url", None)
        from core.rate_limit import check_auth_rate_limit

        for _ in range(10):
            await check_auth_rate_limit("a@example.com", "1.2.3.4")
        allowed, retry = await check_auth_rate_limit("a@example.com", "1.2.3.4")
        assert allowed is False
        assert retry >= 1.0

    @pytest.mark.asyncio
    async def test_different_email_same_ip_not_locked(self, monkeypatch):
        """Lockout for user A's email must not affect user B from the same IP."""
        monkeypatch.setattr("core.config.settings.redis_url", None)
        from core.rate_limit import check_auth_rate_limit

        for _ in range(10):
            await check_auth_rate_limit("attacker@example.com", "1.2.3.4")
        allowed, _ = await check_auth_rate_limit("attacker@example.com", "1.2.3.4")
        assert allowed is False

        # Different email component → different key → clean slate.
        allowed, _ = await check_auth_rate_limit("victim@example.com", "1.2.3.4")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_same_email_different_ip_not_locked(self, monkeypatch):
        """Rotating IPs shouldn't bypass — but the combined key gives each IP its own bucket."""
        monkeypatch.setattr("core.config.settings.redis_url", None)
        from core.rate_limit import check_auth_rate_limit

        for _ in range(10):
            await check_auth_rate_limit("target@example.com", "1.1.1.1")
        allowed, _ = await check_auth_rate_limit("target@example.com", "1.1.1.1")
        assert allowed is False

        allowed, _ = await check_auth_rate_limit("target@example.com", "2.2.2.2")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_email_normalized(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.redis_url", None)
        from core.rate_limit import check_auth_rate_limit, _auth_key_id

        assert _auth_key_id("A@Example.COM", "1.2.3.4") == _auth_key_id("a@example.com", "1.2.3.4")

        for _ in range(10):
            await check_auth_rate_limit("a@example.com", "9.9.9.9")
        allowed, _ = await check_auth_rate_limit("A@Example.COM", "9.9.9.9")
        assert allowed is False


class TestClientIPExtraction:
    def test_xff_last_hop_used(self):
        from core.rate_limit import _client_ip

        class _Req:
            headers = {"x-forwarded-for": "1.1.1.1, 2.2.2.2, 3.3.3.3"}
            client = type("C", (), {"host": "10.0.0.1"})()

        # Last hop — the one Caddy actually verified — wins.
        assert _client_ip(_Req()) == "3.3.3.3"

    def test_falls_back_to_request_client(self):
        from core.rate_limit import _client_ip

        class _Req:
            headers = {}
            client = type("C", (), {"host": "10.0.0.7"})()

        assert _client_ip(_Req()) == "10.0.0.7"

    def test_no_client_no_xff(self):
        from core.rate_limit import _client_ip

        class _Req:
            headers = {}
            client = None

        assert _client_ip(_Req()) == "unknown"


class TestAuthEndpointLockout:
    @pytest.mark.asyncio
    async def test_login_eleventh_attempt_returns_429(self, auth_api):
        for i in range(10):
            r = await auth_api.post(
                "/api/auth/login",
                json={"email": "victim@example.com", "password": "nope"},
            )
            assert r.status_code == 401, f"attempt {i} should have been 401, got {r.status_code}"

        r = await auth_api.post(
            "/api/auth/login",
            json={"email": "victim@example.com", "password": "nope"},
        )
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        assert int(r.headers["Retry-After"]) >= 1

    @pytest.mark.asyncio
    async def test_register_counts_against_same_bucket(self, auth_api):
        """Attacker probing /register shouldn't get a fresh 10-attempt budget."""
        for _ in range(10):
            r = await auth_api.post(
                "/api/auth/login",
                json={"email": "shared@example.com", "password": "x"},
            )
            assert r.status_code == 401

        # Same email hits /register — should already be locked.
        r = await auth_api.post(
            "/api/auth/register",
            json={"email": "shared@example.com", "password": "x" * 12, "display_name": "X"},
        )
        assert r.status_code == 429

    @pytest.mark.asyncio
    async def test_second_email_not_blocked_by_first(self, auth_api):
        for _ in range(11):
            await auth_api.post(
                "/api/auth/login",
                json={"email": "first@example.com", "password": "x"},
            )

        r = await auth_api.post(
            "/api/auth/login",
            json={"email": "second@example.com", "password": "x"},
        )
        # Different email bucket → 401 (bad creds), not 429.
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_xff_header_respected(self, auth_api):
        """Requests tagged with different X-Forwarded-For last hops get separate buckets."""
        for _ in range(10):
            await auth_api.post(
                "/api/auth/login",
                json={"email": "probe@example.com", "password": "x"},
                headers={"X-Forwarded-For": "5.5.5.5"},
            )

        r = await auth_api.post(
            "/api/auth/login",
            json={"email": "probe@example.com", "password": "x"},
            headers={"X-Forwarded-For": "5.5.5.5"},
        )
        assert r.status_code == 429

        r = await auth_api.post(
            "/api/auth/login",
            json={"email": "probe@example.com", "password": "x"},
            headers={"X-Forwarded-For": "6.6.6.6"},
        )
        assert r.status_code == 401


class TestAuthRateLimitRedis:
    @pytest.mark.asyncio
    async def test_redis_path_used_when_available(self, monkeypatch):
        """When Redis is configured + available, check_auth_rate_limit delegates to Redis."""
        monkeypatch.setattr("core.config.settings.redis_url", "redis://localhost:6379")
        monkeypatch.setattr("core.rate_limit._redis_available", lambda: True)

        called = {}

        async def _fake_check(key, capacity, window):
            called["key"] = key
            called["capacity"] = capacity
            called["window"] = window
            return False, 42.0

        monkeypatch.setattr("core.rate_limit._redis_check", _fake_check)

        from core.rate_limit import check_auth_rate_limit

        allowed, retry = await check_auth_rate_limit("x@example.com", "1.1.1.1")
        assert allowed is False
        assert retry == 42.0
        assert called["key"].startswith("lcc:auth_rl:")
        assert called["capacity"] == 10
        assert called["window"] == 900
