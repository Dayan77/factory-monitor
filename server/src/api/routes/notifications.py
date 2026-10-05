"""
Notifications of the logged user (maintenance_workflows.md §5) and Web Push subscriptions.

  GET  /api/notifications/                 unread first, then newest (own only, tenant-scoped)
  POST /api/notifications/{id}/read
  POST /api/notifications/read-all
  GET  /api/notifications/preferences      {kinds: [{kind, label, defaults, channels, mutable}], digest_enabled}
  PUT  /api/notifications/preferences      {channels: {kind: {in_app, email, web_push}}, digest_enabled}
  GET  /api/push/vapid-public-key          {public_key | null, enabled}
  POST /api/push/subscribe                 {endpoint, keys: {p256dh, auth}}
  POST /api/push/unsubscribe               {endpoint}
Real time: WebSocket /ws/sensors/{tenant_id} sends {type: 'notification', notification: {...}} to the user.
"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user
from ...db import get_db
from ...models import Notification, NotificationPreference, PushSubscription, User
from ...notifications import service as notif
from ...notifications.webpush import vapid_from_settings
from ...services.validation import is_uuid
from ..schemas_maintenance import PreferencesIn, PushSubscribeIn

router = APIRouter()
push_router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]


def _human(user: User) -> None:
    if getattr(user, "api_token_id", None):
        raise HTTPException(status_code=403, detail="Notificações são do usuário (não de tokens de API).")


@router.get("/")
async def list_notifications(user: CurrentUser, db: DB, unread: Optional[bool] = None,
                             limit: int = Query(50, ge=1, le=500)):
    _human(user)
    q = select(Notification).where(Notification.tenant_id == user.tenant_id, Notification.user_id == user.id)
    if unread is not None:
        q = q.where(Notification.read_at.is_(None) if unread else Notification.read_at.is_not(None))
    q = q.order_by(case((Notification.read_at.is_(None), 0), else_=1), Notification.updated_at.desc()).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    unread_count = (await db.execute(select(func.count()).select_from(Notification).where(
        Notification.tenant_id == user.tenant_id, Notification.user_id == user.id,
        Notification.read_at.is_(None)))).scalar_one()
    return {"unread_count": unread_count, "items": [notif.notification_out(n) for n in rows]}


@router.post("/read-all")
async def read_all(user: CurrentUser, db: DB):
    _human(user)
    return {"updated": await notif.mark_read(db, user)}


@router.post("/{notification_id}/read")
async def read_one(notification_id: str, user: CurrentUser, db: DB):
    _human(user)
    if not is_uuid(notification_id):
        raise HTTPException(status_code=404, detail="Notificação não encontrada.")
    n = (await db.execute(select(Notification).where(Notification.id == notification_id,
                                                     Notification.tenant_id == user.tenant_id,
                                                     Notification.user_id == user.id))).scalar_one_or_none()
    if n is None:
        raise HTTPException(status_code=404, detail="Notificação não encontrada.")
    await notif.mark_read(db, user, notification_id)
    await db.refresh(n)
    return notif.notification_out(n)


async def _prefs_out(db: AsyncSession, user: User) -> dict:
    p = await db.get(NotificationPreference, user.id)
    kinds = []
    for k, spec in notif.KINDS.items():
        kinds.append({"kind": k, "label": spec.label, "defaults": notif.default_channels(k),
                      "channels": notif.effective_channels(k, p), "mutable": spec.mutable, "digest": spec.digest})
    return {"kinds": kinds, "digest_enabled": p.digest_enabled if p else True}


@router.get("/preferences")
async def get_preferences(user: CurrentUser, db: DB):
    _human(user)
    return await _prefs_out(db, user)


@router.put("/preferences")
async def put_preferences(body: PreferencesIn, user: CurrentUser, db: DB):
    _human(user)
    p = await db.get(NotificationPreference, user.id)
    if p is None:
        p = NotificationPreference(user_id=user.id, tenant_id=user.tenant_id, channels={})
        db.add(p)
    ch = dict(p.channels or {})
    for kind, chans in body.channels.items():
        if kind not in notif.KINDS:
            raise HTTPException(status_code=422, detail=f"Tipo de notificação desconhecido: {kind}")
        if not notif.KINDS[kind].mutable and not all(chans.get(c, True) for c in ("in_app", "web_push")):
            raise HTTPException(status_code=422, detail="O chamado emergencial do plantão não pode ser silenciado.")
        ch[kind] = {c: bool(v) for c, v in chans.items() if c in notif.CHANNELS}
    p.channels = ch
    if body.digest_enabled is not None:
        p.digest_enabled = body.digest_enabled
    await db.commit()
    return await _prefs_out(db, user)


@push_router.get("/vapid-public-key")
async def vapid_public_key(user: CurrentUser):
    v = vapid_from_settings()
    return {"public_key": v.public_key_b64 if v else None, "enabled": v is not None}


@push_router.post("/subscribe", status_code=201)
async def subscribe(body: PushSubscribeIn, user: CurrentUser, db: DB):
    _human(user)
    s = (await db.execute(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))).scalar_one_or_none()
    if s is None:
        s = PushSubscription(tenant_id=user.tenant_id, user_id=user.id, endpoint=body.endpoint,
                             p256dh=body.keys["p256dh"], auth=body.keys["auth"])
        db.add(s)
    else:
        # same browser re-subscribing (possibly another user logged in on the team device)
        s.tenant_id, s.user_id = user.tenant_id, user.id
        s.p256dh, s.auth, s.last_error = body.keys["p256dh"], body.keys["auth"], None
    await db.commit()
    return {"id": s.id, "endpoint": s.endpoint}


class _Unsub(BaseModel):
    endpoint: str


@push_router.post("/unsubscribe")
async def unsubscribe(body: _Unsub, user: CurrentUser, db: DB):
    s = (await db.execute(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint,
                                                         PushSubscription.user_id == user.id,
                                                         PushSubscription.tenant_id == user.tenant_id))).scalar_one_or_none()
    if s is not None:
        await db.delete(s)
        await db.commit()
    return {"removed": s is not None}
