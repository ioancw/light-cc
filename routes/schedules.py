"""Schedule CRUD endpoints."""

from __future__ import annotations

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Query

from core.schedule_crud import (
    create_schedule,
    delete_schedule,
    get_schedule,
    get_schedule_runs,
    list_schedules,
    trigger_schedule_now,
    update_schedule,
)
from routes.auth import get_current_user, User

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


# -- Request/Response schemas --

class CreateScheduleRequest(BaseModel):
    name: str
    cron_expression: str
    prompt: str
    user_timezone: str = "UTC"


class UpdateScheduleRequest(BaseModel):
    name: str | None = None
    cron_expression: str | None = None
    prompt: str | None = None
    enabled: bool | None = None
    user_timezone: str | None = None


class ScheduleResponse(BaseModel):
    id: str
    name: str
    cron_expression: str
    prompt: str
    user_timezone: str
    enabled: bool
    last_run_at: str | None
    next_run_at: str | None
    created_at: str


class ScheduleRunResponse(BaseModel):
    id: str
    status: str
    result: str | None
    error: str | None
    started_at: str
    finished_at: str | None


def _to_response(sched) -> ScheduleResponse:
    return ScheduleResponse(
        id=sched.id,
        name=sched.name,
        cron_expression=sched.cron_expression,
        prompt=sched.prompt,
        user_timezone=sched.user_timezone or "UTC",
        enabled=sched.enabled,
        last_run_at=sched.last_run_at.isoformat() if sched.last_run_at else None,
        next_run_at=sched.next_run_at.isoformat() if sched.next_run_at else None,
        created_at=sched.created_at.isoformat(),
    )


def _run_to_response(run) -> ScheduleRunResponse:
    return ScheduleRunResponse(
        id=run.id,
        status=run.status,
        result=run.result,
        error=run.error,
        started_at=run.started_at.isoformat(),
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
    )


# -- Endpoints --

@router.get("", response_model=list[ScheduleResponse])
async def api_list_schedules(user: User = Depends(get_current_user)):
    schedules = await list_schedules(user.id)
    return [_to_response(s) for s in schedules]


@router.post("", response_model=ScheduleResponse, status_code=201)
async def api_create_schedule(req: CreateScheduleRequest, user: User = Depends(get_current_user)):
    try:
        sched = await create_schedule(user.id, req.name, req.cron_expression, req.prompt, req.user_timezone)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_response(sched)


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def api_get_schedule(schedule_id: str, user: User = Depends(get_current_user)):
    sched = await get_schedule(schedule_id, user.id)
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return _to_response(sched)


@router.patch("/{schedule_id}", response_model=ScheduleResponse)
async def api_update_schedule(
    schedule_id: str, req: UpdateScheduleRequest, user: User = Depends(get_current_user),
):
    kwargs = {k: v for k, v in req.model_dump().items() if v is not None}
    if not kwargs:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        sched = await update_schedule(schedule_id, user.id, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return _to_response(sched)


@router.delete("/{schedule_id}", status_code=204)
async def api_delete_schedule(schedule_id: str, user: User = Depends(get_current_user)):
    deleted = await delete_schedule(schedule_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Schedule not found")


@router.get("/{schedule_id}/runs", response_model=list[ScheduleRunResponse])
async def api_get_runs(
    schedule_id: str,
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    runs = await get_schedule_runs(schedule_id, user.id, limit=limit)
    return [_run_to_response(r) for r in runs]


@router.post("/{schedule_id}/run", status_code=202)
async def api_trigger_now(schedule_id: str, user: User = Depends(get_current_user)):
    triggered = await trigger_schedule_now(schedule_id, user.id)
    if not triggered:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"status": "triggered"}
