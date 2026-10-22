"""Operator PWA (docs/sprint6.md §3): line scope of a pure operator and idempotent client actions.

* A user whose only role is `operator` and who has lines assigned (`users.line_ids`) acts only on those lines:
  downtime list / justification / manual stop and production runs of another line → 403. An operator without
  assigned lines keeps the previous behaviour (all lines of the tenant). Other roles are not restricted here.
* `X-Client-Action-Id` (tablet offline queue): the first request with a key is executed and the key stored in the
  same transaction as its effect; a replay with the same key returns what was recorded — never a second effect.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ClientAction, Machine, User

LINE_FORBIDDEN = "Operador sem acesso a esta linha."
_ACTION_ID = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


def operator_lines(user: User) -> Optional[set[str]]:
    """Lines a pure operator is restricted to, or None (no restriction)."""
    if user.role_set != {"operator"}:
        return None
    lines = {str(x) for x in (user.line_ids or [])}
    return lines or None


async def check_machine(db: AsyncSession, user: User, machine_id: str) -> None:
    lines = operator_lines(user)
    if lines is None:
        return
    line_id = (await db.execute(select(Machine.line_id).where(Machine.id == machine_id,
                                                              Machine.tenant_id == user.tenant_id))).scalar_one_or_none()
    if line_id is not None and str(line_id) not in lines:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=LINE_FORBIDDEN)


def check_line(user: User, line_id: str) -> None:
    lines = operator_lines(user)
    if lines is not None and str(line_id) not in lines:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=LINE_FORBIDDEN)


async def allowed_machine_ids(db: AsyncSession, user: User) -> Optional[list[str]]:
    lines = operator_lines(user)
    if lines is None:
        return None
    return list((await db.execute(select(Machine.id).where(
        Machine.tenant_id == user.tenant_id, Machine.line_id.in_(list(lines))))).scalars())


def valid_action_id(action_id: Optional[str]) -> Optional[str]:
    if action_id is None:
        return None
    action_id = action_id.strip()
    if not _ACTION_ID.match(action_id):
        raise HTTPException(status_code=422, detail="X-Client-Action-Id inválido (8–64 caracteres [A-Za-z0-9._:-]).")
    return action_id


async def replayed(db: AsyncSession, user: User, action_id: Optional[str], endpoint: str) -> Optional[ClientAction]:
    """The stored action of this key, if it was already executed (same endpoint; another endpoint → 422)."""
    if not action_id:
        return None
    row = (await db.execute(select(ClientAction).where(
        ClientAction.tenant_id == user.tenant_id, ClientAction.user_id == user.id,
        ClientAction.action_id == action_id))).scalar_one_or_none()
    if row is not None and row.endpoint != endpoint:
        raise HTTPException(status_code=422, detail="X-Client-Action-Id já usado em outra ação.")
    return row


def remember(db: AsyncSession, user: User, action_id: Optional[str], endpoint: str, entity_id: str,
             status_code: int) -> None:
    """Adds the key to the current transaction (committed together with the effect)."""
    if action_id:
        db.add(ClientAction(tenant_id=user.tenant_id, user_id=user.id, action_id=action_id, endpoint=endpoint,
                            entity_id=entity_id, status_code=status_code))


def is_duplicate_key(e: IntegrityError) -> bool:
    return "uq_client_actions_key" in str(e.orig)
