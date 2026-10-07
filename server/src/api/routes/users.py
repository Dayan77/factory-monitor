"""
Users of the caller's tenant (Sprint 4: several roles per user, area = lines, on-call flag).

  GET    /api/users/            admin, manager, maintenance_planner, production_supervisor (assignment lists)
                                filters: role, line_id, on_call
  GET    /api/users/{id}        same
  POST   /api/users/            admin
  PATCH  /api/users/{id}        admin (everything); manager / maintenance_planner: only line_ids and on_call
                                (on-call roster) of technicians/supervisors
  DELETE /api/users/{id}        admin (deactivates; sessions revoked)
  Sprint 7 (docs/security.md, docs/privacy.md):
  GET    /api/users/invitations             admin — pending/accepted invitations
  POST   /api/users/invitations             admin — {email, name?, role, roles?, line_ids?} → e-mailed link (7 days)
  DELETE /api/users/invitations/{id}        admin — revoke
  POST   /api/users/{id}/anonymize          admin — LGPD erasure (pseudonymized; history kept)
  POST   /api/users/{id}/mfa/reset          admin — turns MFA off for a user who lost the device
  POST   /api/users/{id}/sessions/revoke    admin — signs the user out everywhere
Every change of roles, activation, MFA or invitation is written to audit_events.
Responses keep `role` (primary) and add `roles[]`, `line_ids[]`, `on_call`.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, has_any, hash_password, require_any, require_roles
from ...db import get_db
from ...models import Line, User, UserRole
from ...models.tenant import Tenant
from ...config import settings
from ...models import MfaRecoveryCode, UserInvitation
from ...services.security import notify, passwords, privacy, sessions
from ...services.security.notify import audit
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



# ── invitations (Sprint 7) ───────────────────────────────────────

AdminUser = Annotated[User, Depends(require_roles(UserRole.admin))]


class InvitationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    name: Optional[str] = Field(default=None, max_length=255)
    role: UserRole = UserRole.operator
    roles: Optional[list[UserRole]] = None
    line_ids: list[str] = []


def _inv_out(i: UserInvitation, link: Optional[str] = None) -> dict:
    now = datetime.now(timezone.utc)
    state = ("accepted" if i.accepted_at else "revoked" if i.revoked_at else
             "expired" if i.expires_at <= now else "pending")
    out = {"id": i.id, "email": i.email, "name": i.name, "role": i.role, "roles": list(i.roles or []),
           "line_ids": list(i.line_ids or []), "state": state, "invited_by": i.invited_by, "user_id": i.user_id,
           "created_at": i.created_at, "expires_at": i.expires_at, "accepted_at": i.accepted_at}
    if link:
        out["invite_url"] = link
    return out


def _no_api_token(user: User) -> None:
    if getattr(user, "api_token_id", None):
        raise HTTPException(status_code=403, detail="Tokens de API não gerenciam usuários.")


@router.get("/invitations")
async def list_invitations(user: AdminUser, db: DB):
    rows = (await db.execute(select(UserInvitation).where(UserInvitation.tenant_id == user.tenant_id)
                             .order_by(UserInvitation.created_at.desc()).limit(500))).scalars().all()
    return [_inv_out(i) for i in rows]


@router.post("/invitations", status_code=201)
async def create_invitation(body: InvitationIn, user: AdminUser, request: Request, background: BackgroundTasks,
                            db: DB):
    """Invites someone into the tenant: the e-mailed link (also returned once as `invite_url`, to share by
    another channel) lets the person choose the password (policy applies) — the admin never knows it."""
    _no_api_token(user)
    if body.role == UserRole.superadmin or (body.roles and UserRole.superadmin in body.roles):
        raise HTTPException(status_code=403, detail="Papel superadmin não é atribuído por aqui.")
    email = str(body.email)
    if (await db.execute(select(User.id).where(func.lower(User.email) == email.lower()))).first():
        raise HTTPException(status_code=409, detail="E-mail já cadastrado.")
    tenant = await db.get(Tenant, user.tenant_id)
    count = (await db.execute(select(func.count(User.id)).where(User.tenant_id == user.tenant_id))).scalar_one()
    if tenant is not None and count >= tenant.max_users:
        raise HTTPException(status_code=422, detail=f"Limite de {tenant.max_users} usuários atingido.")
    probe = User(tenant_id=user.tenant_id, email=email, password_hash="!", name=body.name or email,
                 role=body.role)
    await set_roles(db, probe, body.role, body.roles, body.line_ids, None)   # validates roles and line_ids
    now = datetime.now(timezone.utc)
    await db.execute(update(UserInvitation).where(
        UserInvitation.tenant_id == user.tenant_id, func.lower(UserInvitation.email) == email.lower(),
        UserInvitation.accepted_at.is_(None), UserInvitation.revoked_at.is_(None)).values(revoked_at=now))
    token = secrets.token_urlsafe(32)
    inv = UserInvitation(tenant_id=user.tenant_id, email=email, name=body.name, role=probe.role.value,
                         roles=list(probe.roles or []), line_ids=list(probe.line_ids or []),
                         token_hash=hashlib.sha256(token.encode()).hexdigest(), invited_by=user.id, created_at=now,
                         expires_at=now + timedelta(hours=settings.invite_ttl_hours))
    db.add(inv)
    await db.flush()
    await audit(db, user.tenant_id, user.id, "user.invitation_created", "invitation", inv.id,
                {"role": inv.role, "roles": inv.roles}, request=request)
    await db.commit()
    background.add_task(notify.send_invite_email, email, tenant.name if tenant else "", user.name, token)
    return _inv_out(inv, notify.invite_link(token))


@router.delete("/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(invitation_id: str, user: AdminUser, request: Request, db: DB):
    _no_api_token(user)
    inv = (await db.execute(select(UserInvitation).where(UserInvitation.id == invitation_id,
                                                         UserInvitation.tenant_id == user.tenant_id))
           ).scalar_one_or_none() if is_uuid(invitation_id) else None
    if inv is None:
        raise HTTPException(status_code=404, detail="Convite não encontrado.")
    if inv.accepted_at is None and inv.revoked_at is None:
        inv.revoked_at = datetime.now(timezone.utc)
        await audit(db, user.tenant_id, user.id, "user.invitation_revoked", "invitation", inv.id, request=request)
    await db.commit()
    return Response(status_code=204)


@router.get("/{user_id}", response_model=UserOut, dependencies=[Depends(require_any(*LISTERS))])
async def get_user(user_id: str, user: CurrentUser, db: DB):
    return await _get_user(db, user_id, user.tenant_id)


@router.post("/", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, user: Annotated[User, Depends(require_roles(UserRole.admin))], db: DB,
                      request: Request):
    if body.role == UserRole.superadmin or (body.roles and UserRole.superadmin in body.roles):
        raise HTTPException(status_code=403, detail="Papel superadmin não é atribuído por aqui.")
    tenant = await db.get(Tenant, user.tenant_id)
    count = (await db.execute(select(func.count(User.id)).where(User.tenant_id == user.tenant_id))).scalar_one()
    if tenant is not None and count >= tenant.max_users:
        raise HTTPException(status_code=422, detail=f"Limite de {tenant.max_users} usuários atingido.")
    if (await db.execute(select(User.id).where(User.email == body.email))).first():
        raise HTTPException(status_code=409, detail="E-mail já cadastrado.")
    try:
        passwords.check_policy(body.password, [body.email, body.name, tenant.name if tenant else None])
    except passwords.PasswordPolicyError as e:
        raise HTTPException(status_code=422, detail=str(e))
    u = User(tenant_id=user.tenant_id, email=body.email, password_hash=hash_password(body.password), name=body.name,
             role=body.role, password_changed_at=datetime.now(timezone.utc))
    await set_roles(db, u, body.role, body.roles, body.line_ids, body.on_call)
    db.add(u)
    await db.flush()
    await audit(db, user.tenant_id, user.id, "user.created", "user", u.id,
                {"roles": list(u.roles or []), "via": "api_token" if getattr(user, "api_token_id", None) else "admin"},
                request=request)
    await db.commit()
    await db.refresh(u)
    return u


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: str, body: UserUpdate, user: CurrentUser, db: DB, request: Request):
    target = await _get_user(db, user_id, user.tenant_id)
    before = {"roles": sorted(target.role_set), "is_active": target.is_active, "line_ids": list(target.line_ids or []),
              "on_call": bool(target.on_call)}
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
    after = {"roles": sorted(target.role_set), "is_active": target.is_active, "line_ids": list(target.line_ids or []),
             "on_call": bool(target.on_call)}
    changed = {k: [before[k], after[k]] for k in before if before[k] != after[k]}
    if changed:
        action = ("user.roles_changed" if "roles" in changed else
                  "user.activation_changed" if "is_active" in changed else "user.roster_changed")
        await audit(db, user.tenant_id, user.id, action, "user", target.id, changed, request=request)
    if before["is_active"] and not target.is_active:
        await sessions.revoke_all(db, target.id, "deactivated")
    await db.commit()
    await db.refresh(target)
    return target


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(user_id: str, user: Annotated[User, Depends(require_roles(UserRole.admin))], db: DB,
                          request: Request):
    target = await _get_user(db, user_id, user.tenant_id)
    if target.id == user.id:
        raise HTTPException(status_code=422, detail="Não é possível desativar o próprio usuário.")
    if target.role == UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Permissão insuficiente.")
    was = target.is_active
    target.is_active = False
    await sessions.revoke_all(db, target.id, "deactivated")
    if was:
        await audit(db, user.tenant_id, user.id, "user.activation_changed", "user", target.id,
                    {"is_active": [True, False]}, request=request)
    await db.commit()


async def _admin_target(db: AsyncSession, user_id: str, user: User) -> User:
    _no_api_token(user)
    target = await _get_user(db, user_id, user.tenant_id)
    if target.role == UserRole.superadmin and user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Permissão insuficiente.")
    return target


@router.post("/{user_id}/anonymize", status_code=204)
async def anonymize(user_id: str, user: AdminUser, request: Request, db: DB):
    """LGPD erasure by the tenant admin: the account is pseudonymized (docs/privacy.md §3) — irreversible."""
    target = await _admin_target(db, user_id, user)
    if target.id == user.id:
        raise HTTPException(status_code=422, detail="Use 'Excluir minha conta' para a própria conta.")
    if target.anonymized_at is None:
        await privacy.anonymize_user(db, target, user.id, "admin")
        await audit(db, user.tenant_id, user.id, "user.anonymized", "user", target.id, {"by": "admin"},
                    request=request)
    await db.commit()
    return Response(status_code=204)


@router.post("/{user_id}/mfa/reset", status_code=204)
async def reset_mfa(user_id: str, user: AdminUser, request: Request, db: DB):
    target = await _admin_target(db, user_id, user)
    target.mfa_secret_enc = target.mfa_pending_enc = None
    target.mfa_enabled_at = None
    target.mfa_last_step = None
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == target.id))
    await sessions.revoke_all(db, target.id, "mfa_reset")
    await audit(db, user.tenant_id, user.id, "user.mfa_reset", "user", target.id, request=request)
    await db.commit()
    return Response(status_code=204)


@router.post("/{user_id}/sessions/revoke", status_code=204)
async def revoke_user_sessions(user_id: str, user: AdminUser, request: Request, db: DB):
    target = await _admin_target(db, user_id, user)
    n = await sessions.revoke_all(db, target.id, "admin_revoked")
    target.tokens_valid_after = datetime.now(timezone.utc)
    await audit(db, user.tenant_id, user.id, "user.sessions_revoked", "user", target.id, {"sessions": n},
                request=request)
    await db.commit()
    return Response(status_code=204)
