"""Tests for the YAML agent loader (core/agent_loader.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.agent_loader import (
    AgentDef,
    discover_agents,
    parse_agent_file,
    sync_agents_to_db,
)
from core.db_models import AgentDefinition


def _write_agent(tmp_path: Path, name: str, content: str) -> Path:
    d = tmp_path / name
    d.mkdir()
    path = d / "AGENT.md"
    path.write_text(content, encoding="utf-8")
    return path


# ── parse_agent_file ──────────────────────────────────────────────────

class TestParseAgentFile:
    def test_minimal_valid(self, tmp_path):
        path = _write_agent(tmp_path, "a1", """---
name: a1
description: A minimal agent
---

You are a minimal agent.
""")
        result = parse_agent_file(path)
        assert result is not None
        assert result.name == "a1"
        assert result.description == "A minimal agent"
        assert result.system_prompt == "You are a minimal agent."
        assert result.trigger == "manual"
        assert result.memory_scope == "user"
        assert result.tools is None

    def test_no_frontmatter_returns_none(self, tmp_path):
        path = _write_agent(tmp_path, "a2", "Just a body, no frontmatter.")
        assert parse_agent_file(path) is None

    def test_invalid_yaml_returns_none(self, tmp_path):
        path = _write_agent(tmp_path, "a3", """---
