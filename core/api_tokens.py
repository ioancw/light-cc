"""API token CRUD + verification.

Long-lived personal access tokens for programmatic callers (webhooks, cron
on external boxes, CI integrations, strangers' automations). Complement to
the short-lived JWT access tokens issued by ``/api/auth/login``.

Security properties:
- Plaintext is returned to the caller exactly once, at creation.
- Only a SHA-256 hash is stored.
- Tokens are opaque (no embedded claims) -- the owning user is resolved
  via DB lookup.
- Revocation is immediate and idempotent.
- Expiry is optional (``None`` = never).

Token format: ``lcc_`` + 32 url-safe base64 characters. The ``lcc_`` prefix
is a marker for auth middleware to distinguish API tokens from JWTs; the
first 12 plaintext characters are stored as ``prefix`` for UI display.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from core.database import get_db
from core.db_models import ApiToken, User


TOKEN_PREFIX = "lcc_"
_TOKEN_BODY_BYTES = 24  # ~32 url-safe chars after base64 encoding
_DISPLAY_PREFIX_LEN = 12  # stored for UI ("lcc_" + 8 random chars)


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BODY_BYTES)


async def create_api_token(
    user_id: str,
    name: str,
    expires_at: Optional[datetime] = None,
) -> tuple[ApiToken, str]:
    """Create a new API token for ``user_id``.

    Returns ``(row, plaintext)``. The plaintext is the only time the caller
    can see the secret -- future reads of the row only expose the prefix.
    """
    if not name or not name.strip():
        raise ValueError("Token name is required")

    plaintext = _generate_token()
    row = ApiToken(
        user_id=user_id,
        name=name.strip(),
        token_hash=_hash_token(plaintext),
        prefix=plaintext[:_DISPLAY_PREFIX_LEN],
        expires_at=expires_at,
    )

    db = await get_db()
    try:
        db.add(row)
        await db.commit()
        await db.refresh(row)
    finally:
        await db.close()

    return row, plaintext


async def list_api_tokens(user_id: str) -> list[ApiToken]:
    db = await get_db()
    try:
        result = await db.execute(
            select(ApiToken)
            .where(ApiToken.user_id == user_id)
            .order_by(ApiToken.created_at.desc()),
        )
        return list(result.scalars().all())
    finally:
        await db.close()


async def revoke_api_token(user_id: str, token_id: str) -> bool:
    """Mark a token revoked. Returns True if a matching, not-yet-revoked
    token was found and revoked; False otherwise (wrong user, unknown id,
    or already revoked)."""
    db = await get_db()
    try:
        result = await db.execute(
            select(ApiToken).where(
                ApiToken.id == token_id,
                ApiToken.user_id == user_id,
            ),
        )
        row = result.scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = datetime.now(timezone.utc)
        await db.commit()
        return True
    finally:
        await db.close()


async def verify_api_token(plaintext: str) -> Optional[User]:
    """Resolve a plaintext API token to its owning ``User``.

    Returns ``None`` for any failure mode (unknown hash, revoked, expired).
    On success, updates ``last_used_at``. Callers must not leak the reason
    for failure.
    """
    if not plaintext or not plaintext.startswith(TOKEN_PREFIX):
        return None

    # Constant-time hash comparison is implicit in the DB index lookup, but
    # we still compute the hash outside the query to avoid leaking timing
    # on the prefix check.
    token_hash = _hash_token(plaintext)

    db = await get_db()
    try:
        result = await db.execute(
            select(ApiToken).where(ApiToken.token_hash == token_hash),
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None

        # Constant-time verify to defuse any theoretical hash-length oracle.
        if not hmac.compare_digest(row.token_hash, token_hash):
            return None

        now = datetime.now(timezone.utc)
        if row.revoked_at is not None:
            return None
        if row.expires_at is not None:
            # SQLite drops tzinfo on read; assume UTC for the stored value.
            expires = row.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= now:
                return None

        row.last_used_at = now
        user = (await db.execute(
            select(User).where(User.id == row.user_id),
        )).scalar_one_or_none()
        if user is None:
            return None

        await db.commit()
        return user
    finally:
        await db.close()
