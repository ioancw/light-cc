#!/usr/bin/env python3
"""One-time migration: move file-based memories to the database.

Reads existing data/users/*/memory/*.md files and inserts them
into the 'memories' table. Safe to run multiple times — skips
entries whose title+user_id already exist.

Usage:
    python scripts/migrate_file_memories.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import settings  # noqa: E402
from core.database import init_db, get_db, shutdown_db  # noqa: E402
from core.db_models import Memory  # noqa: E402


DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "users"


def _extract_title(text: str, filename: str) -> str:
    """Extract a title from memory file content or derive from filename."""
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:255]
    return filename.replace(".md", "").replace("-", " ").strip()[:255]


def _extract_type_from_frontmatter(text: str) -> str:
    """Try to extract memory type from YAML frontmatter if present."""
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            frontmatter = text[3:end]
            for line in frontmatter.splitlines():
                if line.strip().startswith("type:"):
                    return line.split(":", 1)[1].strip()
    return "note"


async def migrate() -> None:
    await init_db()

    if not DATA_ROOT.exists():
        print(f"No data directory found at {DATA_ROOT}")
        return

    user_dirs = [d for d in DATA_ROOT.iterdir() if d.is_dir()]
    total_migrated = 0
    total_skipped = 0

    for user_dir in user_dirs:
        user_id = user_dir.name
        memory_dir = user_dir / "memory"
        if not memory_dir.exists():
            continue

        md_files = sorted(memory_dir.glob("*.md"))
        if not md_files:
            continue

        print(f"\nMigrating memories for user: {user_id} ({len(md_files)} files)")

        async with get_db() as db:
            try:
                for md_file in md_files:
                    text = md_file.read_text(encoding="utf-8")
                    title = _extract_title(text, md_file.name)
                    memory_type = _extract_type_from_frontmatter(text)

                    # Check if already exists
                    from sqlalchemy import select
                    result = await db.execute(
                        select(Memory.id)
                        .where(Memory.user_id == user_id, Memory.title == title)
                        .limit(1)
                    )
                    if result.scalar_one_or_none():
                        print(f"  SKIP (exists): {md_file.name}")
                        total_skipped += 1
                        continue

                    mem = Memory(
                        user_id=user_id,
                        title=title,
                        content=text,
                        memory_type=memory_type,
                    )
                    db.add(mem)
                    print(f"  OK: {md_file.name} -> '{title}' (type: {memory_type})")
                    total_migrated += 1

                await db.commit()
            except Exception as e:
                print(f"  ERROR: {e}")
                await db.rollback()

    print(f"\nDone. Migrated: {total_migrated}, Skipped: {total_skipped}")
    await shutdown_db()


if __name__ == "__main__":
    asyncio.run(migrate())
