#!/bin/bash
set -e

# Run database migrations
cd /app && alembic upgrade head

# Auto-generate JWT_SECRET if not set
if [ -z "$JWT_SECRET" ] || [ "$JWT_SECRET" = "change-me-in-production" ]; then
    export JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
    echo "Generated random JWT_SECRET (set JWT_SECRET env var to persist across restarts)"
fi

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
    db = await get_db()
    try:
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
    finally:
        await db.close()
    await shutdown_db()

asyncio.run(create_admin())
" 2>&1 || echo "Admin user creation failed (non-fatal)"
fi

# Start the application
exec python3 -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} "$@"
