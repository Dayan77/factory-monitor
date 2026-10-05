from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, require_roles
from ...db import get_db
from ...models import Component, Machine, Position, User, UserRole
from ...services.usage import queries as uq
from ...services.usage.recompute import (RecomputeBusy, RecomputeJob, get_job, recompute_usage,
                                         start_recompute_job, tenant_slot)
from ...services.validation import is_uuid
from ..schemas_assets import RecomputeJobOut, RecomputeOut, UsageHistoryOut, UsagePoint

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB          = Annotated[AsyncSession, Depends(get_db)]

Measure = Literal["cycles", "hours", "starts", "good", "scrap", "total"]
STORED = {"hours": "operating_seconds"}


def _aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


async def _owned(db: AsyncSession, model, eid: str, tenant_id: str, label: str):
    if not is_uuid(eid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} não encontrado(a).")
    row = (await db.execute(select(model).where(model.id == eid, model.tenant_id == tenant_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} não encontrado(a).")
    return row


@router.get("/history", response_model=UsageHistoryOut)
async def usage_history(
    user: CurrentUser, db: DB,
    measure: Measure,
    position_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    component_id: Optional[str] = None,
    start: Optional[datetime] = Query(default=None, description="default: stop − 24 h"),
    stop: Optional[datetime] = Query(default=None, description="default: agora"),
    bucket: str = Query(default="1h", pattern="^(1h|1d)$"),
):
    """Time-bucketed usage (sum per bucket). Exactly one of position_id / machine_id / component_id.
    hours are operating hours; good/scrap/total are machine production. `1d` buckets are aligned to
    the plant's local midnight. Only buckets with recorded usage are returned (sparse)."""
    given = [x for x in (position_id, machine_id, component_id) if x]
    if len(given) != 1:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Informe exatamente um de position_id, machine_id ou component_id.")
    tid = user.tenant_id
    if position_id:
        pos = await _owned(db, Position, position_id, tid, "Posição")
        scope, eid, mid = "position", pos.id, pos.machine_id
    elif machine_id:
        m = await _owned(db, Machine, machine_id, tid, "Máquina")
        scope, eid, mid = "machine", m.id, m.id
    else:
        c = await _owned(db, Component, component_id, tid, "Componente")
        scope, eid = "component", c.id
        mid = None
        if c.last_position_id:
            mid = (await db.execute(select(Position.machine_id).where(Position.id == c.last_position_id))).scalar_one_or_none()
    if measure in ("good", "scrap", "total") and scope != "machine":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="good/scrap/total só existem por máquina (machine_id).")
    now = datetime.now(timezone.utc)
    stop = _aware(stop) or now
    start = _aware(start) or (stop - timedelta(hours=24))
    if stop <= start:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="stop deve ser após start.")
    tz = await uq.machine_timezone(db, tid, mid) if mid else uq.tzinfo(None)

    if measure == "good" and not await uq.machine_has_good_tag(db, tid, eid):
        hist = await uq.history(db, tid, scope, eid, ["scrap", "total"], start, stop, bucket, tz)
        points = [UsagePoint(t=t, value=uq.production(v, False)["good"]) for t, v in hist.items()]
    else:
        stored = STORED.get(measure, measure)
        hist = await uq.history(db, tid, scope, eid, [stored], start, stop, bucket, tz)
        div = 3600.0 if measure == "hours" else 1.0
        points = [UsagePoint(t=t, value=v.get(stored, 0.0) / div) for t, v in hist.items()]
    return UsageHistoryOut(measure=measure, bucket=bucket, start=start, stop=stop, points=points)


def _job_out(job: RecomputeJob) -> RecomputeJobOut:
    r = job.report
    return RecomputeJobOut(
        job_id=job.id, status=job.status, apply=job.apply, started_at=job.started_at,
        finished_at=job.finished_at, error=job.error,
        result=RecomputeOut(readings=r.readings, applied=r.applied, differences=r.differences) if r else None,
    )


def _busy(e: RecomputeBusy) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT,
                         detail={"message": "Já existe um recálculo em andamento para este tenant.",
                                 "job_id": None if str(e) == "sync" else str(e)})


@router.post("/recompute", dependencies=[Depends(require_roles(UserRole.admin))],
             responses={200: {"model": RecomputeOut}, 202: {"model": RecomputeJobOut}, 409: {}})
async def recompute(user: CurrentUser, db: DB, apply: bool = False, background: bool = False):
    """Rebuilds the tenant's accumulators from the raw readings in InfluxDB (domain.md §3.5).
    apply=false (default) only reports differences; apply=true replaces them.
    background=true (recommended for long histories) returns 202 with a job id at once — poll
    GET /api/usage/recompute/jobs/{job_id}. One recompute per tenant at a time (409)."""
    if background:
        try:
            job = start_recompute_job(user.tenant_id, apply)
        except RecomputeBusy as e:
            raise _busy(e) from None
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=_job_out(job).model_dump(mode="json"))
    try:
        with tenant_slot(user.tenant_id):
            report = await recompute_usage(db, user.tenant_id, apply=apply)
    except RecomputeBusy as e:
        raise _busy(e) from None
    return RecomputeOut(readings=report.readings, applied=report.applied, differences=report.differences)


@router.get("/recompute/jobs/{job_id}", response_model=RecomputeJobOut,
            dependencies=[Depends(require_roles(UserRole.admin))])
async def recompute_job(job_id: str, user: CurrentUser):
    job = get_job(job_id, user.tenant_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recálculo não encontrado.")
    return _job_out(job)
