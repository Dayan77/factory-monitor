"""
Public (unauthenticated) endpoints for the marketing site.

POST /api/public/leads → 202. Anti-spam: honeypot field `website` (must be empty; a filled one gets
the same 202 but nothing is stored or sent) and a per-IP rate limit
(settings.leads_rate_limit_per_hour, default 5/h, counted in the leads table so it holds across
workers/restarts → 429). The IP is stored only as a keyed hash. The notification e-mail to
LEADS_TO_EMAIL is sent after the response (SendGrid if SENDGRID_API_KEY, else SMTP; with no mail
configuration it is logged and the lead is still stored).
"""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Annotated

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db import get_db
from ...models import Lead
from ...services.mailer import notify_lead
from ..schemas_assets import LeadCreate

log = structlog.get_logger()
router = APIRouter()

DB = Annotated[AsyncSession, Depends(get_db)]
ACCEPTED = {"status": "accepted"}


def client_ip(request: Request) -> str:
    # ProxyHeadersMiddleware (src/main.py, trusted proxies = FORWARDED_ALLOW_IPS) already rewrote
    # request.client to the right-most untrusted X-Forwarded-For entry: the real client.
    return request.client.host if request.client else "unknown"


def ip_hash(ip: str) -> str:
    return hmac.new(settings.app_secret_key.encode(), ip.encode(), hashlib.sha256).hexdigest()


@router.post("/leads", status_code=status.HTTP_202_ACCEPTED)
async def create_lead(body: LeadCreate, request: Request, background: BackgroundTasks, db: DB):
    if body.website and body.website.strip():
        log.info("leads.honeypot")
        return ACCEPTED
    h = ip_hash(client_ip(request))
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    # Serialise the count-then-insert per IP (transaction-scoped advisory lock): concurrent posts
    # from one IP used to all see the same count and all pass the limit (Sprint 2 review).
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": "lead:" + h})
    recent = (await db.execute(
        select(func.count()).select_from(Lead).where(Lead.ip_hash == h, Lead.created_at >= since)
    )).scalar_one()
    if recent >= settings.leads_rate_limit_per_hour:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Muitas mensagens deste endereço. Tente novamente mais tarde.")
    lead = Lead(
        name=body.name.strip(), email=str(body.email), company=body.company, phone=body.phone,
        message=body.message, consent=True, consent_at=datetime.now(timezone.utc), source=body.source,
        ip_hash=h, user_agent=(request.headers.get("user-agent") or "")[:500] or None,
    )
    db.add(lead)
    await db.commit()
    log.info("leads.stored", lead_id=lead.id)
    background.add_task(notify_lead, lead.id)
    return ACCEPTED


# ── public demo (Sprint 6 — docs/sprint6.md §5) ──────────────────────────────

@router.post("/demo-session", status_code=status.HTTP_201_CREATED)
async def demo_session(request: Request, db: DB):
    """"Ver demonstração": no sign-up; per-IP rate limit (DEMO_RATE_LIMIT_PER_HOUR, default 10/h) → a 2 h JWT with
    role demo_viewer in the read-only demo tenant (then the site redirects to /dashboard)."""
    from ...services.demo import session as demo
    try:
        out = await demo.create_session(db, ip_hash(client_ip(request)))
    except demo.RateLimited:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Muitas demonstrações abertas deste endereço. Tente novamente mais tarde.")
    except demo.DemoUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Demonstração indisponível no momento.")
    log.info("demo.session_opened")            # LGPD: only the event, no IP / identifier
    return out


@router.get("/demo-status")
async def demo_status(db: DB):
    """Banner of the demo: "planta simulada temporariamente parada" when the virtual plant sent nothing for 5 min."""
    from ...services.demo.session import demo_status as status_of
    out = await status_of(db)
    out.pop("gateway_last_seen_at", None)
    return out
