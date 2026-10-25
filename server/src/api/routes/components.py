from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, require_roles
from ...db import get_db
from ...models import Component, Position, User, UserRole
from ...services import assets
from ...services.usage import queries as uq
from ...services.validation import is_uuid
from ..patching import apply_patch
from ..schemas_assets import ComponentCreate, ComponentOut, ComponentUpdate, RemoveRequest, UsageValues
from ..tenant_refs import ensure_owned

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB          = Annotated[AsyncSession, Depends(get_db)]


async def get_component_or_404(component_id: str, tenant_id: str, db: AsyncSession,
                               status_code: int = 404) -> Component:
    if not is_uuid(component_id):
        raise HTTPException(status_code=status_code, detail="Componente não encontrado.")
    comp = (await db.execute(
        select(Component).where(Component.id == component_id, Component.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if comp is None:
        raise HTTPException(status_code=status_code, detail="Componente não encontrado.")
    return comp


def _state(c: Component) -> str:
    if c.removed_at is not None:
        return "removed"
    return "installed" if c.position_id else "in_stock"


async def component_out(db: AsyncSession, tenant_id: str, comps: list[Component]) -> list[ComponentOut]:
    usages = await uq.component_usages(db, tenant_id, comps)
    out = []
    for c in comps:
        o = ComponentOut.model_validate(c)
        o.state = _state(c)
        o.usage_since_install = UsageValues(**usages[c.id])
        o.life_consumed_pct, o.life_measure = uq.component_life(c, usages[c.id])
        out.append(o)
    return out


@router.get("/", response_model=list[ComponentOut])
async def list_components(user: CurrentUser, db: DB, position_id: Optional[str] = None,
                          in_stock: Optional[bool] = None, include_removed: bool = True,
                          serial_number: Optional[str] = None):
    """position_id: installed in that position *and* its past components (history), newest first.
    in_stock=true: not installed and never removed; in_stock=false: installed or removed."""
    q = select(Component).where(Component.tenant_id == user.tenant_id)
    if position_id:
        if not is_uuid(position_id):
            return []
        q = q.where((Component.position_id == position_id) | (Component.last_position_id == position_id))
    if in_stock is True:
        q = q.where(Component.position_id.is_(None), Component.removed_at.is_(None))
    elif in_stock is False:
        q = q.where((Component.position_id.is_not(None)) | (Component.removed_at.is_not(None)))
    if serial_number:
        q = q.where(Component.serial_number == serial_number)
    if not include_removed:
        q = q.where(Component.removed_at.is_(None))
    comps = (await db.execute(q.order_by(Component.installed_at.desc().nulls_last(),
                                         Component.created_at.desc()))).scalars().all()
    return await component_out(db, user.tenant_id, list(comps))


@router.post("/", response_model=ComponentOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(UserRole.admin))])
async def create_component(body: ComponentCreate, user: CurrentUser, db: DB):
    position = None
    if body.position_id is not None:
        pid = await ensure_owned(db, Position, body.position_id, user.tenant_id, "position_id")
        position = (await db.execute(select(Position).where(Position.id == pid))).scalar_one()
    comp = Component(
        tenant_id=user.tenant_id, component_type=body.component_type, manufacturer=body.manufacturer,
        model=body.model, serial_number=body.serial_number,
        nominal_life=body.nominal_life.model_dump(exclude_none=True),
    )
    db.add(comp)
    if position is not None:
        await db.flush()
        await assets.install(db, user.tenant_id, position, comp, body.installed_at)
    else:
        await db.commit()
        await db.refresh(comp)
    return (await component_out(db, user.tenant_id, [comp]))[0]


@router.get("/{component_id}", response_model=ComponentOut)
async def get_component(component_id: str, user: CurrentUser, db: DB):
    comp = await get_component_or_404(component_id, user.tenant_id, db)
    return (await component_out(db, user.tenant_id, [comp]))[0]


@router.patch("/{component_id}", response_model=ComponentOut,
              dependencies=[Depends(require_roles(UserRole.admin))])
async def update_component(component_id: str, body: ComponentUpdate, user: CurrentUser, db: DB):
    comp = await get_component_or_404(component_id, user.tenant_id, db)
    if body.component_type is not None and comp.position_id is not None and body.component_type != comp.component_type:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Não é possível mudar o tipo de um componente instalado.")
    apply_patch(comp, body, nullable=frozenset({"manufacturer", "model", "serial_number"}),
                skip=frozenset({"nominal_life"}))
    if body.nominal_life is not None:
        comp.nominal_life = body.nominal_life.model_dump(exclude_none=True)
    await db.commit()
    await db.refresh(comp)
    return (await component_out(db, user.tenant_id, [comp]))[0]


@router.post("/{component_id}/remove", response_model=ComponentOut,
             dependencies=[Depends(require_roles(UserRole.admin))])
async def remove_component(component_id: str, body: RemoveRequest, user: CurrentUser, db: DB):
    """Uninstalls (never deletes): position_id = null, removed_at, removal_reason; the component
    keeps usage_since_install (age at removal) and the position keeps usage_total."""
    comp = await get_component_or_404(component_id, user.tenant_id, db)
    await assets.remove(db, comp, body.reason, body.removed_at)
    return (await component_out(db, user.tenant_id, [comp]))[0]
