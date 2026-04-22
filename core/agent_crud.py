"""AgentDefinition CRUD operations -- used by REST API.

Agents are callable personas (no triggers, no cron). For scheduled firings,
create a ``Schedule`` row whose prompt references the agent (``/agent-name``).
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.database import get_db
from core.db_models import AgentDefinition, AgentRun


_VALID_MEMORY_SCOPES = {"user", "agent", "none"}


def _validate_definition(memory_scope: str) -> None:
    if memory_scope not in _VALID_MEMORY_SCOPES:
        raise ValueError(
            f"Invalid memory_scope: {memory_scope}. Must be one of {sorted(_VALID_MEMORY_SCOPES)}"
        )


async def create_agent(
    user_id: str,
    name: str,
    description: str,
    system_prompt: str,
    *,
    model: str | None = None,
    tools: list[str] | None = None,
    skills: list[str] | None = None,
    max_turns: int = 20,
    timeout_seconds: int = 300,
    memory_scope: str = "user",
    permissions: dict | None = None,
) -> AgentDefinition:
    """Create a new agent definition. Raises ValueError for invalid input or duplicate name."""
    _validate_definition(memory_scope)

    async with get_db() as db:
        agent = AgentDefinition(
            user_id=user_id,
            name=name,
            description=description,
            system_prompt=system_prompt,
            model=model,
            tools=json.dumps(tools) if tools is not None else None,
            skills=json.dumps(skills) if skills is not None else None,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            memory_scope=memory_scope,
            permissions=json.dumps(permissions) if permissions else None,
            enabled=True,
            source="user",
        )
        db.add(agent)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise ValueError(f"An agent named '{name}' already exists.")
        await db.refresh(agent)
        return agent


async def list_agents(user_id: str) -> list[AgentDefinition]:
    async with get_db() as db:
        stmt = select(AgentDefinition).where(AgentDefinition.user_id == user_id).order_by(AgentDefinition.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())


async def get_agent(agent_id: str, user_id: str) -> AgentDefinition | None:
    async with get_db() as db:
        stmt = select(AgentDefinition).where(
            AgentDefinition.id == agent_id,
            AgentDefinition.user_id == user_id,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()


async def get_agent_by_name(name: str, user_id: str) -> AgentDefinition | None:
    """Look up an agent by name, scoped to a user."""
    async with get_db() as db:
        stmt = select(AgentDefinition).where(
            AgentDefinition.name == name,
            AgentDefinition.user_id == user_id,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()


async def match_agent_by_intent(
    user_message: str,
    user_id: str,
    *,
    threshold: int = 2,
) -> AgentDefinition | None:
    """Score this user's enabled agents against the message; return the best
    match if its score crosses ``threshold``.

    Mirrors ``skills.registry.match_skill_by_intent`` so skill and agent
    intent routing behave consistently. Score per agent:
      - +2 for each name word found in the message
      - +1 for each description word (>3 chars) found in the message

    The match is a *hint* for the chat handler to nudge the model toward
    delegation; it never auto-dispatches.
    """
    if not user_id or user_id == "default":
        return None

    msg_lower = user_message.strip().lower()
    if not msg_lower:
        return None

    async with get_db() as db:
        stmt = select(AgentDefinition).where(
            AgentDefinition.user_id == user_id,
            AgentDefinition.enabled.is_(True),
        )
        result = await db.execute(stmt)
        agents = list(result.scalars().all())

    best: AgentDefinition | None = None
    best_score = 0
    for a in agents:
        score = 0
        for word in a.name.replace("-", " ").split():
            if word.lower() in msg_lower:
                score += 2
        for word in (a.description or "").lower().split():
            if len(word) > 3 and word in msg_lower:
                score += 1
        if score > best_score:
            best_score = score
            best = a

    return best if best_score >= threshold else None


async def update_agent(agent_id: str, user_id: str, **kwargs) -> AgentDefinition | None:
    """Update an agent definition. Fields: name, description, model, system_prompt,
    tools, max_turns, timeout_seconds, memory_scope, permissions, enabled.
    """
    async with get_db() as db:
        stmt = select(AgentDefinition).where(
            AgentDefinition.id == agent_id,
            AgentDefinition.user_id == user_id,
        )
        result = await db.execute(stmt)
        agent = result.scalar_one_or_none()
        if not agent:
            return None

        for key, value in kwargs.items():
            if key == "tools":
                agent.tools = json.dumps(value) if value is not None else None
            elif key == "skills":
                agent.skills = json.dumps(value) if value is not None else None
            elif key == "permissions":
                agent.permissions = json.dumps(value) if value else None
            elif hasattr(agent, key):
                setattr(agent, key, value)

        _validate_definition(agent.memory_scope)

        await db.commit()
        await db.refresh(agent)
        return agent


async def delete_agent(agent_id: str, user_id: str) -> bool:
    async with get_db() as db:
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


async def get_agent_runs(agent_id: str, user_id: str, limit: int = 20) -> list[AgentRun]:
    async with get_db() as db:
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


async def get_agent_run(agent_id: str, run_id: str, user_id: str) -> AgentRun | None:
    async with get_db() as db:
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
