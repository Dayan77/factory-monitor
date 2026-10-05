"""
FastAPI dependencies for authentication and RBAC.

Usage in routes:
    current_user = Depends(get_current_user)                     # any authenticated user
    _            = Depends(require_roles(UserRole.admin))         # admin or superadmin only
    _            = Depends(require_any(*PLANNERS))                # any of these roles (maintenance)

A user may hold several roles (`users.roles`, Sprint 4); `role` stays as the primary role.
Hierarchy checks (`require_roles`) use the highest level among the user's roles; the maintenance
roles (planner, technician, production supervisor) sit at the operator level of the hierarchy and
get their extra permissions through `require_any` / `has_any`.

Integration API tokens (`Authorization: Bearer fct_…`, created by a tenant admin) authenticate as
a transient service principal of that tenant with the maintenance-planner permissions; scope
`read` allows only safe methods (GET/HEAD/OPTIONS), `write` allows everything else too.
"""

import hashlib
from datetime import datetime, timezone
from typing import Annotated, Iterable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import ApiToken, User, UserRole
from .security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

API_TOKEN_PREFIX = "fct_"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

ROLE_HIERARCHY = {
    UserRole.operator:   0,
    UserRole.maintenance_planner: 0,
    UserRole.technician: 0,
    UserRole.production_supervisor: 0,
    UserRole.manager:    1,
    UserRole.admin:      2,
    UserRole.superadmin: 3,
}
_LEVEL = {r.value: lvl for r, lvl in ROLE_HIERARCHY.items()}


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def role_level(user: User) -> int:
    return max((_LEVEL.get(r, 0) for r in user.role_set), default=0)


def has_any(user: User, roles: Iterable) -> bool:
    """True if the user holds any of `roles` (admin/superadmin always pass)."""
    rs = user.role_set
    if rs & {"admin", "superadmin"}:
        return True
    want = {r.value if isinstance(r, UserRole) else str(r) for r in roles}
    return bool(rs & want)


async def _api_token_user(token: str, request: Request, db: AsyncSession) -> User:
    exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token de API inválido ou revogado.",
                        headers={"WWW-Authenticate": "Bearer"})
    row = (await db.execute(select(ApiToken).where(ApiToken.token_hash == hash_api_token(token)))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None or row.revoked_at is not None or (row.expires_at is not None and row.expires_at <= now):
        raise exc
    # the token acts on behalf of the admin who created it: it dies with that account (deactivated/removed)
    creator_ok = row.created_by is not None and (await db.execute(select(User.id).where(
        User.id == row.created_by, User.tenant_id == row.tenant_id, User.is_active.is_(True)))).first() is not None
    if not creator_ok:
        raise exc
    if request.method.upper() not in SAFE_METHODS and "write" not in (row.scopes or []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token de API sem escopo 'write'.")
    await db.execute(update(ApiToken).where(ApiToken.id == row.id).values(last_used_at=now))
    await db.commit()
    # Transient principal (never added to the session): acts with planner permissions.
    principal = User(id=row.created_by, tenant_id=row.tenant_id, email=f"api-token+{row.prefix}@integration",
                     password_hash="!", name=f"API: {row.name}", role=UserRole.maintenance_planner,
                     roles=[UserRole.maintenance_planner.value], line_ids=[], on_call=False, is_active=True,
                     created_at=row.created_at)
    principal.api_token_id = row.id  # type: ignore[attr-defined]
    return principal


async def get_current_user(
    request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
    db:    Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if token.startswith(API_TOKEN_PREFIX):
        return await _api_token_user(token, request, db)
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido ou expirado.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        user_id: str = payload.get("sub")
        if not user_id:
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if not user:
        raise credentials_exc
    return user


def require_roles(*roles: UserRole):
    """Dependency factory — passes if user's (highest) role meets the minimum required."""
    min_level = min(ROLE_HIERARCHY[r] for r in roles)

    async def _dep(user: Annotated[User, Depends(get_current_user)]) -> User:
        if role_level(user) < min_level:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permissão insuficiente.")
        return user

    return _dep


def require_any(*roles):
    """Dependency factory — passes if the user holds any of `roles` (admin/superadmin always)."""

    async def _dep(user: Annotated[User, Depends(get_current_user)]) -> User:
        if not has_any(user, roles):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permissão insuficiente.")
        return user

    return _dep
