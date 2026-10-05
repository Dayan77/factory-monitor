from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, require_roles
from ...db import get_db
from ...models import Component, Machine, Position, Tag, UsageBucket, UsageTotal, User, UserRole
from ...services import assets
from ...services.usage import queries as uq
from ...services.usage.topology import load_topology
from ...services.validation import is_uuid
from ..patching import apply_patch
from ..schemas_assets import (ComponentUsage, InstallRequest, PositionCreate, PositionDetail,
                              PositionOut, PositionUpdate, PositionUsage)
from ..tenant_refs import ensure_owned
from .components import component_out, get_component_or_404

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB          = Annotated[AsyncSession, Depends(get_db)]


async def get_position_or_404(position_id: str, tenant_id: str, db: AsyncSession) -> Position:
    if not is_uuid(position_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Posição não encontrada.")
    pos = (await db.execute(
        select(Position).where(Position.id == position_id, Position.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if pos is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Posição não encontrada.")
    return pos


async def _check_counter_tag(db: AsyncSession, tag_id: str, tenant_id: str, machine_id: str) -> str:
    tag_id = await ensure_owned(db, Tag, tag_id, tenant_id, "counter_tag_id")
    tag_machine = (await db.execute(select(Tag.machine_id).where(Tag.id == tag_id))).scalar_one()
    if tag_machine is not None and tag_machine != machine_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="counter_tag_id: a tag pertence a outra máquina.")
    return tag_id


async def _out(db: AsyncSession, positions: list[Position], tenant_id: str) -> list[PositionOut]:
    pids = [p.id for p in positions]
    installed = dict((await db.execute(
        select(Component.position_id, Component.id).where(
            Component.tenant_id == tenant_id, Component.position_id.in_(pids), Component.removed_at.is_(None))
    )).all()) if pids else {}
    out = []
    for p in positions:
        o = PositionOut.model_validate(p)
        o.installed_component_id = installed.get(p.id)
        out.append(o)
    return out


@router.get("/", response_model=list[PositionOut])
async def list_positions(user: CurrentUser, db: DB, machine_id: Optional[str] = None):
    q = select(Position).where(Position.tenant_id == user.tenant_id)
    if machine_id:
        if not is_uuid(machine_id):
            return []
        q = q.where(Position.machine_id == machine_id)
    positions = (await db.execute(q.order_by(Position.name))).scalars().all()
    return await _out(db, positions, user.tenant_id)


@router.post("/", response_model=PositionOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(UserRole.admin))])
async def create_position(body: PositionCreate, user: CurrentUser, db: DB):
    body.machine_id = await ensure_owned(db, Machine, body.machine_id, user.tenant_id, "machine_id")
    if body.counter_tag_id is not None:
        body.counter_tag_id = await _check_counter_tag(db, body.counter_tag_id, user.tenant_id, body.machine_id)
    pos = Position(tenant_id=user.tenant_id, **body.model_dump())
    db.add(pos)
    await db.commit()
    await db.refresh(pos)
    return (await _out(db, [pos], user.tenant_id))[0]


@router.get("/{position_id}", response_model=PositionDetail)
async def get_position(position_id: str, user: CurrentUser, db: DB):
    tid = user.tenant_id
    pos = await get_position_or_404(position_id, tid, db)
    base = (await _out(db, [pos], tid))[0]
    vals = {m: v for (_, m), v in (await uq.totals(db, tid, "position", [pos.id])).items()}
    topo = await load_topology(db, tid, [pos.machine_id])
    info = topo.positions.get(pos.id)
    usage = PositionUsage(**uq.position_usage(vals), cycles_estimated=bool(info and not info.has_cycle_tag))
    comp = await assets.installed_component(db, tid, pos.id)
    comp_out = comp_usage = None
    if comp is not None:
        comp_out = (await component_out(db, tid, [comp]))[0]
        tz = await uq.machine_timezone(db, tid, pos.machine_id)
        comp_usage = ComponentUsage(**await uq.component_usage_detail(db, tid, comp, tz, datetime.now(timezone.utc)))
    return PositionDetail(**base.model_dump(), installed_component=comp_out, usage=usage,
                          component_usage=comp_usage)


@router.patch("/{position_id}", response_model=PositionOut,
              dependencies=[Depends(require_roles(UserRole.admin))])
async def update_position(position_id: str, body: PositionUpdate, user: CurrentUser, db: DB):
    pos = await get_position_or_404(position_id, user.tenant_id, db)
    if body.counter_tag_id is not None:
        body.counter_tag_id = await _check_counter_tag(db, body.counter_tag_id, user.tenant_id, pos.machine_id)
    apply_patch(pos, body, nullable=frozenset({"counter_tag_id"}))
    await db.commit()
    await db.refresh(pos)
    return (await _out(db, [pos], user.tenant_id))[0]


@router.delete("/{position_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_roles(UserRole.admin))])
async def delete_position(position_id: str, user: CurrentUser, db: DB):
    """Positions are permanent in normal use; deleting is only allowed with no installed component
    (component history is kept: its last_position_id becomes null)."""
    pos = await get_position_or_404(position_id, user.tenant_id, db)
    if await assets.installed_component(db, user.tenant_id, pos.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Remova o componente instalado antes de excluir a posição.")
    for model in (UsageTotal, UsageBucket):
        await db.execute(delete(model).where(model.tenant_id == user.tenant_id, model.scope == "position",
                                             model.entity_id == pos.id))
    await db.delete(pos)
    await db.commit()


@router.post("/{position_id}/install", response_model=PositionDetail,
             dependencies=[Depends(require_roles(UserRole.admin))])
async def install_component(position_id: str, body: InstallRequest, user: CurrentUser, db: DB):
    pos = await get_position_or_404(position_id, user.tenant_id, db)
    comp = await get_component_or_404(body.component_id, user.tenant_id, db, status_code=404)
    await assets.install(db, user.tenant_id, pos, comp, body.installed_at)
    return await get_position(position_id, user, db)
