"""AgentDefinition CRUD operations -- used by REST API and scheduler."""

from __future__ import annotations

import json

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.database import get_db
from core.db_models import AgentDefinition, AgentRun
from core.scheduler import _compute_next_run


_VALID_TRIGGERS = {"manual", "cron", "webhook", "api"}
_VALID_MEMORY_SCOPES = {"user", "agent", "none"}


def _validate_definition(
    trigger: str,
    cron_expression: str | None,
    memory_scope: str,
) -> None:
    if trigger not in _VALID_TRIGGERS:
        raise ValueError(f"Invalid trigger: {trigger}. Must be one of {sorted(_VALID_TRIGGERS)}")
    if memory_scope not in _VALID_MEMORY_SCOPES:
        raise ValueError(f"Invalid memory_scope: {memory_scope}. Must be one of {sorted(_VALID_MEMORY_SCOPES)}")
    if trigger == "cron":
        if not cron_expression:
            raise ValueError("cron_expression is required when trigger='cron'")
        if not croniter.is_valid(cron_expression):
            raise ValueError(f"Invalid cron expression: {cron_expression}")


async def create_agent(
    user_id: str,
    name: str,
    description: str,
    system_prompt: str,
    *,
    model: str | None = None,
    tools: list[str] | None = None,
    max_turns: int = 20,
    timeout_seconds: int = 300,
    memory_scope: str = "user",
    permissions: dict | None = None,
    trigger: str = "manual",
    cron_expression: str | None = None,
    cron_timezone: str = "UTC",
    webhook_url: str | None = None,
) -> AgentDefinition:
    """Create a new agent definition. Raises ValueError for invalid input or duplicate name."""
    _validate_definition(trigger, cron_expression, memory_scope)

    next_run_at = None
    if trigger == "cron" and cron_expression:
        next_run_at = _compute_next_run(cron_expression, user_tz=cron_timezone)

    db = await get_db()
    try:
        agent = AgentDefinition(
            user_id=user_id,
            name=name,
            description=description,
            system_prompt=system_prompt,
            model=model,
            tools=json.dumps(tools) if tools is not None else None,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            memory_scope=memory_scope,
            permissions=json.dumps(permissions) if permissions else None,
            trigger=trigger,
            cron_expression=cron_expression,
            cron_timezone=cron_timezone,
            webhook_url=webhook_url,
            enabled=True,
            source="user",
            next_run_at=next_run_at,
        )
        db.add(agent)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise ValueError(f"An agent named '{name}' already exists.")
        await db.refresh(agent)
        return agent
    finally:
        await db.close()


async def list_agents(user_id: str) -> list[AgentDefinition]:
    db = await get_db()
    try:
        stmt = select(AgentDefinition).where(AgentDefinition.user_id == user_id).order_by(AgentDefinition.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())
    finally:
        await db.close()


async def get_agent(agent_id: str, user_id: str) -> AgentDefinition | None:
    db = await get_db()
    try:
        stmt = select(AgentDefinition).where(
            AgentDefinition.id == agent_id,
            AgentDefinition.user_id == user_id,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    finally:
        await db.close()


async def get_agent_by_name(name: str, user_id: str) -> AgentDefinition | None:
    """Look up an agent by name, scoped to a user."""
    db = await get_db()
    try:
        stmt = select(AgentDefinition).where(
            AgentDefinition.name == name,
            AgentDefinition.user_id == user_id,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    finally:
        await db.close()


async def update_agent(agent_id: str, user_id: str, **kwargs) -> AgentDefinition | None:
    """Update an agent definition. Fields: name, description, model, system_prompt,
    tools, max_turns, timeout_seconds, memory_scope, permissions, trigger,
    cron_expression, cron_timezone, webhook_url, enabled.
    """
    db = await get_db()
    try:
        stmt = select(AgentDefinition).where(
            AgentDefinition.id == agent_id,
            AgentDefinition.user_id == user_id,
        )
        result = await db.execute(stmt)
        agent = result.scalar_one_or_none()
        if not agent:
            return None

        # Apply updates
        for key, value in kwargs.items():
            if key == "tools":
                agent.tools = json.dumps(value) if value is not None else None
            elif key == "permissions":
                agent.permissions = json.dumps(value) if value else None
            elif hasattr(agent, key):
                setattr(agent, key, value)

        # Revalidate after update
        _validate_definition(agent.trigger, agent.cron_expression, agent.memory_scope)

        # Recompute next_run_at if trigger/cron/timezone changed or we toggled enabled
        if agent.trigger == "cron" and agent.cron_expression and agent.enabled:
            if any(k in kwargs for k in ("cron_expression", "cron_timezone", "trigger", "enabled")):
                agent.next_run_at = _compute_next_run(agent.cron_expression, user_tz=agent.cron_timezone)
        elif agent.trigger != "cron":
            agent.next_run_at = None

        await db.commit()
        await db.refresh(agent)
        return agent
    finally:
        await db.close()


async def delete_agent(agent_id: str, user_id: str) -> bool:
    db = await get_db()
    try:
        stmt = select(AgentDefinition).where(
            AgentDefinition.id == agent_id,
            AgentDefinition.user_id == user_id,
        )
        result = await db.execute(stmt)
        agent = result.scalar_one_or_none()
        if not agent:
            return False
        await db.delete(agent)
        await db.commit()
        return True
    finally:
        await db.close()


async def get_agent_runs(agent_id: str, user_id: str, limit: int = 20) -> list[AgentRun]:
    db = await get_db()
    try:
        # Verify ownership first
        owner_stmt = select(AgentDefinition.id).where(
            AgentDefinition.id == agent_id,
            AgentDefinition.user_id == user_id,
        )
        owner_result = await db.execute(owner_stmt)
        if not owner_result.scalar_one_or_none():
            return []

        stmt = (
            select(AgentRun)
            .where(AgentRun.agent_id == agent_id)
            .order_by(AgentRun.started_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())
    finally:
        await db.close()


async def get_agent_run(agent_id: str, run_id: str, user_id: str) -> AgentRun | None:
    db = await get_db()
    try:
        owner_stmt = select(AgentDefinition.id).where(
            AgentDefinition.id == agent_id,
            AgentDefinition.user_id == user_id,
        )
        owner_result = await db.execute(owner_stmt)
        if not owner_result.scalar_one_or_none():
            return None

        stmt = select(AgentRun).where(
            AgentRun.id == run_id,
            AgentRun.agent_id == agent_id,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    finally:
        await db.close()
