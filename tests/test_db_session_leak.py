"""Regression tests for core/database.py session hygiene.

R1 converted `get_db()` from `db = await get_db()` (caller owns close) to an
`@asynccontextmanager` (scope owns close). These tests assert that pattern:
a sequential run of `async with get_db()` blocks never leaks checked-out
connections and the underlying pool stays bounded.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from core.database import _engine, get_db


@pytest.mark.asyncio
async def test_sequential_get_db_does_not_leak_connections():
    """100 sequential get_db() blocks → 0 checked-out connections after each.

    Runs a trivial query inside each block so SQLAlchemy actually acquires
    a connection from the pool (an unused session never checks out).
    """
    pool = _engine.pool
    for _ in range(100):
        async with get_db() as db:
            await db.execute(text("SELECT 1"))
        assert pool.checkedout() == 0, (
            f"connection leaked: {pool.checkedout()} still checked out after get_db exit"
        )


@pytest.mark.asyncio
async def test_get_db_closes_on_exception():
    """If the body raises, the session is still closed and the pool released."""
    pool = _engine.pool
    baseline = pool.checkedout()

    class Boom(Exception):
        pass

    for _ in range(25):
        with pytest.raises(Boom):
            async with get_db() as db:
                await db.execute(text("SELECT 1"))
                raise Boom

    assert pool.checkedout() == baseline, (
        f"exception path leaked {pool.checkedout() - baseline} connection(s)"
    )


@pytest.mark.asyncio
async def test_pool_size_stays_bounded():
    """After 100 sequential blocks, the pool hasn't ballooned in size.

    Uses the `size()` method where available (QueuePool-family). For pool
    implementations without `size()` (NullPool, StaticPool), the assertion
    is skipped — the leak test above already covers correctness for those.
    """
    pool = _engine.pool
    size_fn = getattr(pool, "size", None)
    if size_fn is None:
        pytest.skip(f"pool {type(pool).__name__} has no size(); leak test covers it")

    for _ in range(100):
        async with get_db() as db:
            await db.execute(text("SELECT 1"))

    # QueuePool caps at pool_size + max_overflow. Engine default is 5 + 10 = 15.
    # Anything above 20 would indicate unbounded growth.
    assert size_fn() <= 20, f"pool grew to {size_fn()} connections — unbounded growth"
