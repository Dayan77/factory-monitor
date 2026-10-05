"""
Users of the caller's tenant (Sprint 4: several roles per user, area = lines, on-call flag).

  GET    /api/users/            admin, manager, maintenance_planner, production_supervisor (assignment lists)
                                filters: role, line_id, on_call
  GET    /api/users/{id}        same
  POST   /api/users/            admin
  PATCH  /api/users/{id}        admin (everything); manager / maintenance_planner: only line_ids and on_call
                                (on-call roster) of technicians/supervisors
  DELETE /api/users/{id}        admin (deactivates)
Responses keep `role` (primary) and add `roles[]`, `line_ids[]`, `on_call`.
"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, has_any, hash_password, require_any, require_roles
from ...db import get_db
from ...models import Line, User, UserRole
from ...models.tenant import Tenant
from ...services.validation import is_uuid
from ..schemas import UserCreate, UserOut, UserUpdate

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]
LISTERS = ("manager", "maintenance_planner", "production_supervisor")


async def set_roles(db: AsyncSession, user: User, role=None, roles=None, line_ids=None, on_call=None) -> None:
    """Normalizes roles (primary `role` ∪ `roles`), validates line_ids against the user's tenant."""
    if role is not None:
        user.role = UserRole(role)
    if roles is not None or role is not None:
        cur = set(user.roles or []) if roles is None else {UserRole(r).value for r in roles}
        primary = UserRole(user.role).value
        if roles is not None and not cur:
            cur = {primary}
        if roles is not None and primary not in cur:
            # the primary role must be one of the roles: pick the highest of the list
            order = ["superadmin", "admin", "manager", "maintenance_planner", "production_supervisor", "technician",
                     "operator"]
            user.role = UserRole(next(r for r in order if r in cur))
            primary = user.role.value
        cur.add(primary)
        user.roles = sorted(cur)
    elif not user.roles and user.role is not None:
        user.roles = [UserRole(user.role).value]
    if line_ids is not None:
        ids = [str(x) for x in line_ids]
        if any(not is_uuid(x) for x in ids):
            raise HTTPException(status_code=422, detail="line_ids inválido.")
        if ids:
            found = set((await db.execute(select(Line.id).where(Line.tenant_id == user.tenant_id,
                                                                 Line.id.in_(ids)))).scalars().all())
            if found != set(ids):
                raise HTTPException(status_code=404, detail="line_ids: linha não encontrada.")
        user.line_ids = sorted(set(ids))
    if on_call is not None:
        user.on_call = bool(on_call)


async def _get_user(db: AsyncSession, user_id: str, tenant_id: str) -> User:
    u = (await db.execute(select(User).where(User.id == user_id, User.tenant_id == tenant_id))
         ).scalar_one_or_none() if is_uuid(user_id) else None
    if u is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    return u


@router.get("/", response_model=list[UserOut], dependencies=[Depends(require_any(*LISTERS))])
async def list_users(user: CurrentUser, db: DB, role: Optional[str] = None, line_id: Optional[str] = None,
                     on_call: Optional[bool] = None, include_inactive: bool = False):
    q = select(User).where(User.tenant_id == user.tenant_id)
    if not include_inactive:
        q = q.where(User.is_active.is_(True))
    rows = (await db.execute(q.order_by(User.name))).scalars().all()
    out = []
    for u in rows:
        if u.role == UserRole.superadmin:
            continue
        if role and role not in u.role_set:
            continue
        if line_id and u.line_ids and line_id not in {str(x) for x in u.line_ids}:
            continue
        if on_call is not None and bool(u.on_call) != on_call:
            continue
        out.append(u)
    return out


@router.get("/{user_id}", response_model=UserOut, dependencies=[Depends(require_any(*LISTERS))])
async def get_user(user_id: str, user: CurrentUser, db: DB):
    return await _get_user(db, user_id, user.tenant_id)


@router.post("/", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, user: Annotated[User, Depends(require_roles(UserRole.admin))], db: DB):
    if body.role == UserRole.superadmin or (body.roles and UserRole.superadmin in body.roles):
        raise HTTPException(status_code=403, detail="Papel superadmin não é atribuído por aqui.")
    tenant = await db.get(Tenant, user.tenant_id)
    count = (await db.execute(select(func.count(User.id)).where(User.tenant_id == user.tenant_id))).scalar_one()
    if tenant is not None and count >= tenant.max_users:
        raise HTTPException(status_code=422, detail=f"Limite de {tenant.max_users} usuários atingido.")
    if (await db.execute(select(User.id).where(User.email == body.email))).first():
        raise HTTPException(status_code=409, detail="E-mail já cadastrado.")
    u = User(tenant_id=user.tenant_id, email=body.email, password_hash=hash_password(body.password), name=body.name,
             role=body.role)
    await set_roles(db, u, body.role, body.roles, body.line_ids, body.on_call)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: str, body: UserUpdate, user: CurrentUser, db: DB):
    target = await _get_user(db, user_id, user.tenant_id)
    sent = body.model_fields_set
    admin = bool(user.role_set & {"admin", "superadmin"})
    if not admin:
        roster_only = sent <= {"line_ids", "on_call"}
        if not (has_any(user, ("manager", "maintenance_planner")) and roster_only
                and not (target.role_set & {"admin", "superadmin", "manager"})):
            raise HTTPException(status_code=403, detail="Permissão insuficiente.")
    if target.role == UserRole.superadmin and user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Permissão insuficiente.")
    if (body.role == UserRole.superadmin or (body.roles and UserRole.superadmin in body.roles)) \
            and user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Papel superadmin não é atribuído por aqui.")
    await set_roles(db, target, body.role if "role" in sent else None, body.roles if "roles" in sent else None,
                    body.line_ids if "line_ids" in sent else None, body.on_call if "on_call" in sent else None)
    if "name" in sent and body.name:
        target.name = body.name
    if "is_active" in sent and body.is_active is not None:
        if target.id == user.id and not body.is_active:
            raise HTTPException(status_code=422, detail="Não é possível desativar o próprio usuário.")
        target.is_active = body.is_active
    await db.commit()
    await db.refresh(target)
    return target


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(user_id: str, user: Annotated[User, Depends(require_roles(UserRole.admin))], db: DB):
    target = await _get_user(db, user_id, user.tenant_id)
    if target.id == user.id:
        raise HTTPException(status_code=422, detail="Não é possível desativar o próprio usuário.")
    if target.role == UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Permissão insuficiente.")
    target.is_active = False
    await db.commit()
