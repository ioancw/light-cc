"""Fail-closed revocation + algorithm pinning + origin guard tests (S5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import os
import pytest

from core.auth import create_access_token, is_token_revoked, decode_token


class TestAlgorithmPinning:
    def test_decode_does_not_accept_none_alg(self):
        """Even if settings.jwt_algorithm is wrong, decode stays HS256."""
        import jwt as pyjwt

        # Build a token with the 'none' alg — the classic downgrade vector.
        # jose will reject this because we lock algorithms=["HS256"].
        unsigned = pyjwt.encode({"sub": "u1", "type": "access"}, key="", algorithm="none")
        assert decode_token(unsigned) is None

    def test_decode_still_works_for_valid_hs256(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.jwt_secret", "unit-test-secret-32-bytes-minimum")
        from core.auth import create_access_token as _create
        token = _create("u1", "u1@example.com")
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "u1"


class TestRevocationFailClosed:
    @pytest.mark.asyncio
    async def test_no_redis_configured_allows_token(self, monkeypatch):
        """When Redis isn't configured at all (dev), non-revoked tokens pass."""
        monkeypatch.setattr("core.config.settings.redis_url", None)
        token = create_access_token("u1", "u1@example.com")
        assert await is_token_revoked(token) is False

    @pytest.mark.asyncio
    async def test_redis_configured_but_pool_missing_fails_closed(self, monkeypatch):
        """Redis configured but _pool is None (connect failed) → token treated as revoked."""
        monkeypatch.setattr("core.config.settings.redis_url", "redis://localhost:6379")
        monkeypatch.setattr("core.redis_store._pool", None)
        token = create_access_token("u1", "u1@example.com")
        assert await is_token_revoked(token) is True

    @pytest.mark.asyncio
    async def test_redis_call_raises_fails_closed(self, monkeypatch):
        """Redis pool present but sismember raises → token treated as revoked."""
        monkeypatch.setattr("core.config.settings.redis_url", "redis://localhost:6379")
        fake_pool = AsyncMock()
        fake_pool.sismember = AsyncMock(side_effect=ConnectionError("boom"))
        monkeypatch.setattr("core.redis_store._pool", fake_pool)

        token = create_access_token("u1", "u1@example.com")
        assert await is_token_revoked(token) is True

    @pytest.mark.asyncio
    async def test_redis_says_not_revoked_allows_token(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.redis_url", "redis://localhost:6379")
        fake_pool = AsyncMock()
        fake_pool.sismember = AsyncMock(return_value=False)
        monkeypatch.setattr("core.redis_store._pool", fake_pool)

        token = create_access_token("u1", "u1@example.com")
        assert await is_token_revoked(token) is False

    @pytest.mark.asyncio
    async def test_redis_says_revoked_blocks_token(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.redis_url", "redis://localhost:6379")
        fake_pool = AsyncMock()
        fake_pool.sismember = AsyncMock(return_value=True)
        monkeypatch.setattr("core.redis_store._pool", fake_pool)

        token = create_access_token("u1", "u1@example.com")
        assert await is_token_revoked(token) is True


class TestOriginGuard:
    def test_production_rejects_wildcard_without_domain(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("JWT_SECRET", "x" * 32)
        monkeypatch.delenv("DOMAIN", raising=False)

        from core.config import Settings, ServerConfig

        with pytest.raises(ValueError, match="allowed_origins"):
            Settings(server=ServerConfig(allowed_origins=["*"]))

    def test_production_auto_populates_from_domain(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("JWT_SECRET", "x" * 32)
        monkeypatch.setenv("DOMAIN", "example.com")

        from core.config import Settings, ServerConfig

        s = Settings(server=ServerConfig(allowed_origins=["*"]))
        assert "https://example.com" in s.server.allowed_origins
        assert "https://www.example.com" in s.server.allowed_origins
        assert "*" not in s.server.allowed_origins

    def test_development_allows_wildcard(self, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        from core.config import Settings, ServerConfig

        s = Settings(server=ServerConfig(allowed_origins=["*"]))
        assert s.server.allowed_origins == ["*"]
