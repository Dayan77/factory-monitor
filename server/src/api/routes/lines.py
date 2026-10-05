from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_db
from ...models import User, UserRole, Line, Machine, Plant
from ...auth import get_current_user, require_roles
from ...services.assets import default_plant
from ...services.usage import queries as uq
from ...services.validation import is_uuid
from ..patching import apply_patch
from ..schemas import LineOut, LineCreate, LineUpdate
from ..schemas_assets import LineProductionOut, ProductionPoint
from ..tenant_refs import ensure_owned

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB          = Annotated[AsyncSession, Depends(get_db)]


async def _get_line_or_404(line_id: str, tenant_id: str, db: AsyncSession) -> Line:
    if not is_uuid(line_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Linha não encontrada.")
    result = await db.execute(select(Line).where(Line.id == line_id, Line.tenant_id == tenant_id))
    line = result.scalar_one_or_none()
    if not line:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Linha não encontrada.")
    return line


async def _ensure_machine_in_line(db: AsyncSession, machine_id: str, line: Line, label: str) -> str:
    machine_id = await ensure_owned(db, Machine, machine_id, line.tenant_id, label)
    line_of = (await db.execute(select(Machine.line_id).where(Machine.id == machine_id))).scalar_one()
    if line_of != line.id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"{label}: a máquina não pertence a esta linha.")
    return machine_id


@router.get("/", response_model=list[LineOut])
async def list_lines(user: CurrentUser, db: DB, plant_id: Optional[str] = None):
    q = select(Line).where(Line.tenant_id == user.tenant_id)
    if plant_id:
        if not is_uuid(plant_id):
            return []
        q = q.where(Line.plant_id == plant_id)
    result = await db.execute(q.order_by(Line.name))
    return result.scalars().all()


@router.post("/", response_model=LineOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(UserRole.admin))])
async def create_line(body: LineCreate, user: CurrentUser, db: DB):
    data = body.model_dump()
    if body.plant_id is not None:
        data["plant_id"] = await ensure_owned(db, Plant, body.plant_id, user.tenant_id, "plant_id")
    else:
        data["plant_id"] = (await default_plant(db, user.tenant_id)).id
    line = Line(tenant_id=user.tenant_id, **data)
    db.add(line)
    await db.commit()
    await db.refresh(line)
    return line


@router.get("/{line_id}", response_model=LineOut)
async def get_line(line_id: str, user: CurrentUser, db: DB):
    return await _get_line_or_404(line_id, user.tenant_id, db)


@router.patch("/{line_id}", response_model=LineOut,
              dependencies=[Depends(require_roles(UserRole.admin))])
async def update_line(line_id: str, body: LineUpdate, user: CurrentUser, db: DB):
    line = await _get_line_or_404(line_id, user.tenant_id, db)
    sent = body.model_fields_set
    if body.plant_id is not None:
        body.plant_id = await ensure_owned(db, Plant, body.plant_id, user.tenant_id, "plant_id")
    for f in ("output_machine_id", "bottleneck_machine_id"):
        if f in sent and getattr(body, f) is not None:
            setattr(body, f, await _ensure_machine_in_line(db, getattr(body, f), line, f))
    apply_patch(line, body, nullable=frozenset({"description", "location", "output_machine_id",
                                                "bottleneck_machine_id"}))
    await db.commit()
    await db.refresh(line)
    return line


@router.delete("/{line_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_roles(UserRole.admin))])
async def delete_line(line_id: str, user: CurrentUser, db: DB):
    line = await _get_line_or_404(line_id, user.tenant_id, db)
    await db.delete(line)
    await db.commit()


@router.get("/{line_id}/production", response_model=LineProductionOut)
async def line_production(
    line_id: str, user: CurrentUser, db: DB,
    start: Optional[datetime] = Query(default=None, description="default: início do dia na planta"),
    stop: Optional[datetime] = Query(default=None, description="default: agora"),
    bucket: str = Query(default="1h", pattern="^(1h|1d)$"),
):
    """Production of the line = part counters of its output machine (domain.md §1, kpis.md §1.9).
    Hourly bucket granularity: `start` is floored to the hour."""
    line = await _get_line_or_404(line_id, user.tenant_id, db)
    tz_name = (await db.execute(select(Plant.timezone).where(Plant.id == line.plant_id))).scalar_one_or_none()
    tz = uq.tzinfo(tz_name)
    now = datetime.now(timezone.utc)
    start = _aware(start) or uq.local_midnight_utc(now, tz)
    stop = _aware(stop) or now
    if stop <= start:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="stop deve ser após start.")
    out = LineProductionOut(line_id=line.id, output_machine_id=line.output_machine_id, start=start, stop=stop)
    mid = line.output_machine_id
    if mid is None:
        out.reason = "no_output_machine"
        return out
    has_good = await uq.machine_has_good_tag(db, user.tenant_id, mid)
    sums = await uq.window_sums(db, user.tenant_id, "machine", [mid], start, stop)
    vals = {m: v for (_, m), v in sums.items()}
    prod = uq.production(vals, has_good)
    out.good, out.scrap, out.total = prod["good"], prod["scrap"], prod["total"]
    elapsed_h = (min(stop, now) - start).total_seconds() / 3600.0
    out.cadence_per_hour = out.total / elapsed_h if elapsed_h > 0 else None
    run_h = vals.get("operating_seconds", 0.0) / 3600.0
    out.cadence_running_per_hour = out.total / run_h if run_h > 0 else None
    hist = await uq.history(db, user.tenant_id, "machine", mid, ["good", "scrap", "total"], start, stop, bucket, tz)
    for t, v in hist.items():
        p = uq.production(v, has_good)
        out.points.append(ProductionPoint(t=t, good=p["good"], scrap=p["scrap"]))
    return out


def _aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts
