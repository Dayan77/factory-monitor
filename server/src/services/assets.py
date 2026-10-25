"""Asset helpers: default plant, component install/remove (domain.md §1, §5.4)."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Component, ComponentType, Plant, Position, RemovalReason
from ..models.plant import DEFAULT_TIMEZONE

DEFAULT_PLANT_NAME = "Planta principal"
CLOCK_SKEW = timedelta(minutes=5)


async def default_plant(db: AsyncSession, tenant_id: str) -> Plant:
    """The tenant's oldest plant; creates 'Planta principal' when it has none."""
    plant = (await db.execute(
        select(Plant).where(Plant.tenant_id == tenant_id).order_by(Plant.created_at, Plant.id).limit(1)
    )).scalar_one_or_none()
    if plant is None:
        plant = Plant(tenant_id=tenant_id, name=DEFAULT_PLANT_NAME, timezone=DEFAULT_TIMEZONE)
        db.add(plant)
        await db.flush()
    return plant


def _conflict(msg: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg)


def _not_future(ts: Optional[datetime], label: str) -> datetime:
    now = datetime.now(timezone.utc)
    if ts is None:
        return now
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if ts > now + CLOCK_SKEW:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{label} não pode estar no futuro.")
    return ts


async def installed_component(db: AsyncSession, tenant_id: str, position_id: str) -> Optional[Component]:
    return (await db.execute(
        select(Component).where(Component.tenant_id == tenant_id, Component.position_id == position_id,
                                Component.removed_at.is_(None))
    )).scalar_one_or_none()


async def install(db: AsyncSession, tenant_id: str, position: Position, component: Component,
                  installed_at: Optional[datetime] = None) -> Component:
    """Installs a stock component in a position. Commits. 409 if the position is occupied,
    the component is installed elsewhere or was removed (removed components are history)."""
    if component.removed_at is not None:
        raise _conflict("Componente removido não pode ser reinstalado (o histórico é mantido).")
    if component.position_id is not None:
        raise _conflict("Componente já está instalado em uma posição.")
    if (position.component_type != component.component_type
            and ComponentType.other not in (position.component_type, component.component_type)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Tipo do componente diferente do tipo da posição.")
    if await installed_component(db, tenant_id, position.id) is not None:
        raise _conflict("A posição já tem um componente instalado. Remova-o antes.")
    component.position_id = position.id
    component.last_position_id = position.id
    component.installed_at = _not_future(installed_at, "installed_at")
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _conflict("A posição já tem um componente instalado. Remova-o antes.")
    await db.refresh(component)
    return component


async def remove(db: AsyncSession, component: Component, reason: RemovalReason,
                 removed_at: Optional[datetime] = None) -> Component:
    """Removes an installed component: position_id = null, removed_at, removal_reason.
    Never deletes; usage_since_install stays as the age at removal. Commits."""
    if component.position_id is None or component.removed_at is not None:
        raise _conflict("Componente não está instalado.")
    ts = _not_future(removed_at, "removed_at")
    if component.installed_at is not None and ts < component.installed_at:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="removed_at anterior à instalação.")
    component.position_id = None
    component.removed_at = ts
    component.removal_reason = reason
    await db.commit()
    await db.refresh(component)
    return component
