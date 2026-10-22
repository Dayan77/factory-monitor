from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...services import operator_scope as opscope
from ...db import get_db
from ...models import User, UserRole, Machine, Line, Position, Component
from ...auth import get_current_user, require_roles
from ..schemas import MachineOut, MachineCreate, MachineUpdate
from ..schemas_assets import MachineUsageOut, MachineUsagePosition, ProductionCounts
from ...services.usage import queries as uq
from ...services.usage.topology import load_topology
from ..tenant_refs import ensure_owned
from ...services.validation import is_uuid

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB          = Annotated[AsyncSession, Depends(get_db)]


async def _get_machine_or_404(machine_id: str, tenant_id: str, db: AsyncSession) -> Machine:
    if not is_uuid(machine_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Máquina não encontrada.")
    result = await db.execute(
        select(Machine).where(Machine.id == machine_id, Machine.tenant_id == tenant_id)
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Máquina não encontrada.")
    return m


@router.get("/", response_model=list[MachineOut])
async def list_machines(user: CurrentUser, db: DB, line_id: str | None = None):
    q = select(Machine).where(Machine.tenant_id == user.tenant_id)
    if line_id:
        if not is_uuid(line_id):
            return []
        q = q.where(Machine.line_id == line_id)
    own = opscope.operator_lines(user)       # operator PWA: only his lines (§3.2)
    if own is not None:
        q = q.where(Machine.line_id.in_(list(own)))
    result = await db.execute(q.order_by(Machine.name))
    return result.scalars().all()


@router.post("/", response_model=MachineOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(UserRole.admin))])
async def create_machine(body: MachineCreate, user: CurrentUser, db: DB):
    body.line_id = await ensure_owned(db, Line, body.line_id, user.tenant_id, "line_id")
    machine = Machine(tenant_id=user.tenant_id, **body.model_dump())
    db.add(machine)
    await db.commit()
    await db.refresh(machine)
    return machine


@router.get("/{machine_id}", response_model=MachineOut)
async def get_machine(machine_id: str, user: CurrentUser, db: DB):
    return await _get_machine_or_404(machine_id, user.tenant_id, db)


@router.patch("/{machine_id}", response_model=MachineOut,
              dependencies=[Depends(require_roles(UserRole.manager))])
async def update_machine(machine_id: str, body: MachineUpdate, user: CurrentUser, db: DB):
    machine = await _get_machine_or_404(machine_id, user.tenant_id, db)
    if body.line_id is not None:
        body.line_id = await ensure_owned(db, Line, body.line_id, user.tenant_id, "line_id")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(machine, field, value)
    await db.commit()
    await db.refresh(machine)
    return machine


@router.delete("/{machine_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_roles(UserRole.admin))])
async def delete_machine(machine_id: str, user: CurrentUser, db: DB):
    machine = await _get_machine_or_404(machine_id, user.tenant_id, db)
    await db.delete(machine)
    await db.commit()


@router.get("/{machine_id}/usage", response_model=MachineUsageOut)
async def machine_usage(machine_id: str, user: CurrentUser, db: DB):
    """Usage of every position (usage_total + life of the installed component) and today's
    production (productive day = local midnight in the plant timezone → now)."""
    from datetime import datetime, timezone

    machine = await _get_machine_or_404(machine_id, user.tenant_id, db)
    tid = user.tenant_id
    positions = (await db.execute(
        select(Position).where(Position.tenant_id == tid, Position.machine_id == machine.id)
        .order_by(Position.name)
    )).scalars().all()
    pids = [p.id for p in positions]
    ptotals = await uq.totals(db, tid, "position", pids)
    comps = (await db.execute(
        select(Component).where(Component.tenant_id == tid, Component.position_id.in_(pids),
                                Component.removed_at.is_(None))
    )).scalars().all() if pids else []
    comp_by_pos = {c.position_id: c for c in comps}
    cusage = await uq.component_usages(db, tid, comps)
    topo = await load_topology(db, tid, [machine.id])

    rows = []
    for p in positions:
        u = uq.position_usage({m: v for (eid, m), v in ptotals.items() if eid == p.id})
        c = comp_by_pos.get(p.id)
        life, measure = uq.component_life(c, cusage[c.id]) if c else (None, None)
        info = topo.positions.get(p.id)
        rows.append(MachineUsagePosition(
            position_id=p.id, name=p.name, component_type=p.component_type, **u,
            cycles_estimated=bool(info and not info.has_cycle_tag),
            component_id=c.id if c else None, life_consumed_pct=life, life_measure=measure))

    tz = await uq.machine_timezone(db, tid, machine.id)
    now = datetime.now(timezone.utc)
    start = uq.local_midnight_utc(now, tz)
    sums = await uq.window_sums(db, tid, "machine", [machine.id], start, now)
    prod = uq.production({m: v for (_, m), v in sums.items()},
                         await uq.machine_has_good_tag(db, tid, machine.id))
    mt = await uq.totals(db, tid, "machine", [machine.id])
    return MachineUsageOut(
        machine_id=machine.id, positions=rows,
        production=ProductionCounts(**prod, start=start, timezone=tz.key),
        hours_total=mt.get((machine.id, "operating_seconds"), 0.0) / 3600.0,
        starts_total=mt.get((machine.id, "starts"), 0.0),
    )
