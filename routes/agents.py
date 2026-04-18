"""AgentDefinition CRUD + run endpoints.

Agents are callable personas. Invocation paths:
- ``POST /api/agents/{id}/run``      -- async background run (returns 202 + run_id)
- ``POST /api/agents/run``           -- async background run by name (returns 202)
- ``POST /api/agents/run/sync``      -- synchronous headless run; blocks until
  the agent completes and returns its full output. This is the entry point
  for CC → Light CC integration and other external automation.
"""

from __future__ import annotations

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Query

from core.agent_crud import (
    create_agent,
    delete_agent,
    get_agent,
    get_agent_by_name,
    get_agent_run,
    get_agent_runs,
    list_agents,
    update_agent,
)
from routes.auth import get_current_user, User

router = APIRouter(prefix="/api/agents", tags=["agents"])


# -- Request/Response schemas --

class RunByNameRequest(BaseModel):
    name: str


class SyncRunRequest(BaseModel):
    """Body for the synchronous /api/agents/run/sync endpoint."""
    name: str
    prompt: str


class SyncRunResponse(BaseModel):
    """Full result of a synchronous agent run."""
    run_id: str
    status: str
    result: str | None
    error: str | None
    tokens_used: int
    conversation_id: str | None


class CreateAgentRequest(BaseModel):
    name: str
    description: str
    system_prompt: str
    model: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    max_turns: int = 20
    timeout_seconds: int = 300
    memory_scope: str = "user"
    permissions: dict | None = None


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    max_turns: int | None = None
    timeout_seconds: int | None = None
    memory_scope: str | None = None
    permissions: dict | None = None
    enabled: bool | None = None


class AgentResponse(BaseModel):
    id: str
    name: str
    description: str
    system_prompt: str
    model: str | None
    tools: list[str] | None
    skills: list[str] | None
    max_turns: int
    timeout_seconds: int
    memory_scope: str
    permissions: dict | None
    enabled: bool
    source: str
    last_run_at: str | None
    created_at: str


class AgentRunResponse(BaseModel):
    id: str
    agent_id: str
    status: str
    trigger_type: str
    result: str | None
    error: str | None
    started_at: str
    finished_at: str | None
    tokens_used: int
    conversation_id: str | None


def _to_response(agent) -> AgentResponse:
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        system_prompt=agent.system_prompt,
        model=agent.model,
        tools=agent.tools_list,
        skills=agent.skills_list,
        max_turns=agent.max_turns,
        timeout_seconds=agent.timeout_seconds,
        memory_scope=agent.memory_scope,
        permissions=_parse_json(agent.permissions),
        enabled=agent.enabled,
        source=agent.source,
        last_run_at=agent.last_run_at.isoformat() if agent.last_run_at else None,
        created_at=agent.created_at.isoformat(),
    )


def _run_to_response(run) -> AgentRunResponse:
    return AgentRunResponse(
        id=run.id,
        agent_id=run.agent_id,
        status=run.status,
        trigger_type=run.trigger_type,
        result=run.result,
        error=run.error,
        started_at=run.started_at.isoformat(),
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        tokens_used=run.tokens_used,
        conversation_id=run.conversation_id,
    )


def _parse_json(raw):
    if not raw:
        return None
    import json
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


# -- Endpoints --

@router.get("", response_model=list[AgentResponse])
async def api_list_agents(user: User = Depends(get_current_user)):
    agents = await list_agents(user.id)
    return [_to_response(a) for a in agents]


@router.post("", response_model=AgentResponse, status_code=201)
async def api_create_agent(req: CreateAgentRequest, user: User = Depends(get_current_user)):
    try:
        agent = await create_agent(
            user_id=user.id,
            name=req.name,
            description=req.description,
            system_prompt=req.system_prompt,
            model=req.model,
            tools=req.tools,
            skills=req.skills,
            max_turns=req.max_turns,
            timeout_seconds=req.timeout_seconds,
            memory_scope=req.memory_scope,
            permissions=req.permissions,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_response(agent)


@router.get("/{agent_id}", response_model=AgentResponse)
async def api_get_agent(agent_id: str, user: User = Depends(get_current_user)):
    agent = await get_agent(agent_id, user.id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _to_response(agent)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def api_update_agent(
    agent_id: str, req: UpdateAgentRequest, user: User = Depends(get_current_user),
):
    kwargs = {k: v for k, v in req.model_dump(exclude_unset=True).items()}
    if not kwargs:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        agent = await update_agent(agent_id, user.id, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _to_response(agent)


@router.delete("/{agent_id}", status_code=204)
async def api_delete_agent(agent_id: str, user: User = Depends(get_current_user)):
    deleted = await delete_agent(agent_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/{agent_id}/runs", response_model=list[AgentRunResponse])
async def api_list_agent_runs(
    agent_id: str,
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    agent = await get_agent(agent_id, user.id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    runs = await get_agent_runs(agent_id, user.id, limit=limit)
    return [_run_to_response(r) for r in runs]


@router.get("/{agent_id}/runs/{run_id}", response_model=AgentRunResponse)
async def api_get_agent_run(
    agent_id: str, run_id: str, user: User = Depends(get_current_user),
):
    run = await get_agent_run(agent_id, run_id, user.id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_to_response(run)


async def _gate_agent_run(user_id: str) -> None:
    """Enforce per-user rate limits on the external agent-run surface."""
    from core.rate_limit import check_rate_limit_async
    allowed, reason = await check_rate_limit_async(user_id, "agent_run")
    if not allowed:
        raise HTTPException(status_code=429, detail=reason)


@router.post("/{agent_id}/run", response_model=AgentRunResponse, status_code=202)
async def api_trigger_agent_run(agent_id: str, user: User = Depends(get_current_user)):
    """Trigger a manual run of the agent. Returns the AgentRun immediately (run executes in background)."""
    from core.agent_runner import trigger_agent_run
    agent = await get_agent(agent_id, user.id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.enabled:
        raise HTTPException(status_code=400, detail="Agent is disabled")
    await _gate_agent_run(user.id)
    try:
        run = await trigger_agent_run(agent, trigger_type="manual")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to trigger run: {e}")
    return _run_to_response(run)


@router.post("/run", response_model=AgentRunResponse, status_code=202)
async def api_trigger_agent_run_by_name(
    req: RunByNameRequest, user: User = Depends(get_current_user),
):
    """Async: look up an agent by name and enqueue a run. Returns 202 + run record."""
    from core.agent_runner import trigger_agent_run
    agent = await get_agent_by_name(req.name, user.id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{req.name}' not found")
    if not agent.enabled:
        raise HTTPException(status_code=400, detail="Agent is disabled")
    await _gate_agent_run(user.id)
    try:
        run = await trigger_agent_run(agent, trigger_type="api")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to trigger run: {e}")
    return _run_to_response(run)


@router.post("/run/sync", response_model=SyncRunResponse)
async def api_run_agent_sync(
    req: SyncRunRequest, user: User = Depends(get_current_user),
):
    """Synchronous headless execution. Blocks until the agent completes.

    The primary external-integration entry point — a CC session (or any
    other system) can POST ``{name, prompt}`` and receive the agent's
    full output in the response body. Runs with persistence + webhook
    side effects, same as the async path; the difference is the caller
    gets the result inline instead of polling.
    """
    from core.agent_runner import run_agent_once
    agent = await get_agent_by_name(req.name, user.id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{req.name}' not found")
    if not agent.enabled:
        raise HTTPException(status_code=400, detail="Agent is disabled")
    await _gate_agent_run(user.id)
    try:
        result = await run_agent_once(
            agent, req.prompt,
            trigger_type="api",
            persist_conversation=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent run failed: {e}")

    return SyncRunResponse(
        run_id=result.run_id,
        status=result.status,
        result=result.result_text,
        error=result.error,
        tokens_used=result.tokens_used,
        conversation_id=result.conversation_id,
    )
