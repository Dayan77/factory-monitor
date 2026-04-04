"""
FastAPI dependencies for authentication and RBAC.

Usage in routes:
    current_user = Depends(get_current_user)                     # any authenticated user
    _            = Depends(require_roles(UserRole.admin))         # admin or superadmin only
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User, UserRole
from .security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

ROLE_HIERARCHY = {
    UserRole.operator:   0,
    UserRole.manager:    1,
    UserRole.admin:      2,
    UserRole.superadmin: 3,
}


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db:    Annotated[AsyncSession, Depends(get_db)],
) -> User:
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
    """Dependency factory — passes if user's role meets the minimum required."""
    min_level = min(ROLE_HIERARCHY[r] for r in roles)

    async def _dep(user: Annotated[User, Depends(get_current_user)]) -> User:
        if ROLE_HIERARCHY[user.role] < min_level:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permissão insuficiente.")
        return user

    return _dep
