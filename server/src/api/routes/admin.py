"""
Rotas exclusivas do superadmin — gerenciamento de tenants e usuários globais.
Todos os endpoints requerem role superadmin.
"""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_db
from ...models import User, UserRole
from ...models.tenant import Tenant
from ...models.machine import Machine
from ...auth import get_current_user, require_roles, hash_password
from .users import set_roles
from ..schemas import (
    TenantOut, TenantWithStats, TenantCreate, TenantUpdate,
    UserOut, UserCreate, UserUpdate,
)

router = APIRouter()

SuperAdmin = Annotated[User, Depends(require_roles(UserRole.superadmin))]
DB         = Annotated[AsyncSession, Depends(get_db)]


# ── helpers ───────────────────────────────────────────────────────

async def _get_tenant_or_404(tenant_id: str, db: AsyncSession) -> Tenant:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant não encontrado.")
    return tenant


# ── tenants ───────────────────────────────────────────────────────

@router.get("/tenants", response_model=list[TenantWithStats])
async def list_tenants(_: SuperAdmin, db: DB):
    tenants_result = await db.execute(select(Tenant).order_by(Tenant.name))
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
async def create_tenant(body: TenantCreate, _: SuperAdmin, db: DB):
    # Verifica slug único
    existing = await db.execute(select(Tenant).where(Tenant.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug já em uso.")

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
    )
    db.add(admin_user)
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
async def update_tenant(tenant_id: str, body: TenantUpdate, _: SuperAdmin, db: DB):
    tenant = await _get_tenant_or_404(tenant_id, db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(tenant, field, value)
    # Se reativando, limpa o motivo de suspensão
    if body.is_active is True and tenant.suspended_reason and body.suspended_reason is None:
        tenant.suspended_reason = None
    await db.commit()
    await db.refresh(tenant)
    return tenant


@router.delete("/tenants/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(tenant_id: str, _: SuperAdmin, db: DB):
    tenant = await _get_tenant_or_404(tenant_id, db)
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
async def create_tenant_user(tenant_id: str, body: UserCreate, _: SuperAdmin, db: DB):
    tenant = await _get_tenant_or_404(tenant_id, db)

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
    existing = await db.execute(
        select(User).where(User.tenant_id == tenant_id, User.email == body.email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="E-mail já cadastrado neste tenant.")

    user = User(
        tenant_id=tenant_id,
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        role=body.role,
    )
    await set_roles(db, user, body.role, body.roles, body.line_ids, body.on_call)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.patch("/tenants/{tenant_id}/users/{user_id}", response_model=UserOut)
async def update_tenant_user(tenant_id: str, user_id: str, body: UserUpdate, _: SuperAdmin, db: DB):
    await _get_tenant_or_404(tenant_id, db)
    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")

    data = body.model_dump(exclude_none=True)
    await set_roles(db, user, data.pop("role", None), data.pop("roles", None), data.pop("line_ids", None),
                    data.pop("on_call", None))
    for field, value in data.items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/tenants/{tenant_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant_user(tenant_id: str, user_id: str, _: SuperAdmin, db: DB):
    await _get_tenant_or_404(tenant_id, db)
    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
    await db.delete(user)
    await db.commit()