name: a3
description: [unclosed
---
body
""")
        assert parse_agent_file(path) is None

    def test_missing_name_rejected(self, tmp_path):
        # name comes from parent dir if missing, but description+body still required
        path = _write_agent(tmp_path, "a4", """---
description: no body below
---
""")
        # Empty body triggers rejection
        assert parse_agent_file(path) is None

    def test_missing_description_rejected(self, tmp_path):
        path = _write_agent(tmp_path, "a5", """---
name: a5
---

body
""")
        assert parse_agent_file(path) is None

    def test_tools_as_list(self, tmp_path):
        path = _write_agent(tmp_path, "a6", """---
name: a6
description: d
tools:
  - WebSearch
  - WebFetch
---

body
""")
        result = parse_agent_file(path)
        assert result.tools == ["WebSearch", "WebFetch"]

    def test_tools_as_comma_string(self, tmp_path):
        path = _write_agent(tmp_path, "a7", """---
name: a7
description: d
tools: Read, Write, Grep
---

body
""")
        result = parse_agent_file(path)
        assert result.tools == ["Read", "Write", "Grep"]

    def test_tools_as_space_string(self, tmp_path):
        path = _write_agent(tmp_path, "a8", """---
name: a8
description: d
tools: Read Write Grep
---

body
""")
        result = parse_agent_file(path)
        assert result.tools == ["Read", "Write", "Grep"]

    def test_cron_trigger_parsed(self, tmp_path):
        path = _write_agent(tmp_path, "a9", """---
name: a9
description: d
trigger: cron
cron: "0 8 * * 1-5"
timezone: Europe/London
---

Run every weekday at 8 AM.
""")
        result = parse_agent_file(path)
        assert result.trigger == "cron"
        assert result.cron_expression == "0 8 * * 1-5"
        assert result.cron_timezone == "Europe/London"

    def test_invalid_trigger_defaults_to_manual(self, tmp_path):
        path = _write_agent(tmp_path, "a10", """---
name: a10
description: d
trigger: nonsense
---

body
""")
        result = parse_agent_file(path)
        assert result.trigger == "manual"

    def test_memory_scope_invalid_defaults_to_user(self, tmp_path):
        path = _write_agent(tmp_path, "a11", """---
name: a11
description: d
memory-scope: wonky
---

body
""")
        result = parse_agent_file(path)
        assert result.memory_scope == "user"

    def test_max_turns_and_timeout_parsed(self, tmp_path):
        path = _write_agent(tmp_path, "a12", """---
name: a12
description: d
max-turns: 42
timeout: 600
---

body
""")
        result = parse_agent_file(path)
        assert result.max_turns == 42
        assert result.timeout_seconds == 600

    def test_webhook_url_parsed(self, tmp_path):
        path = _write_agent(tmp_path, "a13", """---
name: a13
description: d
webhook-url: https://example.com/hook
---

body
""")
        result = parse_agent_file(path)
        assert result.webhook_url == "https://example.com/hook"

    def test_cron_trigger_without_expression_still_parses(self, tmp_path):
        # Loader warns but doesn't reject; DB-level validation catches this
        path = _write_agent(tmp_path, "a14", """---
name: a14
description: d
trigger: cron
---

body
""")
        result = parse_agent_file(path)
        assert result is not None
        assert result.trigger == "cron"
        assert result.cron_expression is None

    def test_disabled_flag(self, tmp_path):
        path = _write_agent(tmp_path, "a15", """---
name: a15
description: d
enabled: false
---

body
""")
        result = parse_agent_file(path)
        assert result.enabled is False


# ── discover_agents ────────────────────────────────────────────────────

class TestDiscoverAgents:
    def test_empty_dir(self, tmp_path):
        assert discover_agents(tmp_path) == []

    def test_nonexistent_dir(self, tmp_path):
        assert discover_agents(tmp_path / "missing") == []

    def test_finds_multiple_agents(self, tmp_path):
        _write_agent(tmp_path, "alpha", """---
name: alpha
description: a
---
body
""")
        _write_agent(tmp_path, "beta", """---
name: beta
description: b
---
body
""")
        results = discover_agents(tmp_path)
        names = {a.name for a in results}
        assert names == {"alpha", "beta"}

    def test_skips_duplicates(self, tmp_path):
        # Two different dirs, same `name` field in frontmatter
        _write_agent(tmp_path, "x1", """---
name: shared
description: a
---
body
""")
        _write_agent(tmp_path, "x2", """---
name: shared
description: b
---
body
""")
        results = discover_agents(tmp_path)
        assert len(results) == 1


# ── sync_agents_to_db ─────────────────────────────────────────────────

@pytest_asyncio.fixture
async def loader_db(test_db: AsyncSession, test_user):
    """Patch get_db in agent_loader to use the test session."""

    async def _get_test_db():
        return test_db

    with patch("core.database.get_db", side_effect=_get_test_db):
        yield test_db, test_user


class TestSyncAgentsToDB:
    @pytest.mark.asyncio
    async def test_inserts_new_agents(self, tmp_path, loader_db):
        db, user = loader_db
        _write_agent(tmp_path, "daily", """---
name: daily
description: Daily agent
---

Do the daily thing.
""")

        count = await sync_agents_to_db(tmp_path, user.id)
        assert count == 1

        rows = (await db.execute(
            select(AgentDefinition).where(AgentDefinition.user_id == user.id),
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].name == "daily"
        assert rows[0].source == "yaml"

    @pytest.mark.asyncio
    async def test_updates_existing_yaml_row(self, tmp_path, loader_db):
        db, user = loader_db
        _write_agent(tmp_path, "upd", """---
name: upd
description: v1
---

Version 1 prompt.
""")
        await sync_agents_to_db(tmp_path, user.id)

        # Rewrite the file
        _write_agent(tmp_path / "upd", "AGENT.md_ignored", "")  # no-op dir write
        (tmp_path / "upd" / "AGENT.md").write_text("""---
name: upd
description: v2
---

Version 2 prompt.
""", encoding="utf-8")

        count = await sync_agents_to_db(tmp_path, user.id)
        assert count == 1

        rows = (await db.execute(
            select(AgentDefinition).where(AgentDefinition.user_id == user.id),
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].description == "v2"
        assert "Version 2" in rows[0].system_prompt

    @pytest.mark.asyncio
    async def test_skips_user_owned_row(self, tmp_path, loader_db):
        db, user = loader_db
        # Pre-create a user-owned row with the same name
        from core.agent_crud import create_agent

        async def _get_test_db():
            return db

        with patch("core.agent_crud.get_db", side_effect=_get_test_db):
            await create_agent(
                user_id=user.id, name="conflict",
                description="user version",
                system_prompt="User's own prompt.",
            )

        _write_agent(tmp_path, "conflict", """---
name: conflict
description: yaml version
---

YAML version prompt.
""")

        count = await sync_agents_to_db(tmp_path, user.id)
        # The yaml agent was skipped
        assert count == 0

        rows = (await db.execute(
            select(AgentDefinition).where(
                AgentDefinition.user_id == user.id,
                AgentDefinition.name == "conflict",
            ),
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].source == "user"
        assert rows[0].description == "user version"

    @pytest.mark.asyncio
    async def test_empty_dir_is_noop(self, tmp_path, loader_db):
        _, user = loader_db
        count = await sync_agents_to_db(tmp_path, user.id)
        assert count == 0

    @pytest.mark.asyncio
    async def test_sync_is_per_user(self, tmp_path, loader_db):
        """YAML sync runs per user -- each user gets their own copy of every
        shipped agent, and re-running for the same user doesn't duplicate."""
        db, user = loader_db

        from core.auth import hash_password
        from core.db_models import User

        # Add a second user to the same DB
        u2 = User(
            email="u2@example.com",
            password_hash=hash_password("x"),
            display_name="U2",
        )
        db.add(u2)
        await db.commit()
        await db.refresh(u2)

        _write_agent(tmp_path, "shared", """---
name: shared
description: d
---

body
""")

        # First sync for each user → 1 row per user
        assert await sync_agents_to_db(tmp_path, user.id) == 1
        assert await sync_agents_to_db(tmp_path, u2.id) == 1

        # Re-running sync for user.id must not create a duplicate
        assert await sync_agents_to_db(tmp_path, user.id) == 1

        rows = (await db.execute(
            select(AgentDefinition).where(AgentDefinition.name == "shared"),
        )).scalars().all()
        assert len(rows) == 2
        assert {r.user_id for r in rows} == {user.id, u2.id}

    @pytest.mark.asyncio
    async def test_cron_yaml_sync_leaves_next_run_at_unset(self, tmp_path, loader_db):
        """Sync does not compute next_run_at for cron YAML agents -- the
        scheduler fills it in lazily on first pass. Guard against a future
        refactor that might quietly start computing it (or stop)."""
        db, user = loader_db
        _write_agent(tmp_path, "cron-y", """---
name: cron-y
description: d
trigger: cron
cron: "0 8 * * 1-5"
timezone: UTC
---

body
""")
        await sync_agents_to_db(tmp_path, user.id)

        row = (await db.execute(
            select(AgentDefinition).where(
                AgentDefinition.user_id == user.id,
                AgentDefinition.name == "cron-y",
            ),
        )).scalar_one()
        assert row.trigger == "cron"
        assert row.cron_expression == "0 8 * * 1-5"
        assert row.next_run_at is None
