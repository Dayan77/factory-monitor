from datetime import datetime, timezone
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_db
from ...models import User, UserRole, AlertRule, AlertEvent
from ...auth import get_current_user, require_roles
from ..schemas import AlertRuleOut, AlertRuleCreate, AlertEventOut

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB          = Annotated[AsyncSession, Depends(get_db)]


@router.get("/rules", response_model=list[AlertRuleOut])
async def list_rules(user: CurrentUser, db: DB):
    result = await db.execute(
        select(AlertRule)
        .where(AlertRule.tenant_id == user.tenant_id)
        .order_by(AlertRule.created_at.desc())
    )
    return result.scalars().all()


@router.post("/rules", response_model=AlertRuleOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(UserRole.manager))])
async def create_rule(body: AlertRuleCreate, user: CurrentUser, db: DB):
    rule = AlertRule(tenant_id=user.tenant_id, **body.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.patch("/rules/{rule_id}/toggle",
              dependencies=[Depends(require_roles(UserRole.manager))])
async def toggle_rule(rule_id: str, user: CurrentUser, db: DB):
    result = await db.execute(
        select(AlertRule).where(AlertRule.id == rule_id, AlertRule.tenant_id == user.tenant_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Regra não encontrada.")
    rule.is_active = not rule.is_active
    await db.commit()
    return {"id": rule.id, "is_active": rule.is_active}


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_roles(UserRole.admin))])
async def delete_rule(rule_id: str, user: CurrentUser, db: DB):
    result = await db.execute(
        select(AlertRule).where(AlertRule.id == rule_id, AlertRule.tenant_id == user.tenant_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Regra não encontrada.")
    await db.delete(rule)
    await db.commit()


@router.get("/events", response_model=list[AlertEventOut])
async def list_events(
    user: CurrentUser, db: DB,
    active_only: bool = False,
    limit: int = 50,
):
    q = (
        select(AlertEvent)
        .where(AlertEvent.tenant_id == user.tenant_id)
        .order_by(AlertEvent.triggered_at.desc())
        .limit(limit)
    )
    if active_only:
        q = q.where(AlertEvent.resolved_at.is_(None))
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/events/{event_id}/resolve",
             dependencies=[Depends(require_roles(UserRole.operator))])
async def resolve_event(event_id: str, user: CurrentUser, db: DB):
    result = await db.execute(
        select(AlertEvent).where(AlertEvent.id == event_id, AlertEvent.tenant_id == user.tenant_id)
    )
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Evento não encontrado.")
    event.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    return {"id": event.id, "resolved_at": event.resolved_at}
