"""Server-side sessions and refresh-token rotation with reuse detection (docs/security.md §1.1).

A sign-in creates an `auth_sessions` row (the refresh-token family). Access tokens carry `sid`; refresh tokens carry
`sid` + `jti`. `/api/auth/refresh` consumes the refresh token (single use) and issues a new pair in the same family.
Presenting an already-used refresh token again:
  * within REFRESH_REUSE_GRACE_S of its rotation (two tabs refreshing at once) → a new pair, nothing revoked;
  * later → token theft assumed: the whole session (family) is revoked and audited (`auth.refresh_reuse`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from jose import JWTError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth.security import TYP_REFRESH, create_access_token, create_refresh_token, decode_token
from ...config import settings
from ...models import AuthSession, RefreshToken, User
from ..validation import is_uuid


class RefreshError(Exception):
    def __init__(self, reason: str, session: Optional[AuthSession] = None):
        super().__init__(reason)
        self.reason = reason
        self.session = session


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ip_prefix(ip: Optional[str]) -> Optional[str]:
    """Truncated IP shown in the session list (LGPD: never the full address)."""
    if not ip:
        return None
    if ":" in ip:
        parts = ip.split(":")
        return ":".join(parts[:3]) + "::/48"
    parts = ip.split(".")
    return ".".join(parts[:3]) + ".x" if len(parts) == 4 else None


def _claims(user: User, sid: str) -> dict:
    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    return {"sub": user.id, "tenant_id": user.tenant_id, "role": role, "sid": sid}


async def _issue(db: AsyncSession, user: User, session: AuthSession, now: datetime) -> tuple[dict, str]:
    jti = uuid4().hex
    db.add(RefreshToken(jti=jti, session_id=session.id, issued_at=now, expires_at=session.expires_at))
    await db.flush()
    claims = _claims(user, session.id)
    ttl = max(timedelta(seconds=1), session.expires_at - now)
    return {"access_token": create_access_token(claims),
            "refresh_token": create_refresh_token({**claims, "jti": jti}, ttl=ttl),
            "token_type": "bearer", "expires_in": settings.jwt_access_token_expire_minutes * 60,
            "session_id": session.id}, jti


async def start_session(db: AsyncSession, user: User, ip: Optional[str] = None, user_agent: Optional[str] = None,
                        mfa: bool = False, session_id: Optional[str] = None) -> dict:
    now = _now()
    s = AuthSession(id=session_id or str(uuid4()), tenant_id=user.tenant_id, user_id=user.id, created_at=now,
                    last_used_at=now, expires_at=now + timedelta(days=settings.jwt_refresh_token_expire_days),
                    ip_prefix=ip_prefix(ip), user_agent=(user_agent or "")[:300] or None, mfa=mfa)
    db.add(s)
    await db.flush()
    out, _ = await _issue(db, user, s, now)
    return out


async def rotate(db: AsyncSession, refresh_token: str) -> tuple[User, dict]:
    """Consumes a refresh token. Raises RefreshError(reason, session) — reason 'reuse' means the family was revoked
    (the caller commits and audits)."""
    try:
        claims = decode_token(refresh_token, typ=TYP_REFRESH)
    except JWTError:
        raise RefreshError("invalid")
    sid, jti, sub = claims.get("sid"), claims.get("jti"), claims.get("sub")
    if not sid or not jti or not sub:
        raise RefreshError("legacy")             # pre-rotation refresh tokens: sign in again
    if not is_uuid(str(sid)) or not is_uuid(str(sub)):
        raise RefreshError("invalid")
    now = _now()
    s = (await db.execute(select(AuthSession).where(AuthSession.id == sid).with_for_update())).scalar_one_or_none()
    if s is None or s.user_id != sub:
        raise RefreshError("invalid")
    if s.revoked_at is not None or s.expires_at <= now:
        raise RefreshError("revoked", s)
    rt = (await db.execute(select(RefreshToken).where(RefreshToken.jti == jti, RefreshToken.session_id == sid)
                           .with_for_update())).scalar_one_or_none()
    if rt is None:
        raise RefreshError("invalid", s)
    if rt.used_at is not None:
        if (now - rt.used_at).total_seconds() > settings.refresh_reuse_grace_s:
            await revoke_session(db, s, "refresh_reuse", now)
            raise RefreshError("reuse", s)
    user = (await db.execute(select(User).where(User.id == sub, User.is_active.is_(True)))).scalar_one_or_none()
    if user is None:
        raise RefreshError("user", s)
    from ...models import Tenant
    if not (await db.execute(select(Tenant.is_active).where(Tenant.id == user.tenant_id))).scalar_one_or_none():
        raise RefreshError("revoked", s)
    if user.tokens_valid_after is not None and s.created_at < user.tokens_valid_after:
        raise RefreshError("revoked", s)
    out, new_jti = await _issue(db, user, s, now)
    if rt.used_at is None:
        rt.used_at = now
        rt.replaced_by = new_jti
    s.last_used_at = now
    return user, out


async def revoke_session(db: AsyncSession, s: AuthSession, reason: str, now: Optional[datetime] = None) -> None:
    if s.revoked_at is None:
        s.revoked_at = now or _now()
        s.revoked_reason = reason[:32]


async def revoke_all(db: AsyncSession, user_id: str, reason: str, except_sid: Optional[str] = None) -> int:
    q = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if except_sid:
        q = q.where(AuthSession.id != except_sid)
    res = await db.execute(q.values(revoked_at=_now(), revoked_reason=reason[:32]))
    return res.rowcount or 0


async def list_sessions(db: AsyncSession, user_id: str) -> list[AuthSession]:
    now = _now()
    return list((await db.execute(select(AuthSession).where(
        AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None), AuthSession.expires_at > now)
        .order_by(AuthSession.last_used_at.desc().nullslast()))).scalars().all())


async def access_valid(db: AsyncSession, user: User, claims: dict) -> bool:
    """Session / global cutoff checks of an access token already verified (signature, expiry, kind)."""
    sid = claims.get("sid")
    tva = user.tokens_valid_after
    iat = claims.get("iat")
    if not sid and tva is not None and (not isinstance(iat, (int, float)) or iat <= int(tva.timestamp())):
        return False                       # sid-less token issued before "sair de todos" / password reset
    if sid:
        if not is_uuid(str(sid)):
            return False
        row = (await db.execute(select(AuthSession.revoked_at, AuthSession.expires_at, AuthSession.user_id)
                                .where(AuthSession.id == str(sid)))).first()
        if row is None or row[0] is not None or row[1] <= _now() or row[2] != user.id:
            return False
    return True


async def purge(db: AsyncSession, now: Optional[datetime] = None) -> int:
    """Drops sessions expired / revoked more than 30 days ago (their refresh tokens go with them)."""
    from sqlalchemy import delete, or_
    now = now or _now()
    cut = now - timedelta(days=30)
    res = await db.execute(delete(AuthSession).where(or_(AuthSession.expires_at < cut, AuthSession.revoked_at < cut)))
    return res.rowcount or 0
