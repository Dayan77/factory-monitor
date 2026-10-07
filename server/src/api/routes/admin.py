"""
Rotas exclusivas do superadmin — gerenciamento de tenants e usuários globais.
Todos os endpoints requerem role superadmin.
"""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_db
from ...models import User, UserRole
from ...models.tenant import Tenant
from ...models.machine import Machine
from ...auth import get_current_user, require_roles, hash_password
from ...services.security import passwords
from ...services.security.notify import audit
from ...services.validation import is_uuid
from .users import set_roles
from ..schemas import (
    TenantOut, TenantWithStats, TenantCreate, TenantUpdate,
    UserOut, UserCreate, UserUpdate,
)

router = APIRouter()

SuperAdmin = Annotated[User, Depends(require_roles(UserRole.superadmin))]
DB         = Annotated[AsyncSession, Depends(get_db)]


# ── helpers ───────────────────────────────────────────────────────

def _policy(password: str, *context) -> None:
    try:
        passwords.check_policy(password, context)
    except passwords.PasswordPolicyError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


async def _email_taken(db: AsyncSession, email: str) -> bool:
    # e-mail is the login: unique across tenants (login by e-mail must find one account)
    return (await db.execute(select(User.id).where(func.lower(User.email) == email.lower()))).first() is not None


