#!/bin/bash
set -e

# ── Secret validation ─────────────────────────────────────────────────
# Fail fast on known-weak production configurations. We never silently
# accept defaults in prod — a demo password in production is worse than
# a crash on boot.

ENV="${ENV:-production}"

# POSTGRES_PASSWORD: the compose file already enforces it via `${...:?}`,
# but belt-and-braces: reject the legacy default "lightcc" in production.
if [ "$ENV" = "production" ] && [ "${POSTGRES_PASSWORD:-}" = "lightcc" ]; then
    echo "FATAL: POSTGRES_PASSWORD is the default 'lightcc' in production." >&2
    echo "       Set a strong value in .env and restart the stack." >&2
    exit 1
fi

# JWT_SECRET: prod requires explicit value; dev gets a stable per-deploy
# fallback so restarts don't invalidate sessions.
if [ "$ENV" = "production" ]; then
    if [ -z "${JWT_SECRET:-}" ] || [ "$JWT_SECRET" = "change-me-in-production" ] || [ "$JWT_SECRET" = "change-me-to-a-long-random-string" ]; then
        echo "FATAL: JWT_SECRET is unset or left at the default in production." >&2
        echo "       Generate one with:" >&2
        echo "         python -c 'import secrets; print(secrets.token_urlsafe(48))'" >&2
        echo "       Set JWT_SECRET in .env and restart." >&2
        exit 1
    fi
else
    # Dev: prefer the env var, else read/create a stable secret in the
    # data volume so the user stays logged in across `docker compose restart`.
    if [ -z "${JWT_SECRET:-}" ] || [ "$JWT_SECRET" = "change-me-in-production" ]; then
        mkdir -p /app/data
        secret_file="/app/data/.dev_jwt_secret"
        if [ ! -s "$secret_file" ]; then
            python3 -c "import secrets; print(secrets.token_urlsafe(48))" > "$secret_file"
            chmod 600 "$secret_file"
            echo "entrypoint: generated stable dev JWT_SECRET at $secret_file"
        fi
        export JWT_SECRET="$(cat "$secret_file")"
    fi
fi

# Run database migrations
cd /app && alembic upgrade head

# Create admin user on first run if credentials are provided
if [ -n "$ADMIN_EMAIL" ] && [ -n "$ADMIN_PASSWORD" ]; then
    python3 -c "
import asyncio
import sys
sys.path.insert(0, '/app')

async def create_admin():
    from core.database import init_db, get_db, shutdown_db
    from core.db_models import User
    from core.auth import hash_password, get_user_by_email
    await init_db()
    async with get_db() as db:
        existing = await get_user_by_email(db, '$ADMIN_EMAIL')
        if existing:
            print('Admin user already exists, skipping creation')
            return
        user = User(
            email='$ADMIN_EMAIL',
            password_hash=hash_password('$ADMIN_PASSWORD'),
            display_name='${ADMIN_NAME:-Admin}',
            is_admin=True,
        )
        db.add(user)
        await db.commit()
        print(f'Admin user created: $ADMIN_EMAIL')
    await shutdown_db()

asyncio.run(create_admin())
" 2>&1 || echo "Admin user creation failed (non-fatal)"
fi

# Start the application
exec python3 -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} "$@"
