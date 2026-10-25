"""LGPD (docs/privacy.md): personal-data export, account anonymization and retention jobs.

Anonymization keeps the `users` row (so audit trail, work orders, failures, downtime justifications and the rest of
the maintenance/production history keep pointing to the same — now pseudonymous — person) and erases what
identifies or belongs only to the person: e-mail, name, password, MFA, sessions, invitations, reset tokens,
notifications, notification preferences, push subscriptions and AI conversations.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings

log = structlog.get_logger()
EXPORT_AUDIT_LIMIT = 5000


def _iso(d) -> Optional[str]:
    return d.isoformat() if d is not None else None


def pseudonym(user_id: str) -> str:
    return "Usuário removido " + hashlib.sha256(("pseudo:" + user_id).encode()).hexdigest()[:8]


async def export_user(db: AsyncSession, user) -> dict:
    """Every personal datum the platform holds about `user` (art. 18 LGPD: confirmação e acesso), as JSON."""
    from ...models import (AiConversation, AiMessage, AuditEvent, AuthSession, MaintenanceAbsence, Notification,
                           NotificationPreference, PushSubscription, Tenant, UserInvitation, WorkOrder)
    tid, uid = user.tenant_id, user.id
    tenant = await db.get(Tenant, tid)
    sess = (await db.execute(select(AuthSession).where(AuthSession.user_id == uid, AuthSession.tenant_id == tid)
                             .order_by(AuthSession.created_at.desc()))).scalars().all()
    audit = (await db.execute(select(AuditEvent).where(AuditEvent.user_id == uid, AuditEvent.tenant_id == tid)
                              .order_by(AuditEvent.created_at.desc()).limit(EXPORT_AUDIT_LIMIT))).scalars().all()
    prefs = (await db.execute(select(NotificationPreference).where(NotificationPreference.user_id == uid,
                                                                   NotificationPreference.tenant_id == tid))
             ).scalar_one_or_none()
    pushes = (await db.execute(select(PushSubscription).where(PushSubscription.user_id == uid,
                                                              PushSubscription.tenant_id == tid))).scalars().all()
    notes = (await db.execute(select(Notification).where(Notification.user_id == uid, Notification.tenant_id == tid)
                              .order_by(Notification.created_at.desc()).limit(1000))).scalars().all()
    convs = (await db.execute(select(AiConversation).where(AiConversation.user_id == uid,
                                                           AiConversation.tenant_id == tid))).scalars().all()
    msgs = {}
    if convs:
        for m in (await db.execute(select(AiMessage).where(AiMessage.tenant_id == tid, AiMessage.conversation_id.in_(
                [c.id for c in convs])).order_by(AiMessage.created_at))).scalars().all():
            msgs.setdefault(m.conversation_id, []).append({"role": m.role, "content": m.content,
                                                           "created_at": _iso(m.created_at)})
    wos = (await db.execute(select(WorkOrder.id, WorkOrder.title, WorkOrder.state, WorkOrder.created_at,
                                   WorkOrder.assignee_id, WorkOrder.created_by)
                            .where(WorkOrder.tenant_id == tid,
                                   (WorkOrder.assignee_id == uid) | (WorkOrder.created_by == uid))
                            .limit(5000))).all()
    absences = (await db.execute(select(MaintenanceAbsence).where(MaintenanceAbsence.user_id == uid,
                                                                  MaintenanceAbsence.tenant_id == tid))).scalars().all()
    invites = (await db.execute(select(UserInvitation).where(UserInvitation.tenant_id == tid,
                                                             UserInvitation.invited_by == uid))).scalars().all()
    return {
        "format": "factoryconnect.personal-data-export/v1",
        "generated_at": _iso(datetime.now(timezone.utc)),
        "controller": {"tenant_id": tid, "tenant_name": tenant.name if tenant else None},
        "user": {"id": uid, "email": user.email, "name": user.name, "role": user.role.value
                 if hasattr(user.role, "value") else str(user.role), "roles": user.all_roles,
                 "line_ids": list(user.line_ids or []), "on_call": bool(user.on_call), "is_active": user.is_active,
                 "created_at": _iso(user.created_at), "last_login_at": _iso(user.last_login_at),
                 "password_changed_at": _iso(user.password_changed_at),
                 "mfa_enabled": user.mfa_enabled_at is not None, "mfa_enabled_at": _iso(user.mfa_enabled_at)},
        "sessions": [{"id": s.id, "created_at": _iso(s.created_at), "last_used_at": _iso(s.last_used_at),
                      "expires_at": _iso(s.expires_at), "revoked_at": _iso(s.revoked_at),
                      "revoked_reason": s.revoked_reason, "ip_prefix": s.ip_prefix, "user_agent": s.user_agent,
                      "mfa": s.mfa} for s in sess],
        "audit_events": [{"action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id,
                          "data": a.data, "created_at": _iso(a.created_at)} for a in audit],
        "notification_preferences": ({"channels": prefs.channels, "digest_enabled": prefs.digest_enabled}
                                     if prefs else None),
        "push_subscriptions": [{"id": p.id, "service": _host(p.endpoint), "user_agent": p.user_agent,
                                "created_at": _iso(p.created_at)} for p in pushes],
        "notifications": [{"kind": n.kind, "title": n.title, "body": n.body, "created_at": _iso(n.created_at),
                           "read_at": _iso(n.read_at)} for n in notes],
        "ai_conversations": [{"id": c.id, "title": c.title, "created_at": _iso(c.created_at),
                              "messages": msgs.get(c.id, [])} for c in convs],
        "work_orders": [{"id": w.id, "title": w.title, "state": getattr(w.state, "value", w.state),
                         "created_at": _iso(w.created_at),
                         "relation": "assignee" if w.assignee_id == uid else "creator"} for w in wos],
        "maintenance_absences": [{"date": str(a.date), "hours": a.hours, "note": a.note} for a in absences],
        "invitations_sent": [{"email": i.email, "created_at": _iso(i.created_at), "accepted_at": _iso(i.accepted_at)}
                             for i in invites],
    }


def _host(url: str) -> Optional[str]:
    from urllib.parse import urlparse
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


async def anonymize_user(db: AsyncSession, user, by_user_id: Optional[str], reason: str) -> None:
    """Irreversible pseudonymization (the caller audits and commits)."""
    from ...models import (AiConversation, AuthSession, MaintenanceAbsence, MfaRecoveryCode, Notification,
                           NotificationPreference, PasswordHistory, PasswordResetToken, PushSubscription,
                           UserInvitation)
    now = datetime.now(timezone.utc)
    uid, tid = user.id, user.tenant_id
    user.email = f"removido-{uid[:8]}-{hashlib.sha256(uid.encode()).hexdigest()[:6]}@anonimizado.invalid"
    user.name = pseudonym(uid)
    user.password_hash = "!"
    user.is_active = False
    user.on_call = False
    user.line_ids = []
    user.mfa_secret_enc = user.mfa_pending_enc = None
    user.mfa_enabled_at = None
    user.mfa_last_step = None
    user.tokens_valid_after = now
    user.anonymized_at = now
    await db.execute(update(AuthSession).where(AuthSession.user_id == uid, AuthSession.revoked_at.is_(None))
                     .values(revoked_at=now, revoked_reason="anonymized"))
    await db.execute(update(AuthSession).where(AuthSession.user_id == uid)
                     .values(ip_prefix=None, user_agent=None))
    for model in (MfaRecoveryCode, PasswordHistory, PasswordResetToken):
        await db.execute(delete(model).where(model.user_id == uid))
    for model in (Notification, NotificationPreference, PushSubscription, AiConversation):
        await db.execute(delete(model).where(model.user_id == uid, model.tenant_id == tid))
    await db.execute(update(MaintenanceAbsence).where(MaintenanceAbsence.user_id == uid,
                                                      MaintenanceAbsence.tenant_id == tid).values(note=None))
    await db.execute(update(UserInvitation).where(UserInvitation.user_id == uid, UserInvitation.tenant_id == tid)
                     .values(email=user.email, name=None))
    await db.flush()
    log.info("privacy.user_anonymized", tenant_id=tid, reason=reason)


async def purge_expired(db: AsyncSession, now: Optional[datetime] = None) -> dict:
    """Retention: leads after LEAD_RETENTION_DAYS, audit events after AUDIT_RETENTION_DAYS, expired invitations /
    reset tokens / sessions / throttle rows."""
    from ...models import AuditEvent, Lead, PasswordResetToken, UserInvitation
    from . import sessions, throttle
    now = now or datetime.now(timezone.utc)
    out = {}
    if settings.lead_retention_days > 0:
        out["leads"] = (await db.execute(delete(Lead).where(
            Lead.created_at < now - timedelta(days=settings.lead_retention_days)))).rowcount or 0
    if settings.audit_retention_days > 0:
        out["audit_events"] = (await db.execute(delete(AuditEvent).where(
            AuditEvent.created_at < now - timedelta(days=settings.audit_retention_days)))).rowcount or 0
    out["reset_tokens"] = (await db.execute(delete(PasswordResetToken).where(
        PasswordResetToken.expires_at < now - timedelta(days=1)))).rowcount or 0
    out["invitations"] = (await db.execute(delete(UserInvitation).where(
        UserInvitation.accepted_at.is_(None), UserInvitation.expires_at < now - timedelta(days=30)))).rowcount or 0
    out["sessions"] = await sessions.purge(db, now)
    out["throttle"] = await throttle.purge(db, now)
    return out


async def run_privacy_janitor() -> None:
    """Background loop (main.py lifespan): retention purge every PRIVACY_JANITOR_INTERVAL_S."""
    from ...db import AsyncSessionLocal
    while True:
        try:
            async with AsyncSessionLocal() as db:
                out = await purge_expired(db)
                await db.commit()
            if any(out.values()):
                log.info("privacy.purged", **out)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — retry next tick
            log.warning("privacy.janitor_failed", error=str(e)[:200])
        await asyncio.sleep(settings.privacy_janitor_interval_s)
