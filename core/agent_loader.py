"""Parse first-class AgentDefinition files from agents/<name>/AGENT.md.

YAML frontmatter + markdown body pattern (mirrors skills/loader.py).

Frontmatter schema:
    ---
    name: morning-briefing          # required, unique per user
    description: ...                # required, short
    model: claude-sonnet-4-6        # optional, null -> inherit default
    tools: [WebSearch, WebFetch]    # optional, null -> all tools
    max-turns: 15                   # optional, default 20
    timeout: 300                    # optional, default 300
    trigger: cron                   # manual | cron | webhook | api
    cron: "0 8 * * 1-5"             # required if trigger=cron
    timezone: Europe/London         # optional, default UTC
    webhook-url: https://...        # optional
    memory-scope: agent             # user | agent | none
    ---

    You are a morning briefing agent. ...
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
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
    max_turns: int = 20
    timeout_seconds: int = 300
    memory_scope: str = "user"
    trigger: str = "manual"
    cron_expression: str | None = None
    cron_timezone: str = "UTC"
    webhook_url: str | None = None
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

    trigger = str(meta.get("trigger", "manual")).lower()
    if trigger not in {"manual", "cron", "webhook", "api"}:
        logger.warning(f"Agent {name}: invalid trigger '{trigger}', defaulting to manual")
        trigger = "manual"

    cron_expression = meta.get("cron") or meta.get("cron-expression")
    if trigger == "cron" and not cron_expression:
        logger.warning(f"Agent {name}: trigger=cron but no cron expression provided")

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
        max_turns=int(meta.get("max-turns") or meta.get("max_turns") or 20),
        timeout_seconds=int(meta.get("timeout") or meta.get("timeout-seconds") or 300),
        memory_scope=memory_scope,
        trigger=trigger,
        cron_expression=str(cron_expression) if cron_expression else None,
        cron_timezone=str(meta.get("timezone") or meta.get("cron-timezone") or "UTC"),
        webhook_url=meta.get("webhook-url") or meta.get("webhook_url") or None,
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

    # Directory format: agents/<name>/AGENT.md (canonical)
    for agent_md in sorted(root.glob("*/AGENT.md")):
        a = parse_agent_file(agent_md)
        if a and a.name not in seen:
            agents.append(a)
            seen.add(a.name)

    return agents


async def sync_agents_to_db(agents_dir: str | Path, owner_user_id: str) -> int:
    """Sync YAML-defined agents to the DB as source='yaml' rows owned by owner_user_id.

    - Upserts by (user_id, name).
    - Only overwrites rows with source='yaml' (preserves user edits to source='user' rows).
    - Returns the count of agents synced.
    """
    from sqlalchemy import select
    from core.database import get_db
    from core.db_models import AgentDefinition

    defs = discover_agents(agents_dir)
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

            if existing and existing.source != "yaml":
                logger.info(f"Skipping YAML sync of agent '{d.name}' (user-owned copy exists)")
                continue

            tools_json = json.dumps(d.tools) if d.tools is not None else None
            permissions_json = json.dumps(d.permissions) if d.permissions else None

            if existing is None:
                row = AgentDefinition(
                    user_id=owner_user_id,
                    name=d.name,
                    description=d.description,
                    model=d.model,
                    system_prompt=d.system_prompt,
                    tools=tools_json,
                    max_turns=d.max_turns,
                    timeout_seconds=d.timeout_seconds,
                    memory_scope=d.memory_scope,
                    permissions=permissions_json,
                    trigger=d.trigger,
                    cron_expression=d.cron_expression,
                    cron_timezone=d.cron_timezone,
                    webhook_url=d.webhook_url,
                    enabled=d.enabled,
                    source="yaml",
                )
                session.add(row)
            else:
                existing.description = d.description
                existing.model = d.model
                existing.system_prompt = d.system_prompt
                existing.tools = tools_json
                existing.max_turns = d.max_turns
                existing.timeout_seconds = d.timeout_seconds
                existing.memory_scope = d.memory_scope
                existing.permissions = permissions_json
                existing.trigger = d.trigger
                existing.cron_expression = d.cron_expression
                existing.cron_timezone = d.cron_timezone
                existing.webhook_url = d.webhook_url
                existing.enabled = d.enabled
            synced += 1

        await session.commit()
    finally:
        await session.close()

    logger.info(f"Synced {synced} YAML agent(s) to DB for user {owner_user_id}")
    return synced
