"""
Maintenance notifications (maintenance_workflows.md §5).

* `notify()` writes the rows inside the caller's transaction and queues delivery; after the commit
  the caller runs `deliver_pending(db)`: WebSocket `{type: 'notification', notification: {...}}` to
  the user's open sockets, Web Push (VAPID) and immediate e-mail, according to the kind's default
  channels and the user's preferences.
* Repeated notifications with the same `group_key` (still unread, last 24 h) are grouped: one row,
  `count` incremented, `items` appended — never 50 pushes for 50 plans at 80 %; a grouped push is sent
  at most once per PUSH_COALESCE.
* Digest kinds are e-mailed once a day at 07:00 plant local (`send_digests`).
* The on-call emergency call (`emergency_call`) cannot be muted.
* Recipients always belong to the tenant; with a `line_id`, only users whose area (`line_ids`, empty =
  all lines) contains that line.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

import structlog
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Notification, NotificationPreference, PushSubscription, User
from ..services import clock

log = structlog.get_logger()

CHANNELS = ("in_app", "email", "web_push")
GROUP_WINDOW = timedelta(hours=24)
PUSH_COALESCE = timedelta(minutes=5)
MAX_ITEMS = 50


@dataclass(frozen=True)
class KindSpec:
    label: str
    in_app: bool = True
    email: bool = False
    web_push: bool = False
    digest: bool = False          # e-mail goes in the 07:00 summary instead of immediately
    priority: str = "normal"
    mutable: bool = True


KINDS: dict[str, KindSpec] = {
    "plan_due_soon":      KindSpec("Plano ≥ 80 % (rascunho criado)", email=True, digest=True),
    "plan_due":           KindSpec("Plano vencido (due)", email=True, priority="high"),
    "plan_overdue":       KindSpec("Plano além da tolerância (overdue)", email=True, priority="high"),
    "component_life":     KindSpec("Componente ≥ 80 % / 100 % da vida", email=True, digest=True),
    "wo_assigned":        KindSpec("OS atribuída", web_push=True),
    "wo_rescheduled":     KindSpec("OS reprogramada", web_push=True),
    "emergency_call":     KindSpec("Chamado emergencial", web_push=True, priority="emergency", mutable=False),
    "failure_escalation": KindSpec("Falha aberta há mais de 2 × MTTR", web_push=True, priority="high"),
    "failure_new":        KindSpec("Falha nova registrada", email=True, digest=True),
    "wo_overdue":         KindSpec("OS vencida", email=True, digest=True),
    "wo_on_hold":         KindSpec("OS em espera"),
    "window_request":     KindSpec("Pedido de janela de parada", web_push=True),
    "wo_awaiting_close":  KindSpec("OS concluída aguardando fechamento"),
}

_PENDING_KEY = "fm_pending_notifications"


def default_channels(kind: str) -> dict[str, bool]:
    k = KINDS.get(kind, KindSpec(kind))
    return {"in_app": k.in_app, "email": k.email, "web_push": k.web_push}


def effective_channels(kind: str, prefs: Optional[NotificationPreference]) -> dict[str, bool]:
    ch = default_channels(kind)
    spec = KINDS.get(kind)
    if spec is not None and not spec.mutable:
        return ch                                   # emergency on-call: cannot be muted
    if prefs is not None:
        for c, v in ((prefs.channels or {}).get(kind) or {}).items():
            if c in ch and isinstance(v, bool):
                ch[c] = v
    return ch


def in_area(user: User, line_id: Optional[str]) -> bool:
    return line_id is None or not user.line_ids or str(line_id) in {str(x) for x in user.line_ids}


async def users_with_roles(db: AsyncSession, tenant_id: str, roles: Iterable[str], line_id: Optional[str] = None,
                           on_call: Optional[bool] = None) -> list[User]:
    roles = set(roles)
    users = (await db.execute(select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True)))).scalars().all()
    out = []
    for u in users:
        if not (u.role_set & roles):
            continue
        if on_call is not None and bool(u.on_call) != on_call:
            continue
        if not in_area(u, line_id):
            continue
        out.append(u)
    return out


def notification_out(n: Notification) -> dict:
    return {"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link, "priority": n.priority,
            "data": n.data or {}, "count": n.count, "group_key": n.group_key, "line_id": n.line_id,
            "read": n.read_at is not None, "read_at": clock.iso(n.read_at),
            "created_at": clock.iso(n.created_at), "updated_at": clock.iso(n.updated_at)}


async def notify(db: AsyncSession, tenant_id: str, users: Iterable[User], kind: str, title: str,
                 body: Optional[str] = None, link: Optional[str] = None, data: Optional[dict] = None,
                 group_key: Optional[str] = None, line_id: Optional[str] = None,
                 grouped_title: Optional[str] = None) -> list[Notification]:
    """Creates (or groups) one notification per recipient. Delivery happens in deliver_pending()."""
    spec = KINDS.get(kind, KindSpec(kind))
    now = clock.now()
    out: list[Notification] = []
    seen: set[str] = set()
    for u in users:
        if u.id is None or u.id in seen or str(u.tenant_id) != str(tenant_id) or not in_area(u, line_id):
            continue
        seen.add(u.id)
        prefs = await db.get(NotificationPreference, u.id)
        ch = effective_channels(kind, prefs)
        if not any(ch.values()):
            continue
        item = {"title": title, "at": clock.iso(now), **(data or {})}
        existing = None
        if group_key:
            existing = (await db.execute(
                select(Notification).where(Notification.tenant_id == tenant_id, Notification.user_id == u.id,
                                           Notification.group_key == group_key, Notification.read_at.is_(None),
                                           Notification.created_at >= now - GROUP_WINDOW)
                .order_by(Notification.created_at.desc()).limit(1).with_for_update())).scalar_one_or_none()
        if existing is not None:
            existing.count += 1
            d = dict(existing.data or {})
            items = list(d.get("items") or [])
            items.append(item)
            d["items"] = items[-MAX_ITEMS:]
            existing.data = d
            existing.title = (grouped_title or "{count} × " + title).format(count=existing.count)
            existing.body = body
            existing.updated_at = now
            n = existing
        else:
            n = Notification(tenant_id=tenant_id, user_id=u.id, kind=kind, title=title, body=body, link=link,
                             priority=spec.priority, data={**(data or {}), "items": [item]} if group_key else (data or {}),
                             group_key=group_key, line_id=line_id, digest=spec.digest and ch.get("email", False),
                             created_at=now, updated_at=now)
            db.add(n)
        out.append(n)
        db.info.setdefault(_PENDING_KEY, []).append((n, ch))
    await db.flush()
    return out


async def deliver_pending(db: AsyncSession, push_client=None) -> int:
    """After commit: WebSocket + Web Push + immediate e-mail of what notify() queued."""
    pending = db.info.pop(_PENDING_KEY, [])
    from ..websocket.manager import manager
    from .webpush import send, vapid_from_settings
    vapid = vapid_from_settings()
    done = 0
    for n, ch in pending:
        try:
            if ch.get("in_app"):
                await manager.send_to_user(str(n.tenant_id), str(n.user_id),
                                           {"type": "notification", "notification": notification_out(n)})
            spec = KINDS.get(n.kind, KindSpec(n.kind))
            now = clock.now()
            if ch.get("web_push") and vapid is not None and (n.pushed_at is None or now - n.pushed_at >= PUSH_COALESCE):
                subs = (await db.execute(select(PushSubscription).where(
                    PushSubscription.tenant_id == n.tenant_id, PushSubscription.user_id == n.user_id))).scalars().all()
                for s in subs:
                    try:
                        code = await send(s.endpoint, s.p256dh, s.auth,
                                          {"title": n.title, "body": n.body, "link": n.link, "kind": n.kind,
                                           "priority": n.priority, "id": n.id, "count": n.count},
                                          vapid, urgency="high" if n.priority in ("high", "emergency") else "normal",
                                          client=push_client)
                        if code in (404, 410):
                            await db.delete(s)
                        elif code >= 400:
                            s.last_error = f"HTTP {code}"
                    except Exception as e:  # noqa: BLE001
                        s.last_error = f"{type(e).__name__}: {e}"[:500]
                n.pushed_at = now
            if ch.get("email") and not spec.digest and n.emailed_at is None:
                user = await db.get(User, n.user_id)
                if user is not None:
                    await _email(user.email, f"[FactoryConnect] {n.title}", _email_body([n]))
                n.emailed_at = now
            done += 1
        except Exception as e:  # noqa: BLE001 — delivery never breaks the request
            log.warning("notifications.deliver_failed", notification_id=str(n.id), error=f"{type(e).__name__}: {e}"[:200])
    if pending:
        await db.commit()
    return done


def _email_body(ns: list[Notification]) -> str:
    from ..config import settings
    lines = []
    for n in ns:
        lines.append(f"• {n.title}" + (f" ({n.count}×)" if n.count > 1 else ""))
        if n.body:
            lines.append(f"  {n.body}")
        if n.link:
            lines.append(f"  {settings.app_public_url.rstrip('/')}{n.link}")
    return "\n".join(lines) + "\n"


async def _email(to: str, subject: str, body: str) -> bool:
    from ..services.mailer import MailNotConfigured, send_email
    try:
        await send_email(to, subject, body)
        return True
    except MailNotConfigured:
        log.info("notifications.email_not_configured")
        return False
    except Exception as e:  # noqa: BLE001
        log.warning("notifications.email_failed", error=type(e).__name__)
        return False


async def send_digests(db: AsyncSession, tenant_id: str, local_today: date, local_hour: int, digest_hour: int = 7
                       ) -> int:
    """Daily summary e-mail (07:00 plant local) of the digest kinds not yet summarized."""
    if local_hour < digest_hour:
        return 0
    sent = 0
    users = (await db.execute(select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True)))).scalars().all()
    for u in users:
        prefs = await db.get(NotificationPreference, u.id)
        if prefs is None:
            prefs = NotificationPreference(user_id=u.id, tenant_id=tenant_id, channels={})
            db.add(prefs)
        if prefs.last_digest_on is not None and prefs.last_digest_on >= local_today:
            continue
        prefs.last_digest_on = local_today
        ns = (await db.execute(select(Notification).where(
            Notification.tenant_id == tenant_id, Notification.user_id == u.id, Notification.digest.is_(True),
            Notification.digested_at.is_(None)).order_by(Notification.created_at))).scalars().all()
        if not ns:
            continue
        if prefs.digest_enabled:
            await _email(u.email, f"[FactoryConnect] Resumo da manutenção — {local_today:%d/%m/%Y}", _email_body(list(ns)))
            sent += 1
        for n in ns:
            n.digested_at = clock.now()
    await db.commit()
    return sent


async def mark_read(db: AsyncSession, user: User, notification_id: Optional[str] = None) -> int:
    q = update(Notification).where(Notification.tenant_id == user.tenant_id, Notification.user_id == user.id,
                                   Notification.read_at.is_(None))
    if notification_id is not None:
        q = q.where(Notification.id == notification_id)
    res = await db.execute(q.values(read_at=clock.now()))
    await db.commit()
    return res.rowcount or 0
