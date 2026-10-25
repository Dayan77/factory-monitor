"""Demo sessions (docs/sprint6.md §5): "Ver demonstração" → a 2 h JWT with role demo_viewer in the demo tenant.

* per-IP rate limit (settings.demo_rate_limit_per_hour, default 10/h), counted in demo_sessions by a keyed hash of
  the IP (never the IP) under a per-IP advisory lock, so it holds across workers and restarts;
* the JWT: sub = the shared, password-less demo viewer account (roles manager + demo_viewer), `role:
  demo_viewer`, `demo_session: <id>` (AI quota), exp = 2 h;
* `demo_status`: the banner text — "planta simulada temporariamente parada" when no reading arrived from the
  virtual plant for settings.demo_plant_stopped_after_s (5 min).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import jwt
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import DemoSession, Gateway, Machine, Plant
from .bootstrap import demo_tenant, viewer_user
from .guard import DEMO_ROLE

BANNER = "Demonstração — dados de uma planta simulada (planta virtual FactoryConnect)"
BANNER_STOPPED = ("Demonstração — planta simulada temporariamente parada (planta virtual FactoryConnect); os "
                  "dados voltam assim que ela reiniciar")


class DemoUnavailable(Exception):
    pass


class RateLimited(Exception):
    pass


async def demo_status(db: AsyncSession, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    t = await demo_tenant(db)
    if t is None:
        return {"available": False, "plant_running": False, "last_data_at": None, "banner": None,
                "message": "Demonstração indisponível no momento."}
    last = (await db.execute(select(func.max(Machine.last_seen_at)).where(Machine.tenant_id == t.id))).scalar_one()
    gw_last = (await db.execute(select(func.max(Gateway.last_seen_at)).where(Gateway.tenant_id == t.id))).scalar_one()
    running = last is not None and (now - last).total_seconds() <= settings.demo_plant_stopped_after_s
    plant = (await db.execute(select(Plant).where(Plant.tenant_id == t.id).order_by(Plant.created_at)
                              .limit(1))).scalars().first()
    return {"available": True, "plant_running": running, "last_data_at": last, "gateway_last_seen_at": gw_last,
            "stopped_after_s": settings.demo_plant_stopped_after_s,
            "banner": BANNER if running else BANNER_STOPPED, "plant_id": plant.id if plant else None,
            "plant_name": plant.name if plant else None, "timezone": plant.timezone if plant else None}


async def create_session(db: AsyncSession, ip_hash: str, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    t = await demo_tenant(db)
    viewer = await viewer_user(db, t.id) if t is not None else None
    if t is None or viewer is None or not viewer.is_active:
        raise DemoUnavailable()
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": "demo:" + ip_hash})
    recent = (await db.execute(select(func.count()).select_from(DemoSession).where(
        DemoSession.ip_hash == ip_hash, DemoSession.created_at >= now - timedelta(hours=1)))).scalar_one()
    if recent >= settings.demo_rate_limit_per_hour:
        raise RateLimited()
    exp = now + timedelta(hours=settings.demo_session_hours)
    s = DemoSession(ip_hash=ip_hash, expires_at=exp, created_at=now)
    db.add(s)
    await db.flush()
    token = jwt.encode({"sub": viewer.id, "tenant_id": t.id, "role": DEMO_ROLE, "demo_session": s.id,
                        "exp": exp}, settings.app_secret_key, algorithm=settings.jwt_algorithm)
    await db.commit()
    status = await demo_status(db, now)
    return {"access_token": token, "token_type": "bearer", "role": DEMO_ROLE, "tenant_id": t.id,
            "expires_at": exp, "expires_in": int((exp - now).total_seconds()), "redirect_to": "/dashboard",
            "ai_questions_quota": settings.demo_ai_questions_per_session, "banner": status["banner"],
            "plant_id": status["plant_id"], "read_only_message": "Modo demonstração: alterações desativadas"}


async def session_info(db: AsyncSession, session_id: str) -> Optional[dict]:
    s = await db.get(DemoSession, session_id)
    if s is None:
        return None
    return {"id": s.id, "expires_at": s.expires_at, "ai_questions_used": s.ai_questions_used,
            "ai_questions_quota": settings.demo_ai_questions_per_session,
            "ai_questions_left": max(0, settings.demo_ai_questions_per_session - s.ai_questions_used)}
