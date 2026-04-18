"""Parse callable AgentDefinition files from ``agents/<name>/AGENT.md``.

YAML frontmatter + markdown body pattern (mirrors skills/loader.py).

Frontmatter schema:
    ---
    name: person-research           # required, unique per user
    description: ...                # required, short
    model: claude-sonnet-4-6        # optional, null -> inherit default
    tools: [WebSearch, WebFetch]    # optional, null -> all tools
    max-turns: 15                   # optional, default 20
    timeout: 300                    # optional, default 300
    memory-scope: user              # user | agent | none
    enabled: true                   # optional, default true
    ---

    You are a ... agent. ...

Agents are callable via the ``Task`` tool (in-conversation) or the
``POST /api/agents/run`` endpoint (headless). Scheduling lives in
``Schedule`` rows, separate from the agent definition -- if you want a
daily firing, create a Schedule with prompt ``/agent-name``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class AgentDef:
    """Parsed agent definition from YAML (pre-DB representation)."""

    name: str
    description: str
    system_prompt: str
    model: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    max_turns: int = 20
    timeout_seconds: int = 300
    memory_scope: str = "user"
    permissions: dict[str, Any] | None = None
    enabled: bool = True
    source_path: str = ""


def _parse_tools(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    if isinstance(raw, str):
        if "," in raw:
            return [t.strip() for t in raw.split(",") if t.strip()]
        return [t for t in raw.split() if t]
    return None


def parse_agent_file(path: Path) -> AgentDef | None:
    """Parse a single AGENT.md into an AgentDef."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to read agent file {path}: {e}")
        return None

    match = _FRONTMATTER_RE.match(text)
    if not match:
        logger.warning(f"Agent file {path} has no YAML frontmatter, skipping")
        return None

    try:
        meta: dict[str, Any] = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        logger.warning(f"Invalid YAML in {path}: {e}")
        return None

    body = text[match.end():].strip()

    name = meta.get("name", path.parent.name if path.name == "AGENT.md" else path.stem)
    description = meta.get("description", "")
    if not name or not description or not body:
        logger.warning(f"Agent {path} missing name/description/body, skipping")
        return None

    # Legacy fields that used to live here (trigger, cron, timezone, webhook-url)
    # are silently ignored -- scheduling moved to Schedule rows.
    for legacy_key in ("trigger", "cron", "cron-expression", "timezone",
                       "cron-timezone", "webhook-url", "webhook_url"):
        if legacy_key in meta:
            logger.info(
                f"Agent '{name}' uses legacy frontmatter key '{legacy_key}' which is now ignored. "
                f"Scheduling lives in Schedule rows; see docs."
            )

    memory_scope = str(meta.get("memory-scope") or meta.get("memory_scope") or "user").lower()
    if memory_scope not in {"user", "agent", "none"}:
        memory_scope = "user"

    permissions = meta.get("permissions")
    if permissions is not None and not isinstance(permissions, dict):
        permissions = None

    return AgentDef(
        name=str(name),
        description=str(description),
        system_prompt=body,
        model=meta.get("model") or None,
        tools=_parse_tools(meta.get("tools")),
        skills=_parse_tools(meta.get("skills")),
        max_turns=int(meta.get("max-turns") or meta.get("max_turns") or 20),
        timeout_seconds=int(meta.get("timeout") or meta.get("timeout-seconds") or 300),
        memory_scope=memory_scope,
        permissions=permissions,
        enabled=bool(meta.get("enabled", True)),
        source_path=str(path),
    )


def discover_agents(agents_dir: str | Path) -> list[AgentDef]:
    """Auto-discover AGENT.md files under agents_dir."""
    root = Path(agents_dir)
    if not root.exists():
        return []

    agents: list[AgentDef] = []
    seen: set[str] = set()

    for agent_md in sorted(root.glob("*/AGENT.md")):
        a = parse_agent_file(agent_md)
        if a and a.name not in seen:
            agents.append(a)
            seen.add(a.name)

    return agents


async def sync_agent_defs_to_db(
    defs: list[AgentDef],
    owner_user_id: str,
    *,
    source_label: str = "yaml",
) -> int:
    """Upsert a list of AgentDefs into the DB for a specific owner.

    - Upserts by (user_id, name).
    - Overwrites rows whose existing source matches source_label (or legacy 'yaml' rows
      when source_label starts with 'plugin:' — this lets a plugin take ownership of an
      agent previously installed as a YAML definition).
    - Skips user-owned rows (source='user') to preserve manual edits.
    - Returns the count of agents synced.
    """
    from sqlalchemy import select
    from core.database import get_db
    from core.db_models import AgentDefinition

    if not defs:
        return 0

    synced = 0
    session = await get_db()
    try:
        for d in defs:
            stmt = select(AgentDefinition).where(
                AgentDefinition.user_id == owner_user_id,
                AgentDefinition.name == d.name,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing and existing.source == "user":
                logger.info(
                    f"Skipping {source_label} sync of agent '{d.name}' (user-owned copy exists)"
                )
                continue

            tools_json = json.dumps(d.tools) if d.tools is not None else None
            skills_json = json.dumps(d.skills) if d.skills is not None else None
            permissions_json = json.dumps(d.permissions) if d.permissions else None

            if existing is None:
                row = AgentDefinition(
                    user_id=owner_user_id,
                    name=d.name,
                    description=d.description,
                    model=d.model,
                    system_prompt=d.system_prompt,
                    tools=tools_json,
                    skills=skills_json,
                    max_turns=d.max_turns,
                    timeout_seconds=d.timeout_seconds,
                    memory_scope=d.memory_scope,
                    permissions=permissions_json,
                    enabled=d.enabled,
                    source=source_label,
                )
                session.add(row)
            else:
                existing.description = d.description
                existing.model = d.model
                existing.system_prompt = d.system_prompt
                existing.tools = tools_json
                existing.skills = skills_json
                existing.max_turns = d.max_turns
                existing.timeout_seconds = d.timeout_seconds
                existing.memory_scope = d.memory_scope
                existing.permissions = permissions_json
                existing.enabled = d.enabled
                existing.source = source_label
            synced += 1

        await session.commit()
    finally:
        await session.close()

    logger.info(
        f"Synced {synced} agent(s) to DB for user {owner_user_id} (source={source_label})"
    )
    return synced


async def sync_agents_to_db(agents_dir: str | Path, owner_user_id: str) -> int:
    """Sync YAML-defined agents from a directory to the DB as source='yaml'."""
    defs = discover_agents(agents_dir)
    return await sync_agent_defs_to_db(defs, owner_user_id, source_label="yaml")
