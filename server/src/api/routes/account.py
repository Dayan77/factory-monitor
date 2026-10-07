"""
The signed-in user's own account (Sprint 7 — docs/security.md, docs/privacy.md). Not available to API tokens
(they act for their creator, never as him on his personal data) nor to the public demo.

  GET    /api/account/me                    profile + security status (MFA, password age)
  PATCH  /api/account/me                    {name}
  POST   /api/account/password              {current_password, new_password} → other sessions revoked
  GET    /api/account/sessions              active sessions (device, /24 IP prefix, last use, current)
  DELETE /api/account/sessions/{id}         revoke one session
  DELETE /api/account/sessions              revoke every other session (keeps the current one)
  GET    /api/account/mfa                   {enabled, required, recovery_codes_left}
  POST   /api/account/mfa/enroll            {password} → {secret, otpauth_uri} (QR code: render the URI)
  POST   /api/account/mfa/confirm           {code} → MFA on + 10 recovery codes (shown once, stored hashed)
  POST   /api/account/mfa/recovery-codes    {code} → new set of recovery codes (old ones die)
  POST   /api/account/mfa/disable           {password, code} (refused while the tenant requires MFA for admins)
  GET    /api/account/export                LGPD: every personal datum about the user (JSON download)
  DELETE /api/account                       LGPD: {password, confirm: "EXCLUIR"} → account anonymized
  GET    /api/account/tenant-security       tenant policy (admin)
  PUT    /api/account/tenant-security       {require_mfa_admins} (admin)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, require_roles, verify_password
from ...db import get_db
from ...models import AuthSession, MfaRecoveryCode, TenantSecuritySettings, User, UserRole
from ...config import settings
from ...services.security import passwords, privacy, sessions, throttle, totp
from ...services.security.notify import audit
from ...services.validation import is_uuid

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]


async def account_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    if getattr(user, "api_token_id", None):
        raise HTTPException(status_code=403, detail="Tokens de API não acessam dados pessoais da conta.")
    if "demo_viewer" in user.role_set:
        raise HTTPException(status_code=403, detail="Modo demonstração: área indisponível.")
    return user


Me = Annotated[User, Depends(account_user)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileIn(Strict):
    name: str = Field(min_length=1, max_length=255)


class PasswordIn(Strict):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class PasswordOnly(Strict):
    password: str = Field(max_length=256)


class CodeIn(Strict):
    code: str = Field(max_length=16)


class DisableIn(Strict):
    password: str = Field(max_length=256)
    code: str = Field(max_length=32)


class DeleteIn(Strict):
    password: str = Field(max_length=256)
    confirm: Literal["EXCLUIR"]


class TenantSecurityIn(Strict):
    require_mfa_admins: bool


def _iso(d):
    return d.isoformat() if d is not None else None


async def _reload(db: AsyncSession, user: User) -> User:
    u = (await db.execute(select(User).where(User.id == user.id, User.tenant_id == user.tenant_id)
                          .with_for_update())).scalar_one_or_none()
    if u is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    return u


async def _mfa_required(db: AsyncSession, user: User) -> bool:
    if not (user.role_set & {"admin", "superadmin"}):
        return False
    return bool((await db.execute(select(TenantSecuritySettings.require_mfa_admins).where(
        TenantSecuritySettings.tenant_id == user.tenant_id))).scalar_one_or_none())


async def _codes_left(db: AsyncSession, user_id: str) -> int:
    return (await db.execute(select(func.count(MfaRecoveryCode.id)).where(
        MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None)))).scalar_one()


def _wrong_password() -> HTTPException:
    return HTTPException(status_code=403, detail="Senha atual incorreta.")


# Re-authentication inside the account area (current password, TOTP / recovery code) is throttled per user like
# the login: whoever holds a stolen access token cannot use these endpoints as a password / code oracle.
def _reauth_key(kind: str, user: User) -> str:
    return throttle.hkey("acct-" + kind if kind == "pw" else "mfa", user.id)


async def _reauth_guard(db: AsyncSession, user: User, kind: str) -> None:
    wait = await throttle.locked(db, _reauth_key(kind, user))
    if wait > 0:
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde e tente novamente.",
                            headers=throttle.retry_after(wait))


async def _reauth_failed(db: AsyncSession, user: User, kind: str, request: Request, exc: HTTPException):
    lock = await throttle.failure(db, _reauth_key(kind, user), settings.login_max_failures_account)
    await audit(db, user.tenant_id, user.id, "account.reauth_failed", "user", user.id,
                {"factor": "password" if kind == "pw" else "mfa", "lock_s": int(lock)}, request=request)
    await db.commit()
    raise exc


async def _check_password(db: AsyncSession, u: User, password: str, request: Request) -> None:
    await _reauth_guard(db, u, "pw")
    if not verify_password(password, u.password_hash):
        await _reauth_failed(db, u, "pw", request, _wrong_password())
    await throttle.reset(db, _reauth_key("pw", u))


@router.get("/me")
async def get_me(user: Me, db: DB):
    return {"id": user.id, "tenant_id": user.tenant_id, "email": user.email, "name": user.name,
            "role": user.role.value, "roles": user.all_roles, "created_at": _iso(user.created_at),
            "last_login_at": _iso(user.last_login_at), "password_changed_at": _iso(user.password_changed_at),
            "mfa_enabled": user.mfa_enabled_at is not None, "mfa_required": await _mfa_required(db, user)}


@router.patch("/me")
async def patch_me(body: ProfileIn, user: Me, request: Request, db: DB):
    u = await _reload(db, user)
    u.name = body.name.strip()
    await audit(db, u.tenant_id, u.id, "account.profile_updated", "user", u.id, {"fields": ["name"]}, request=request)
    await db.commit()
    return await get_me(u, db)


@router.post("/password", status_code=204)
async def change_password(body: PasswordIn, user: Me, request: Request, db: DB):
    u = await _reload(db, user)
    await _check_password(db, u, body.current_password, request)
    try:
        await passwords.set_password(db, u, body.new_password)
    except passwords.PasswordPolicyError as e:
        raise HTTPException(status_code=422, detail=str(e))
    n = await sessions.revoke_all(db, u.id, "password_changed", except_sid=getattr(user, "session_id", None))
    await audit(db, u.tenant_id, u.id, "account.password_changed", "user", u.id, {"sessions_revoked": n},
                request=request)
    await db.commit()
    return Response(status_code=204)


# ── sessions ─────────────────────────────────────────────────────

@router.get("/sessions")
async def list_sessions(user: Me, db: DB):
    cur = getattr(user, "session_id", None)
    return [{"id": s.id, "created_at": _iso(s.created_at), "last_used_at": _iso(s.last_used_at),
             "expires_at": _iso(s.expires_at), "ip_prefix": s.ip_prefix, "user_agent": s.user_agent,
             "mfa": s.mfa, "current": s.id == cur} for s in await sessions.list_sessions(db, user.id)]


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(session_id: str, user: Me, request: Request, db: DB):
    s = (await db.execute(select(AuthSession).where(AuthSession.id == session_id, AuthSession.user_id == user.id,
                                                    AuthSession.tenant_id == user.tenant_id))
         ).scalar_one_or_none() if is_uuid(session_id) else None
    if s is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    await sessions.revoke_session(db, s, "user_revoked")
    await audit(db, user.tenant_id, user.id, "account.session_revoked", "session", s.id, request=request)
    await db.commit()
    return Response(status_code=204)


@router.delete("/sessions", status_code=204)
async def revoke_other_sessions(user: Me, request: Request, db: DB):
    n = await sessions.revoke_all(db, user.id, "user_revoked", except_sid=getattr(user, "session_id", None))
    await audit(db, user.tenant_id, user.id, "account.sessions_revoked", data={"sessions": n}, request=request)
    await db.commit()
    return Response(status_code=204)


# ── MFA (TOTP) ───────────────────────────────────────────────────

@router.get("/mfa")
async def mfa_status(user: Me, db: DB):
    return {"enabled": user.mfa_enabled_at is not None, "enabled_at": _iso(user.mfa_enabled_at),
            "required": await _mfa_required(db, user), "pending": user.mfa_pending_enc is not None,
            "recovery_codes_left": await _codes_left(db, user.id) if user.mfa_enabled_at else 0}


@router.post("/mfa/enroll")
async def mfa_enroll(body: PasswordOnly, user: Me, request: Request, db: DB):
    u = await _reload(db, user)
    await _check_password(db, u, body.password, request)
    if u.mfa_enabled_at is not None:
        raise HTTPException(status_code=409, detail="MFA já está ativo. Desative antes de cadastrar outro.")
    secret = totp.new_secret()
    u.mfa_pending_enc = totp.encrypt(secret)
    await audit(db, u.tenant_id, u.id, "account.mfa_enroll_started", "user", u.id, request=request)
    await db.commit()
    return {"secret": secret, "otpauth_uri": totp.otpauth_uri(secret, u.email), "digits": totp.DIGITS,
            "period": totp.STEP_S}


async def _new_codes(db: AsyncSession, user_id: str) -> list[str]:
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    codes = totp.new_recovery_codes()
    db.add_all([MfaRecoveryCode(user_id=user_id, code_hash=totp.hash_recovery(c)) for c in codes])
    await db.flush()
    return codes


@router.post("/mfa/confirm")
async def mfa_confirm(body: CodeIn, user: Me, request: Request, db: DB):
    u = await _reload(db, user)
    secret = totp.decrypt(u.mfa_pending_enc)
    if secret is None:
        raise HTTPException(status_code=409, detail="Nenhum cadastro de MFA em andamento.")
    await _reauth_guard(db, u, "mfa")
    step = totp.verify(secret, body.code)
    if step is None:
        await _reauth_failed(db, u, "mfa", request, _bad_code())
    await throttle.reset(db, _reauth_key("mfa", u))
    u.mfa_secret_enc, u.mfa_pending_enc = u.mfa_pending_enc, None
    u.mfa_enabled_at = datetime.now(timezone.utc)
    u.mfa_last_step = step
    codes = await _new_codes(db, u.id)
    await audit(db, u.tenant_id, u.id, "account.mfa_enabled", "user", u.id, request=request)
    await db.commit()
    return {"enabled": True, "recovery_codes": codes}


def _bad_code() -> HTTPException:
    return HTTPException(status_code=422, detail="Código inválido.")


def _check_code(u: User, code: str) -> Optional[int]:
    secret = totp.decrypt(u.mfa_secret_enc)
    return totp.verify(secret, code, u.mfa_last_step) if secret else None


@router.post("/mfa/recovery-codes")
async def mfa_new_codes(body: CodeIn, user: Me, request: Request, db: DB):
    u = await _reload(db, user)
    if u.mfa_enabled_at is None:
        raise HTTPException(status_code=409, detail="MFA não está ativo.")
    await _reauth_guard(db, u, "mfa")
    step = _check_code(u, body.code)
    if step is None:
        await _reauth_failed(db, u, "mfa", request, _bad_code())
    await throttle.reset(db, _reauth_key("mfa", u))
    u.mfa_last_step = step
    codes = await _new_codes(db, u.id)
    await audit(db, u.tenant_id, u.id, "account.mfa_recovery_regenerated", "user", u.id, request=request)
    await db.commit()
    return {"recovery_codes": codes}


@router.post("/mfa/disable", status_code=204)
async def mfa_disable(body: DisableIn, user: Me, request: Request, db: DB):
    u = await _reload(db, user)
    if u.mfa_enabled_at is None:
        raise HTTPException(status_code=409, detail="MFA não está ativo.")
    await _check_password(db, u, body.password, request)
    await _reauth_guard(db, u, "mfa")
    step = _check_code(u, body.code)
    if step is None:
        rc = (await db.execute(select(MfaRecoveryCode).where(
            MfaRecoveryCode.user_id == u.id, MfaRecoveryCode.code_hash == totp.hash_recovery(body.code),
            MfaRecoveryCode.used_at.is_(None)))).scalar_one_or_none()
        if rc is None:
            await _reauth_failed(db, u, "mfa", request, _bad_code())
    await throttle.reset(db, _reauth_key("mfa", u))
    if await _mfa_required(db, u):
        raise HTTPException(status_code=422, detail="O tenant exige MFA para administradores.")
    u.mfa_secret_enc = u.mfa_pending_enc = None
    u.mfa_enabled_at = None
    u.mfa_last_step = None
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == u.id))
    await audit(db, u.tenant_id, u.id, "account.mfa_disabled", "user", u.id, request=request)
    await db.commit()
    return Response(status_code=204)


# ── LGPD ─────────────────────────────────────────────────────────

@router.get("/export")
async def export_data(user: Me, request: Request, db: DB):
    data = await privacy.export_user(db, user)
    await audit(db, user.tenant_id, user.id, "account.data_exported", "user", user.id, request=request)
    await db.commit()
    return JSONResponse(data, headers={
        "Content-Disposition": f'attachment; filename="factoryconnect-meus-dados-{user.id[:8]}.json"',
        "Cache-Control": "no-store"})


@router.delete("", status_code=204)
async def delete_account(body: DeleteIn, user: Me, request: Request, db: DB):
    """Self-service deletion: the account is anonymized (history kept under a pseudonym, docs/privacy.md §3).
    The last active admin of a tenant cannot delete himself (another admin must exist first)."""
    u = await _reload(db, user)
    await _check_password(db, u, body.password, request)
    if u.role == UserRole.superadmin:
        raise HTTPException(status_code=422, detail="Conta superadmin não é excluída por aqui.")
    if "admin" in u.role_set:
        others = (await db.execute(select(func.count(User.id)).where(
            User.tenant_id == u.tenant_id, User.id != u.id, User.is_active.is_(True),
            (User.role == UserRole.admin) | User.roles.any("admin")))).scalar_one()
        if not others:
            raise HTTPException(status_code=422, detail="Você é o único administrador ativo: promova outro antes.")
    await privacy.anonymize_user(db, u, u.id, "self_service")
    await audit(db, u.tenant_id, u.id, "account.deleted", "user", u.id, {"mode": "anonymized", "by": "self"})
    await db.commit()
    return Response(status_code=204)


# ── tenant policy ────────────────────────────────────────────────

@router.get("/tenant-security", dependencies=[Depends(require_roles(UserRole.admin))])
async def get_tenant_security(user: Me, db: DB):
    row = await db.get(TenantSecuritySettings, user.tenant_id)
    return {"require_mfa_admins": bool(row and row.require_mfa_admins),
            "updated_at": _iso(row.updated_at) if row else None}


@router.put("/tenant-security", dependencies=[Depends(require_roles(UserRole.admin))])
async def put_tenant_security(body: TenantSecurityIn, user: Me, request: Request, db: DB):
    if body.require_mfa_admins and user.mfa_enabled_at is None:
        raise HTTPException(status_code=422, detail="Ative o MFA na sua conta antes de exigi-lo dos administradores.")
    row = await db.get(TenantSecuritySettings, user.tenant_id)
    before = bool(row and row.require_mfa_admins)
    if row is None:
        row = TenantSecuritySettings(tenant_id=user.tenant_id)
        db.add(row)
    row.require_mfa_admins = body.require_mfa_admins
    row.updated_by = user.id
    await audit(db, user.tenant_id, user.id, "tenant.security_settings_changed", "tenant", user.tenant_id,
                {"require_mfa_admins": [before, body.require_mfa_admins]}, request=request)
    await db.commit()
    return await get_tenant_security(user, db)
