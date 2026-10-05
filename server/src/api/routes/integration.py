"""
Integration API for CMMS/ERP (tenant admin only).

API tokens (`/api/api-tokens/`): the plaintext token (`fct_…`) is returned once at creation; only its
SHA-256 is stored. Use it as `Authorization: Bearer fct_…` on any endpoint; it acts with the
maintenance-planner permissions of its tenant. Scope `read` = GET only; `write` = also mutations.

Webhooks (`/api/webhooks/`): see services/webhooks.py for the payload, the signature header
`X-FactoryConnect-Signature: sha256=HMAC_SHA256(secret, "<X-FactoryConnect-Timestamp>.<raw body>")`
and the retry policy. The secret is write-only (never returned).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, hash_api_token, require_roles
from ...db import get_db
from ...models import ApiToken, User, UserRole, Webhook, WebhookDelivery
from ...services import clock, webhooks
from ...services.validation import is_uuid
from ..schemas_maintenance import ApiTokenIn, WebhookIn, WebhookPatch

tokens_router = APIRouter()
webhooks_router = APIRouter()

Admin = Annotated[User, Depends(require_roles(UserRole.admin))]
DB = Annotated[AsyncSession, Depends(get_db)]


def _no_token(user: User) -> None:
    if getattr(user, "api_token_id", None):
        raise HTTPException(status_code=403, detail="Tokens de API não gerenciam integrações.")


def _token_out(t: ApiToken) -> dict:
    iso = clock.iso
    return {"id": t.id, "name": t.name, "prefix": t.prefix, "scopes": list(t.scopes or []), "created_by": t.created_by,
            "created_at": iso(t.created_at), "last_used_at": iso(t.last_used_at), "expires_at": iso(t.expires_at),
            "revoked_at": iso(t.revoked_at), "active": t.revoked_at is None}


@tokens_router.get("/")
async def list_tokens(user: Admin, db: DB):
    rows = (await db.execute(select(ApiToken).where(ApiToken.tenant_id == user.tenant_id)
                             .order_by(ApiToken.created_at.desc()))).scalars().all()
    return [_token_out(t) for t in rows]


@tokens_router.post("/", status_code=201)
async def create_token(body: ApiTokenIn, user: Admin, db: DB):
    """Integration token for the tenant. Returns `token` (plaintext, `fct_…`) only now — store it in the
    integration's secret store; only its SHA-256 is kept. Use `Authorization: Bearer fct_…` on the API: the token
    acts with the maintenance-planner permissions of this tenant; scope `read` = GET only, `write` = mutations too.
    Revoke with DELETE."""
    _no_token(user)
    token = "fct_" + secrets.token_urlsafe(32)
    t = ApiToken(tenant_id=user.tenant_id, name=body.name, prefix=token[:12], token_hash=hash_api_token(token),
                 scopes=sorted(set(body.scopes)), created_by=user.id,
                 expires_at=(body.expires_at if body.expires_at is None or body.expires_at.tzinfo
                             else body.expires_at.replace(tzinfo=timezone.utc)))
    db.add(t)
    await db.commit()
    return {**_token_out(t), "token": token}


@tokens_router.delete("/{token_id}", status_code=204)
async def revoke_token(token_id: str, user: Admin, db: DB):
    _no_token(user)
    t = (await db.execute(select(ApiToken).where(ApiToken.id == token_id, ApiToken.tenant_id == user.tenant_id))
         ).scalar_one_or_none() if is_uuid(token_id) else None
    if t is None:
        raise HTTPException(status_code=404, detail="Token não encontrado.")
    t.revoked_at = t.revoked_at or datetime.now(timezone.utc)
    await db.commit()


def _hook_out(h: Webhook) -> dict:
    return {"id": h.id, "url": h.url, "events": list(h.events or []), "description": h.description,
            "is_active": h.is_active, "has_secret": bool(h.secret),
            "created_at": clock.iso(h.created_at)}


def _validate(url: Optional[str], events: Optional[list[str]]) -> None:
    if url is not None:
        reason = webhooks.check_url(url)
        if reason:
            raise HTTPException(status_code=422, detail=reason)
    if events is not None:
        bad = [e for e in events if not webhooks.valid_subscription(e)]
        if bad:
            raise HTTPException(status_code=422, detail={"message": f"Eventos inválidos: {', '.join(bad)}",
                                                         "events": list(webhooks.EVENTS)})


async def _get_hook(db: AsyncSession, hook_id: str, tenant_id: str) -> Webhook:
    h = (await db.execute(select(Webhook).where(Webhook.id == hook_id, Webhook.tenant_id == tenant_id))
         ).scalar_one_or_none() if is_uuid(hook_id) else None
    if h is None:
        raise HTTPException(status_code=404, detail="Webhook não encontrado.")
    return h


@webhooks_router.get("/events")
async def list_events(user: Admin):
    return {"events": list(webhooks.EVENTS), "patterns": ["work_order.*", "failure.*", "plan.*", "*"]}


@webhooks_router.get("/")
async def list_hooks(user: Admin, db: DB):
    rows = (await db.execute(select(Webhook).where(Webhook.tenant_id == user.tenant_id)
                             .order_by(Webhook.created_at))).scalars().all()
    return [_hook_out(h) for h in rows]


@webhooks_router.post("/", status_code=201)
async def create_hook(body: WebhookIn, user: Admin, db: DB):
    """Outbound webhook (CMMS/ERP). Events: `work_order.created`, `work_order.updated`,
    `work_order.state_changed`, `work_order.<state>` (e.g. `work_order.completed`), `failure.created`,
    `failure.updated`, `plan.due`; patterns `work_order.*`, `failure.*`, `plan.*`, `*`.

    Each delivery is `POST <url>` with JSON `{"id", "event", "occurred_at", "tenant_id", "data"}` and headers
    `X-FactoryConnect-Event`, `X-FactoryConnect-Delivery`, `X-FactoryConnect-Timestamp` (unix s) and
    `X-FactoryConnect-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>`. Verify it with a
    constant-time compare and reject old timestamps. Non-2xx → retried after 30 s, 2 min, 10 min, 1 h, 6 h,
    then `failed` (see `/deliveries`). The secret is write-only. Private/loopback URLs are refused
    (WEBHOOK_ALLOW_PRIVATE_HOSTS=true to allow, dev only)."""
    _no_token(user)
    _validate(body.url, body.events)
    h = Webhook(tenant_id=user.tenant_id, url=body.url, secret=body.secret, events=sorted(set(body.events)),
                description=body.description, is_active=body.is_active, created_by=user.id)
    db.add(h)
    await db.commit()
    return _hook_out(h)


@webhooks_router.get("/{hook_id}")
async def get_hook(hook_id: str, user: Admin, db: DB):
    return _hook_out(await _get_hook(db, hook_id, user.tenant_id))


@webhooks_router.patch("/{hook_id}")
async def patch_hook(hook_id: str, body: WebhookPatch, user: Admin, db: DB):
    _no_token(user)
    h = await _get_hook(db, hook_id, user.tenant_id)
    _validate(body.url, body.events)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is None and k != "description":
            continue
        setattr(h, k, sorted(set(v)) if k == "events" else v)
    await db.commit()
    return _hook_out(h)


@webhooks_router.delete("/{hook_id}", status_code=204)
async def delete_hook(hook_id: str, user: Admin, db: DB):
    _no_token(user)
    h = await _get_hook(db, hook_id, user.tenant_id)
    await db.delete(h)
    await db.commit()


@webhooks_router.get("/{hook_id}/deliveries")
async def list_deliveries(hook_id: str, user: Admin, db: DB, limit: int = Query(50, ge=1, le=500)):
    h = await _get_hook(db, hook_id, user.tenant_id)
    rows = (await db.execute(select(WebhookDelivery).where(WebhookDelivery.webhook_id == h.id,
                                                           WebhookDelivery.tenant_id == user.tenant_id)
                             .order_by(WebhookDelivery.created_at.desc()).limit(limit))).scalars().all()
    iso = clock.iso
    return [{"id": d.id, "event": d.event, "status": d.status, "attempts": d.attempts,
             "last_status_code": d.last_status_code, "last_error": d.last_error,
             "next_attempt_at": iso(d.next_attempt_at), "delivered_at": iso(d.delivered_at),
             "created_at": iso(d.created_at)} for d in rows]
