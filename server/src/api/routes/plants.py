from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, require_roles
from ...db import get_db
from ...models import Line, Machine, Plant, Position, User, UserRole
from ...services.validation import is_uuid
from ..patching import apply_patch
from ..schemas_assets import (PlantCreate, PlantOut, PlantUpdate, StatusSummary, TreeLine,
                              TreeMachine, TreePlant)

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB          = Annotated[AsyncSession, Depends(get_db)]


async def get_plant_or_404(plant_id: str, tenant_id: str, db: AsyncSession) -> Plant:
    if not is_uuid(plant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planta não encontrada.")
    plant = (await db.execute(
        select(Plant).where(Plant.id == plant_id, Plant.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if plant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planta não encontrada.")
    return plant


@router.get("/", response_model=list[PlantOut])
async def list_plants(user: CurrentUser, db: DB):
    result = await db.execute(select(Plant).where(Plant.tenant_id == user.tenant_id).order_by(Plant.name))
    return result.scalars().all()


@router.post("/", response_model=PlantOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(UserRole.admin))])
async def create_plant(body: PlantCreate, user: CurrentUser, db: DB):
    plant = Plant(tenant_id=user.tenant_id, **body.model_dump())
    db.add(plant)
    await db.commit()
    await db.refresh(plant)
    return plant


@router.get("/tree", response_model=list[TreePlant])
async def plant_tree(user: CurrentUser, db: DB):
    """Plant → lines (status summary) → machines (status, last_seen_at, positions_count)."""
    tid = user.tenant_id
    plants = (await db.execute(select(Plant).where(Plant.tenant_id == tid).order_by(Plant.name))).scalars().all()
    lines = (await db.execute(select(Line).where(Line.tenant_id == tid).order_by(Line.name))).scalars().all()
    machines = (await db.execute(select(Machine).where(Machine.tenant_id == tid).order_by(Machine.name))).scalars().all()
    counts = dict((await db.execute(
        select(Position.machine_id, func.count()).where(Position.tenant_id == tid).group_by(Position.machine_id)
    )).all())

    by_line = defaultdict(list)
    for m in machines:
        by_line[m.line_id].append(m)
    by_plant = defaultdict(list)
    for ln in lines:
        ms = by_line.get(ln.id, [])
        summary = StatusSummary()
        for m in ms:
            key = m.status.value
            setattr(summary, key, getattr(summary, key) + 1)
        by_plant[ln.plant_id].append(TreeLine(
            id=ln.id, name=ln.name, output_machine_id=ln.output_machine_id,
            bottleneck_machine_id=ln.bottleneck_machine_id, status_summary=summary,
            machines=[TreeMachine(id=m.id, name=m.name, status=m.status, last_seen_at=m.last_seen_at,
                                  positions_count=counts.get(m.id, 0)) for m in ms],
        ))
    return [TreePlant(id=p.id, name=p.name, timezone=p.timezone, lines=by_plant.get(p.id, []))
            for p in plants]


@router.get("/{plant_id}", response_model=PlantOut)
async def get_plant(plant_id: str, user: CurrentUser, db: DB):
    return await get_plant_or_404(plant_id, user.tenant_id, db)


@router.patch("/{plant_id}", response_model=PlantOut,
              dependencies=[Depends(require_roles(UserRole.admin))])
async def update_plant(plant_id: str, body: PlantUpdate, user: CurrentUser, db: DB):
    plant = await get_plant_or_404(plant_id, user.tenant_id, db)
    apply_patch(plant, body, nullable=frozenset({"address"}))
    await db.commit()
    await db.refresh(plant)
    return plant


@router.delete("/{plant_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_roles(UserRole.admin))])
async def delete_plant(plant_id: str, user: CurrentUser, db: DB):
    plant = await get_plant_or_404(plant_id, user.tenant_id, db)
    has_lines = (await db.execute(
        select(func.count()).select_from(Line).where(Line.plant_id == plant.id, Line.tenant_id == user.tenant_id)
    )).scalar_one()
    if has_lines:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="A planta possui linhas. Mova ou exclua as linhas antes.")
    await db.delete(plant)
    await db.commit()
