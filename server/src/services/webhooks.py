"""
Outbound webhooks for CMMS/ERP integration.

Delivery: `POST <url>` with JSON body
    {"id": "<delivery id>", "event": "work_order.completed", "occurred_at": "...Z", "tenant_id": "...",
     "data": {...}}
and headers
    X-FactoryConnect-Event: <event>
    X-FactoryConnect-Delivery: <delivery id>
    X-FactoryConnect-Timestamp: <unix seconds>
    X-FactoryConnect-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>
The receiver recomputes the HMAC over the timestamp, a dot and the raw body with its secret and
compares in constant time (and rejects old timestamps against replay).

Retries: non-2xx or network error → retried with backoff (30 s, 2 min, 10 min, 1 h, 6 h); after
MAX_ATTEMPTS the delivery is `failed`. Events: work_order.created, work_order.state_changed,
work_order.<state> (e.g. work_order.completed), work_order.updated, failure.created, failure.updated,
plan.due (state due/overdue). Subscriptions accept exact names, `prefix.*` or `*`.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Webhook, WebhookDelivery
from . import clock

log = structlog.get_logger()

EVENTS = ("work_order.created", "work_order.updated", "work_order.state_changed", "work_order.draft",
          "work_order.open", "work_order.approved", "work_order.scheduled", "work_order.in_progress",
          "work_order.on_hold", "work_order.completed", "work_order.closed", "work_order.cancelled",
          "failure.created", "failure.updated", "plan.due")
BACKOFF = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10), timedelta(hours=1), timedelta(hours=6))
MAX_ATTEMPTS = len(BACKOFF) + 1


def valid_subscription(pattern: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return any(e.startswith(pattern[:-1]) for e in EVENTS)
    return pattern in EVENTS


def matches(patterns: list[str], event: str) -> bool:
    for p in patterns or []:
        if p == "*" or p == event or (p.endswith(".*") and event.startswith(p[:-1])):
            return True
    return False


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def verify(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


def _ip_blocked(ip: ipaddress._BaseAddress) -> bool:
    """Only globally routable unicast addresses are allowed (blocks private, loopback, link-local, CGNAT
    100.64/10, reserved, multicast, unspecified and IPv4-mapped private IPv6)."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


async def _resolve(host: str, port: int) -> list[str]:
    """Non-blocking DNS lookup (monkeypatched by the tests)."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(ai[4][0] for ai in infos))


async def resolve_url(url: str) -> tuple[Optional[str], list[str]]:
    """(reason, addresses). reason None → acceptable; `addresses` are the validated IPs to connect to
    (empty when private hosts are allowed and the name is not resolved here)."""
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return "URL deve ser http(s) com host.", []
    if p.username or p.password:
        return "URL de webhook não pode conter credenciais.", []
    host = p.hostname
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        addrs = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addrs = await _resolve(host, port)
        except OSError:
            addrs = []
    if settings.webhook_allow_private_hosts:
        return None, addrs
    if not addrs:
        return "Host do webhook não resolvido (DNS).", []
    for a in addrs:
        if _ip_blocked(ipaddress.ip_address(a.split("%")[0])):
            return "Endereço privado/loopback não permitido para webhooks.", []
    return None, addrs


async def check_url(url: str, allow_unresolved: bool = True) -> Optional[str]:
    """None if acceptable, else the reason. Refuses non-http(s) and (unless allowed) non-public hosts.
    When saving, a name that does not resolve right now is accepted; delivery resolves and pins again."""
    reason, _ = await resolve_url(url)
    if reason == "Host do webhook não resolvido (DNS)." and allow_unresolved:
        return None
    return reason


def pinned_request(url: str, ip: Optional[str]) -> tuple[str, dict, dict]:
    """(url, extra headers, request extensions) to connect to the validated `ip` while keeping the original
    Host header and TLS SNI/certificate check on the original name (no second DNS lookup → no rebinding)."""
    if not ip:
        return url, {}, {}
    u = httpx.URL(url)
    host_header = u.host if u.port is None else f"{u.host}:{u.port}"
    ext = {"sni_hostname": u.host} if u.scheme == "https" else {}
    return str(u.copy_with(host=ip.split("%")[0])), {"Host": host_header}, ext


async def enqueue(db: AsyncSession, tenant_id: str, event: str, data: dict) -> int:
    """Queues one delivery per active webhook of the tenant subscribed to `event` (same transaction)."""
    hooks = (await db.execute(select(Webhook).where(Webhook.tenant_id == tenant_id, Webhook.is_active.is_(True)))).scalars().all()
    n = 0
    now = clock.now()
    for h in hooks:
        if not matches(h.events, event):
            continue
        d = WebhookDelivery(tenant_id=tenant_id, webhook_id=h.id, event=event, status="pending", attempts=0,
                            next_attempt_at=datetime.now(timezone.utc),
                            payload={"event": event, "occurred_at": clock.iso(now),
                                     "tenant_id": tenant_id, "data": json.loads(json.dumps(data, default=str))})
        db.add(d)
        n += 1
    return n


async def attempt(db: AsyncSession, d: WebhookDelivery, client: Optional[httpx.AsyncClient] = None) -> bool:
    hook = await db.get(Webhook, d.webhook_id)
    if hook is None or hook.tenant_id != d.tenant_id:
        d.status = "failed"
        d.last_error = "webhook removido"
        return False
    body = json.dumps({"id": d.id, **d.payload}, ensure_ascii=False, separators=(",", ":")).encode()
    ts = str(int(datetime.now(timezone.utc).timestamp()))
    headers = {"Content-Type": "application/json", "User-Agent": "FactoryConnect-Webhooks/1",
               "X-FactoryConnect-Event": d.event, "X-FactoryConnect-Delivery": d.id,
               "X-FactoryConnect-Timestamp": ts, "X-FactoryConnect-Signature": sign(hook.secret, ts, body)}
    d.attempts += 1
    ok = False
    try:
        reason, addrs = await resolve_url(hook.url)
        if reason:
            raise ValueError(reason)
        url, extra, ext = pinned_request(hook.url, addrs[0] if addrs else None)
        req_headers = {**headers, **extra}
        if client is not None:
            r = await client.post(url, content=body, headers=req_headers, timeout=settings.webhook_timeout_s,
                                  extensions=ext, follow_redirects=False)
        else:
            async with httpx.AsyncClient(follow_redirects=False) as c:
                r = await c.post(url, content=body, headers=req_headers, timeout=settings.webhook_timeout_s,
                                 extensions=ext)
        d.last_status_code = r.status_code
        ok = 200 <= r.status_code < 300
        d.last_error = None if ok else f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        d.last_error = f"{type(e).__name__}: {e}"[:500]
    now = datetime.now(timezone.utc)
    if ok:
        d.status, d.delivered_at, d.next_attempt_at = "delivered", now, None
    elif d.attempts >= MAX_ATTEMPTS:
        d.status, d.next_attempt_at = "failed", None
    else:
        d.next_attempt_at = now + BACKOFF[d.attempts - 1]
    return ok


async def deliver_due(db: AsyncSession, client: Optional[httpx.AsyncClient] = None, limit: int = 50) -> int:
    rows = (await db.execute(select(WebhookDelivery).where(
        WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at <= datetime.now(timezone.utc))
        .order_by(WebhookDelivery.next_attempt_at).limit(limit).with_for_update(skip_locked=True))).scalars().all()
    n = 0
    for d in rows:
        n += 1 if await attempt(db, d, client) else 0
        await db.commit()
    return n