async def _get_tenant_or_404(tenant_id: str, db: AsyncSession) -> Tenant:
    if not is_uuid(tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant não encontrado.")
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant não encontrado.")
    return tenant


# ── tenants ───────────────────────────────────────────────────────

@router.get("/tenants", response_model=list[TenantWithStats])
async def list_tenants(_: SuperAdmin, db: DB):
    # the public demo tenant (Sprint 6) is not a client: never listed here
    tenants_result = await db.execute(select(Tenant).where(Tenant.is_demo.is_(False)).order_by(Tenant.name))
    tenants = tenants_result.scalars().all()

    # Busca contagens em uma query
    user_counts = dict(
        (await db.execute(
            select(User.tenant_id, func.count(User.id))
            .group_by(User.tenant_id)
        )).all()
    )
    machine_counts = dict(
        (await db.execute(
            select(Machine.tenant_id, func.count(Machine.id))
            .group_by(Machine.tenant_id)
        )).all()
    )

    return [
        TenantWithStats(
            **TenantOut.model_validate(t).model_dump(),
            user_count=user_counts.get(t.id, 0),
            machine_count=machine_counts.get(t.id, 0),
        )
        for t in tenants
    ]


@router.post("/tenants", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(body: TenantCreate, me: SuperAdmin, db: DB, request: Request):
    # Verifica slug único
    existing = await db.execute(select(Tenant).where(Tenant.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug já em uso.")
    _policy(body.admin_password, body.admin_email, body.admin_name, body.name)
    if await _email_taken(db, body.admin_email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="E-mail já cadastrado.")

    tenant = Tenant(
        name=body.name,
        slug=body.slug,
        plan=body.plan,
        trial_ends_at=body.trial_ends_at,
        max_users=body.max_users,
        max_machines=body.max_machines,
    )
    db.add(tenant)
    await db.flush()  # gera tenant.id antes de criar o usuário

    admin_user = User(
        tenant_id=tenant.id,
        email=body.admin_email,
        password_hash=hash_password(body.admin_password),
        name=body.admin_name,
        role=UserRole.admin,
        roles=[UserRole.admin.value],
    )
    db.add(admin_user)
    await db.flush()
    await audit(db, tenant.id, me.id, "tenant.created", "tenant", tenant.id, {"by": "superadmin"}, request=request)
    await db.commit()
    await db.refresh(tenant)
    return tenant


@router.get("/tenants/{tenant_id}", response_model=TenantWithStats)
async def get_tenant(tenant_id: str, _: SuperAdmin, db: DB):
    tenant = await _get_tenant_or_404(tenant_id, db)

    user_count = (await db.execute(
        select(func.count(User.id)).where(User.tenant_id == tenant_id)
    )).scalar_one()
    machine_count = (await db.execute(
        select(func.count(Machine.id)).where(Machine.tenant_id == tenant_id)
    )).scalar_one()

    return TenantWithStats(
        **TenantOut.model_validate(tenant).model_dump(),
        user_count=user_count,
        machine_count=machine_count,
    )


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
async def update_tenant(tenant_id: str, body: TenantUpdate, me: SuperAdmin, db: DB, request: Request):
    tenant = await _get_tenant_or_404(tenant_id, db)
    changes = body.model_dump(exclude_none=True)
    for field, value in changes.items():
        setattr(tenant, field, value)
    await audit(db, tenant.id, me.id, "tenant.updated", "tenant", tenant.id,
                {"fields": sorted(changes), "is_active": changes.get("is_active")}, request=request)
    if changes.get("is_active") is False:
        # suspension signs every user of the tenant out (tokens already issued stop working)
        from datetime import datetime, timezone

        from sqlalchemy import update

        from ...models import AuthSession
        now = datetime.now(timezone.utc)
        await db.execute(update(AuthSession).where(AuthSession.tenant_id == tenant.id, AuthSession.revoked_at.is_(None))
                         .values(revoked_at=now, revoked_reason="tenant_suspended"))
        await db.execute(update(User).where(User.tenant_id == tenant.id).values(tokens_valid_after=now))
    # Se reativando, limpa o motivo de suspensão
    if body.is_active is True and tenant.suspended_reason and body.suspended_reason is None:
        tenant.suspended_reason = None
    await db.commit()
    await db.refresh(tenant)
    return tenant


@router.delete("/tenants/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(tenant_id: str, me: SuperAdmin, db: DB, request: Request):
    tenant = await _get_tenant_or_404(tenant_id, db)
    if tenant.id == me.tenant_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Não é possível excluir o tenant da própria conta.")
    # the trail of the deletion lives in the superadmin's tenant (the deleted tenant's rows cascade away)
    await audit(db, me.tenant_id, me.id, "tenant.deleted", "tenant", tenant.id, {"slug": tenant.slug},
                request=request)
    await db.delete(tenant)
    await db.commit()


# ── usuários de um tenant ─────────────────────────────────────────

@router.get("/tenants/{tenant_id}/users", response_model=list[UserOut])
async def list_tenant_users(tenant_id: str, _: SuperAdmin, db: DB):
    await _get_tenant_or_404(tenant_id, db)
    result = await db.execute(
        select(User).where(User.tenant_id == tenant_id).order_by(User.name)
    )
    return result.scalars().all()


@router.post("/tenants/{tenant_id}/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_tenant_user(tenant_id: str, body: UserCreate, me: SuperAdmin, db: DB, request: Request):
    tenant = await _get_tenant_or_404(tenant_id, db)
    _policy(body.password, body.email, body.name, tenant.name)

    # Verifica limite de usuários do plano
    user_count = (await db.execute(
        select(func.count(User.id)).where(User.tenant_id == tenant_id)
    )).scalar_one()
    if user_count >= tenant.max_users:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Limite de {tenant.max_users} usuários atingido para este tenant.",
        )

    # Email único dentro do tenant
    if await _email_taken(db, body.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="E-mail já cadastrado.")

    user = User(
        tenant_id=tenant_id,
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        role=body.role,
    )
    await set_roles(db, user, body.role, body.roles, body.line_ids, body.on_call)
    db.add(user)
    await db.flush()
    await audit(db, tenant.id, me.id, "user.created", "user", user.id,
                {"roles": list(user.roles or []), "by": "superadmin"}, request=request)
    await db.commit()
    await db.refresh(user)
    return user


@router.patch("/tenants/{tenant_id}/users/{user_id}", response_model=UserOut)
async def update_tenant_user(tenant_id: str, user_id: str, body: UserUpdate, me: SuperAdmin, db: DB,
                             request: Request):
    await _get_tenant_or_404(tenant_id, db)
    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    ) if is_uuid(user_id) else None
    user = result.scalar_one_or_none() if result is not None else None
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")

    data = body.model_dump(exclude_none=True)
    await set_roles(db, user, data.pop("role", None), data.pop("roles", None), data.pop("line_ids", None),
                    data.pop("on_call", None))
    before = sorted(user.role_set)
    for field, value in data.items():
        setattr(user, field, value)
    await audit(db, tenant_id, me.id, "user.roles_changed" if sorted(user.role_set) != before else "user.updated",
                "user", user.id, {"roles": [before, sorted(user.role_set)], "by": "superadmin",
                                  "fields": sorted(body.model_fields_set)}, request=request)
    if user.is_active is False:
        from ...services.security.sessions import revoke_all
        await revoke_all(db, user.id, "deactivated")
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/tenants/{tenant_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant_user(tenant_id: str, user_id: str, me: SuperAdmin, db: DB, request: Request):
    await _get_tenant_or_404(tenant_id, db)
    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    ) if is_uuid(user_id) else None
    user = result.scalar_one_or_none() if result is not None else None
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
    if user.id == me.id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Não é possível excluir o próprio usuário.")
    await audit(db, tenant_id, me.id, "user.deleted", "user", user.id, {"by": "superadmin"}, request=request)
    await db.delete(user)
    await db.commit()
