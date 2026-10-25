"""
Authentication (Sprint 7 hardening — docs/security.md §1).

  POST /api/auth/login                     e-mail + password → tokens, or {mfa_required, mfa_token} (MFA enabled)
  POST /api/auth/mfa/verify                mfa_token + TOTP code (or recovery_code) → tokens
  POST /api/auth/refresh                   rotates the refresh token (single use; reuse revokes the session)
  POST /api/auth/logout                    revokes the current session                          (auth)
  POST /api/auth/logout-all                revokes every session of the user ("sair de todos")   (auth)
  GET  /api/auth/me
  POST /api/auth/register                  public trial signup (password policy, rate limit)
  POST /api/auth/password-reset/request    always 202 (no user enumeration); e-mails a 15-min single-use link
  POST /api/auth/password-reset/confirm    token + new password → 204 (all sessions revoked)
  GET  /api/auth/invitations/{token}       invitation preview (e-mail, tenant) for the signup page
  POST /api/auth/invitations/accept        token + password → account created in the inviting tenant + tokens

Brute force: failures per account and per IP lock with exponential backoff (429 + Retry-After, same answer for
unknown e-mails); every login, failure, lock, MFA step, reset and refresh-token reuse is audited.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from jose import JWTError
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, hash_password, verify_password
from ...auth.security import TYP_MFA, TYP_RESET, create_token, decode_token, dummy_verify, needs_rehash
from ...config import settings
from ...db import get_db
from ...models import (AuthSession, MfaRecoveryCode, PasswordResetToken, User, UserInvitation, UserRole)
from ...models.tenant import Tenant, TenantPlan
from ...services.security import notify, passwords, sessions, throttle, totp
from ...services.security.notify import audit, client_ip
from ...services.validation import is_uuid
from ..schemas import UserOut

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]

BAD_CREDENTIALS = "Credenciais inválidas."
LOCKED_MSG = "Muitas tentativas de acesso. Aguarde e tente novamente."
RATE_MSG = "Muitas requisições. Tente novamente mais tarde."
GENERIC_RESET = {"status": "accepted",
                 "message": "Se o e-mail estiver cadastrado, enviaremos um link para redefinir a senha."}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginBody(Strict):
    email: EmailStr
    password: str = Field(max_length=256)


class RefreshBody(Strict):
    refresh_token: str = Field(max_length=4096)


class MfaVerifyBody(Strict):
    mfa_token: str = Field(max_length=4096)
    code: Optional[str] = Field(default=None, max_length=16)
    recovery_code: Optional[str] = Field(default=None, max_length=32)


class ResetRequestBody(Strict):
    email: EmailStr


class ResetConfirmBody(Strict):
    token: str = Field(max_length=4096)
    new_password: str = Field(max_length=256)


class AcceptInviteBody(Strict):
    token: str = Field(max_length=256)
    password: str = Field(max_length=256)
    name: Optional[str] = Field(default=None, max_length=255)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    session_id: str


def _locked(seconds: float) -> HTTPException:
    return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=LOCKED_MSG,
                         headers=throttle.retry_after(seconds))


def _ua(request: Request) -> Optional[str]:
    return (request.headers.get("user-agent") or "")[:300] or None


async def _rate(db: AsyncSession, key: str, limit: int, window_s: float) -> None:
    if not await throttle.hit(db, key, limit, window_s):
        await db.commit()
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=RATE_MSG,
                            headers=throttle.retry_after(window_s))


async def _check_tenant(db: AsyncSession, user: User) -> None:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one_or_none()
    if not tenant or not tenant.is_active:
        reason = tenant.suspended_reason if tenant else None
        detail = f"Conta suspensa. {reason}" if reason else "Conta suspensa. Entre em contato com o suporte."
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


async def _finish_login(db: AsyncSession, user: User, request: Request, mfa: bool,
                        session_id: Optional[str] = None) -> dict:
    await db.execute(update(User).where(User.id == user.id).values(last_login_at=datetime.now(timezone.utc)))
    out = await sessions.start_session(db, user, client_ip(request), _ua(request), mfa=mfa, session_id=session_id)
    await audit(db, user.tenant_id, user.id, "auth.login", "session", out["session_id"], {"mfa": mfa},
                request=request)
    await db.commit()
    return out


@router.post("/login", response_model=None)
async def login(body: LoginBody, request: Request, db: DB):
    """Returns `{access_token, refresh_token, token_type, expires_in, session_id}`; when the user has MFA enabled,
    `{mfa_required: true, mfa_token, methods}` instead (then POST /api/auth/mfa/verify)."""
    ip = client_ip(request)
    akey, ikey = throttle.account_key(str(body.email)), throttle.ip_key(ip)
    await _rate(db, throttle.hkey("rl-login", ip), settings.login_rate_per_ip_per_min, 60)
    user = (await db.execute(select(User).where(User.email == body.email, User.is_active.is_(True)))
            ).scalar_one_or_none()
    wait = max(await throttle.locked(db, akey), await throttle.locked(db, ikey))
    if wait > 0:
        dummy_verify(body.password)
        if user is not None:
            await audit(db, user.tenant_id, user.id, "auth.login_blocked", data={"retry_after_s": int(wait)},
                        request=request)
        await db.commit()
        raise _locked(wait)

    ok = verify_password(body.password, user.password_hash) if user is not None else (dummy_verify(body.password)
                                                                                       or False)
    if not ok:
        lock_a = await throttle.failure(db, akey, settings.login_max_failures_account)
        lock_i = await throttle.failure(db, ikey, settings.login_max_failures_ip)
        if user is not None:
            await audit(db, user.tenant_id, user.id, "auth.login_failed", data={"reason": "bad_password"},
                        request=request)
            if lock_a or lock_i:
                await audit(db, user.tenant_id, user.id, "auth.lockout",
                            data={"lock_s": int(max(lock_a, lock_i)), "scope": "account" if lock_a else "ip"},
                            request=request)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=BAD_CREDENTIALS)

    await _check_tenant(db, user)
    await throttle.reset(db, akey)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    if user.mfa_enabled_at is not None:
        import uuid
        mfa_token = create_token({"sub": user.id, "tenant_id": user.tenant_id, "sid": str(uuid.uuid4())},
                                 timedelta(minutes=5), TYP_MFA)
        await audit(db, user.tenant_id, user.id, "auth.mfa_challenge", request=request)
        await db.commit()
        return {"mfa_required": True, "mfa_token": mfa_token, "methods": ["totp", "recovery_code"]}
    return await _finish_login(db, user, request, mfa=False)


@router.post("/mfa/verify", response_model=TokenOut)
async def mfa_verify(body: MfaVerifyBody, request: Request, db: DB):
    try:
        claims = decode_token(body.mfa_token, typ=TYP_MFA)
    except JWTError:
        raise HTTPException(status_code=401, detail="Verificação expirada. Entre novamente.")
    sid, sub = str(claims.get("sid") or ""), str(claims.get("sub") or "")
    user = (await db.execute(select(User).where(User.id == sub, User.is_active.is_(True))
                             .with_for_update())).scalar_one_or_none() if is_uuid(sub) else None
    if user is None or user.mfa_enabled_at is None or not is_uuid(sid):
        raise HTTPException(status_code=401, detail="Verificação expirada. Entre novamente.")
    if await db.get(AuthSession, sid) is not None:          # the challenge was already used
        raise HTTPException(status_code=401, detail="Verificação expirada. Entre novamente.")
    mkey = throttle.hkey("mfa", user.id)
    wait = await throttle.locked(db, mkey)
    if wait > 0:
        raise _locked(wait)
    ok, method = False, "totp"
    if body.code:
        secret = totp.decrypt(user.mfa_secret_enc)
        step = totp.verify(secret, body.code, user.mfa_last_step) if secret else None
        if step is not None:
            user.mfa_last_step = step
            ok = True
    elif body.recovery_code:
        method = "recovery_code"
        rc = (await db.execute(select(MfaRecoveryCode).where(
            MfaRecoveryCode.user_id == user.id, MfaRecoveryCode.code_hash == totp.hash_recovery(body.recovery_code),
            MfaRecoveryCode.used_at.is_(None)).with_for_update())).scalar_one_or_none()
        if rc is not None:
            rc.used_at = datetime.now(timezone.utc)
            ok = True
    if not ok:
        lock = await throttle.failure(db, mkey, settings.login_max_failures_account)
        await audit(db, user.tenant_id, user.id, "auth.mfa_failed", data={"method": method, "lock_s": int(lock)},
                    request=request)
        await db.commit()
        raise HTTPException(status_code=401, detail="Código inválido.")
    await _check_tenant(db, user)
    await throttle.reset(db, mkey)
    if method == "recovery_code":
        await audit(db, user.tenant_id, user.id, "auth.mfa_recovery_used", request=request)
    return await _finish_login(db, user, request, mfa=True, session_id=sid)


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshBody, request: Request, db: DB):
    try:
        user, out = await sessions.rotate(db, body.refresh_token)
    except sessions.RefreshError as e:
        if e.reason == "reuse" and e.session is not None:
            await audit(db, e.session.tenant_id, e.session.user_id, "auth.refresh_reuse", "session", e.session.id,
                        {"action": "session_revoked"}, request=request)
        await db.commit()
        detail = "Sessão encerrada. Entre novamente." if e.reason in ("reuse", "revoked", "legacy") \
            else "Refresh token inválido."
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
    await db.commit()
    return out


@router.post("/logout", status_code=204)
async def logout(user: Annotated[User, Depends(get_current_user)], request: Request, db: DB):
    if getattr(user, "api_token_id", None):
        raise HTTPException(status_code=403, detail="Tokens de API não encerram sessões (revogue o token).")
    sid = getattr(user, "session_id", None)
    if sid:
        s = await db.get(AuthSession, sid)
        if s is not None and s.user_id == user.id:
            await sessions.revoke_session(db, s, "logout")
    await audit(db, user.tenant_id, user.id, "auth.logout", "session", sid, request=request)
    await db.commit()
    return Response(status_code=204)


@router.post("/logout-all", status_code=204)
async def logout_all(user: Annotated[User, Depends(get_current_user)], request: Request, db: DB):
    """Signs out every device: all sessions revoked and every token issued so far rejected."""
    if getattr(user, "api_token_id", None):
        raise HTTPException(status_code=403, detail="Tokens de API não encerram sessões.")
    n = await sessions.revoke_all(db, user.id, "logout_all")
    await db.execute(update(User).where(User.id == user.id).values(tokens_valid_after=datetime.now(timezone.utc)))
    await audit(db, user.tenant_id, user.id, "auth.logout_all", data={"sessions": n}, request=request)
    await db.commit()
    return Response(status_code=204)


@router.get("/me", response_model=UserOut)
async def me(user: Annotated[User, Depends(get_current_user)]):
    return user


# ── Registro público (self-service trial) ────────────────────────

class RegisterRequest(Strict):
    company_name:   str = Field(max_length=255)
    admin_name:     str = Field(max_length=255)
    admin_email:    EmailStr
    admin_password: str = Field(max_length=256)

    @field_validator("admin_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        try:
            passwords.check_policy(v)
        except passwords.PasswordPolicyError as e:
            raise ValueError(str(e))
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


def _policy_422(e: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(e))


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    """Registro público — cria tenant em trial por 14 dias."""
    await _rate(db, throttle.hkey("rl-register", client_ip(request)), 10, 3600)
    try:
        passwords.check_policy(body.admin_password, [body.admin_email, body.admin_name, body.company_name])
    except passwords.PasswordPolicyError as e:
        await db.commit()
        raise _policy_422(e)

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
        await db.commit()
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
        roles=[UserRole.admin.value],
        password_changed_at=datetime.now(timezone.utc),
    )
    db.add(admin)
    await db.flush()
    await audit(db, tenant.id, admin.id, "tenant.registered", "tenant", tenant.id, request=request)
    await db.commit()

    return {"message": "Conta criada com sucesso!", "tenant_slug": slug}


# ── Password reset (no enumeration, single-use 15-min token) ─────

def _pw_fingerprint(user: User) -> str:
    import hashlib
    return hashlib.sha256((user.password_hash or "").encode()).hexdigest()[:16]


@router.post("/password-reset/request", status_code=202)
async def password_reset_request(body: ResetRequestBody, request: Request, background: BackgroundTasks, db: DB):
    """Always 202 with the same body, whether or not the e-mail exists (no user enumeration)."""
    ip = client_ip(request)
    await _rate(db, throttle.hkey("rl-reset-ip", ip), settings.password_reset_per_ip_per_hour, 3600)
    email_ok = await throttle.hit(db, throttle.hkey("rl-reset-email", str(body.email).lower()),
                                  settings.password_reset_per_email_per_hour, 3600)
    user = (await db.execute(select(User).where(User.email == body.email, User.is_active.is_(True)))
            ).scalar_one_or_none()
    if user is not None and email_ok and user.anonymized_at is None:
        now = datetime.now(timezone.utc)
        row = PasswordResetToken(user_id=user.id, created_at=now,
                                 expires_at=now + timedelta(minutes=settings.password_reset_ttl_min))
        db.add(row)
        await db.flush()
        token = create_token({"sub": user.id, "rid": row.id, "pwh": _pw_fingerprint(user)},
                             timedelta(minutes=settings.password_reset_ttl_min), TYP_RESET)
        await audit(db, user.tenant_id, user.id, "auth.password_reset_requested", request=request)
        background.add_task(notify.send_reset_email, user.email, user.name, token)
    await db.commit()
    return GENERIC_RESET


@router.post("/password-reset/confirm", status_code=204)
async def password_reset_confirm(body: ResetConfirmBody, request: Request, db: DB):
    invalid = HTTPException(status_code=400, detail="Link de redefinição inválido ou expirado.")
    await _rate(db, throttle.hkey("rl-reset-confirm", client_ip(request)), 30, 3600)
    try:
        claims = decode_token(body.token, typ=TYP_RESET)
    except JWTError:
        await db.commit()
        raise invalid
    rid = str(claims.get("rid") or "")
    row = (await db.execute(select(PasswordResetToken).where(PasswordResetToken.id == rid)
                            .with_for_update())).scalar_one_or_none() if is_uuid(rid) else None
    now = datetime.now(timezone.utc)
    if row is None or row.used_at is not None or row.expires_at <= now or row.user_id != claims.get("sub"):
        await db.commit()
        raise invalid
    user = (await db.execute(select(User).where(User.id == row.user_id, User.is_active.is_(True))
                             .with_for_update())).scalar_one_or_none()
    if user is None or claims.get("pwh") != _pw_fingerprint(user):
        await db.commit()
        raise invalid
    try:
        await passwords.set_password(db, user, body.new_password)
    except passwords.PasswordPolicyError as e:
        raise _policy_422(e)
    row.used_at = now
    # every other reset link of the user dies too
    await db.execute(update(PasswordResetToken).where(PasswordResetToken.user_id == user.id,
                                                      PasswordResetToken.used_at.is_(None)).values(used_at=now))
    await sessions.revoke_all(db, user.id, "password_reset")
    user.tokens_valid_after = now
    await throttle.reset(db, throttle.account_key(user.email))
    await audit(db, user.tenant_id, user.id, "auth.password_reset", request=request)
    await db.commit()
    return Response(status_code=204)


# ── Invitations (signup by invitation token) ─────────────────────

async def _invitation(db: AsyncSession, token: str, lock: bool = False) -> Optional[UserInvitation]:
    import hashlib
    h = hashlib.sha256((token or "").encode()).hexdigest()
    q = select(UserInvitation).where(UserInvitation.token_hash == h)
    if lock:
        q = q.with_for_update()
    inv = (await db.execute(q)).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if inv is None or inv.accepted_at is not None or inv.revoked_at is not None or inv.expires_at <= now:
        return None
    return inv


@router.get("/invitations/{token}")
async def invitation_preview(token: str, request: Request, db: DB):
    await _rate(db, throttle.hkey("rl-invite", client_ip(request)), 60, 3600)
    inv = await _invitation(db, token)
    await db.commit()
    if inv is None:
        raise HTTPException(status_code=404, detail="Convite inválido ou expirado.")
    tenant = await db.get(Tenant, inv.tenant_id)
    return {"email": inv.email, "name": inv.name, "tenant_name": tenant.name if tenant else None,
            "role": inv.role, "expires_at": inv.expires_at}


@router.post("/invitations/accept", response_model=TokenOut, status_code=201)
async def invitation_accept(body: AcceptInviteBody, request: Request, db: DB):
    await _rate(db, throttle.hkey("rl-invite", client_ip(request)), 60, 3600)
    inv = await _invitation(db, body.token, lock=True)
    if inv is None:
        await db.commit()
        raise HTTPException(status_code=404, detail="Convite inválido ou expirado.")
    tenant = await db.get(Tenant, inv.tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(status_code=403, detail="Conta suspensa. Entre em contato com o suporte.")
    if (await db.execute(select(User.id).where(func.lower(User.email) == inv.email.lower()))).first():
        raise HTTPException(status_code=409, detail="E-mail já cadastrado.")
    count = (await db.execute(select(func.count(User.id)).where(User.tenant_id == inv.tenant_id,
                                                                User.is_active.is_(True)))).scalar_one()
    if count >= tenant.max_users:
        raise HTTPException(status_code=422, detail=f"Limite de {tenant.max_users} usuários atingido.")
    name = (body.name or inv.name or inv.email.split("@")[0]).strip()[:255]
    try:
        passwords.check_policy(body.password, [inv.email, name, tenant.name])
    except passwords.PasswordPolicyError as e:
        raise _policy_422(e)
    from .users import set_roles
    u = User(tenant_id=inv.tenant_id, email=inv.email, name=name, password_hash=hash_password(body.password),
             role=UserRole(inv.role), password_changed_at=datetime.now(timezone.utc))
    await set_roles(db, u, inv.role, list(inv.roles or []) or None, list(inv.line_ids or []), None)
    db.add(u)
    await db.flush()
    inv.accepted_at = datetime.now(timezone.utc)
    inv.user_id = u.id
    await audit(db, u.tenant_id, u.id, "user.invitation_accepted", "invitation", inv.id,
                {"role": inv.role, "invited_by": inv.invited_by}, request=request)
    return await _finish_login(db, u, request, mfa=False)
