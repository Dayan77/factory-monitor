import re
from datetime import datetime, timezone, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_db
from ...models import User, UserRole
from ...models.tenant import Tenant, TenantPlan
from ...auth import (
    verify_password, hash_password, create_access_token, create_refresh_token,
    decode_token, get_current_user,
)
from ..schemas import LoginRequest, TokenResponse, RefreshRequest, UserOut

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(
        select(User).where(User.email == body.email, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas.")

    # Verifica se o tenant está ativo
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant or not tenant.is_active:
        reason = tenant.suspended_reason if tenant else None
        detail = f"Conta suspensa. {reason}" if reason else "Conta suspensa. Entre em contato com o suporte."
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

    await db.execute(
        update(User).where(User.id == user.id).values(last_login_at=datetime.now(timezone.utc))
    )
    await db.commit()

    payload = {"sub": user.id, "tenant_id": user.tenant_id, "role": user.role.value}
    return TokenResponse(
        access_token=create_access_token(payload),
        refresh_token=create_refresh_token(payload),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        payload = decode_token(body.refresh_token)
        if not payload.get("refresh"):
            raise ValueError
    except (JWTError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token inválido.")

    result = await db.execute(select(User).where(User.id == payload["sub"], User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário não encontrado.")

    new_payload = {"sub": user.id, "tenant_id": user.tenant_id, "role": user.role.value}
    return TokenResponse(
        access_token=create_access_token(new_payload),
        refresh_token=create_refresh_token(new_payload),
    )


@router.get("/me", response_model=UserOut)
async def me(user: Annotated[User, Depends(get_current_user)]):
    return user


# ── Registro público (self-service trial) ────────────────────────

class RegisterRequest(BaseModel):
    company_name:   str
    admin_name:     str
    admin_email:    EmailStr
    admin_password: str

    @field_validator("admin_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Senha deve ter no mínimo 8 caracteres.")
        return v

    @field_validator("company_name")
    @classmethod
    def company_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Nome da empresa é obrigatório.")
        return v.strip()


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[àáâãä]", "a", slug)
    slug = re.sub(r"[èéêë]", "e", slug)
    slug = re.sub(r"[ìíîï]", "i", slug)
    slug = re.sub(r"[òóôõö]", "o", slug)
    slug = re.sub(r"[ùúûü]", "u", slug)
    slug = re.sub(r"[ç]", "c", slug)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:60]


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    """Registro público — cria tenant em trial por 14 dias."""

    base_slug = _slugify(body.company_name)
    if not base_slug:
        raise HTTPException(status_code=400, detail="Nome da empresa inválido.")

    # Garante slug único
    slug = base_slug
    suffix = 1
    while True:
        existing = await db.execute(select(Tenant).where(Tenant.slug == slug))
        if not existing.scalar_one_or_none():
            break
        slug = f"{base_slug}-{suffix}"
        suffix += 1

    # Verifica e-mail não duplicado globalmente
    email_exists = await db.execute(select(User).where(User.email == body.admin_email))
    if email_exists.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="E-mail já cadastrado.")

    tenant = Tenant(
        name=body.company_name,
        slug=slug,
        plan=TenantPlan.trial,
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=14),
        max_users=5,
        max_machines=10,
    )
    db.add(tenant)
    await db.flush()

    admin = User(
        tenant_id=tenant.id,
        email=body.admin_email,
        password_hash=hash_password(body.admin_password),
        name=body.admin_name,
        role=UserRole.admin,
    )
    db.add(admin)
    await db.commit()

    return {"message": "Conta criada com sucesso!", "tenant_slug": slug}
