"""Tests for authentication system."""

from __future__ import annotations

import pytest
from core.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_and_verify(self):
        pw = "mysecretpassword"
        hashed = hash_password(pw)
        assert hashed != pw
        assert verify_password(pw, hashed)

    def test_wrong_password(self):
        hashed = hash_password("correct")
        assert not verify_password("wrong", hashed)

    def test_different_hashes(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # bcrypt salts differ


class TestJWT:
    def test_create_and_decode_access(self):
        token = create_access_token("user123", "user@test.com")
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "user123"
        assert payload["email"] == "user@test.com"
        assert payload["type"] == "access"

    def test_create_and_decode_refresh(self):
        token = create_refresh_token("user123")
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "user123"
        assert payload["type"] == "refresh"

    def test_invalid_token(self):
        payload = decode_token("garbage.token.here")
        assert payload is None

    def test_tampered_token(self):
        token = create_access_token("user123", "user@test.com")
        # Tamper with the token
        tampered = token[:-5] + "XXXXX"
        payload = decode_token(tampered)
        assert payload is None
