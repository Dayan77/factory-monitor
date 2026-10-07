"""demo-bot (docs/sprint6.md §5): closes the scenario-F loop of the public demo.

When a stroke-time alert of a cylinder of the demo tenant has been open (or acknowledged) for
settings.demo_bot_replace_after_s, the system user `demo-bot` — like a maintenance team would — converts it into a
predictive WO, schedules and starts it, registers the cylinder replacement (new component from stock) and the
ISO 14224 failure record (degraded, continuous condition monitoring), completes the WO with
`finding = defect_confirmed` and closes it. Every step goes through the maintenance service (WO history with
demo-bot as the author) and is recorded in audit_events. When DEMO_VIRTUAL_PLANT_CONTROL_URL is set, the virtual
plant is told to swap the cylinder too (POST /replace), so its wear restarts — the next alert comes later again.
Only ever acts on the tenant flagged `is_demo`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import (Component, FailureRecord, Machine, Position, PredictiveAlert, Tag, WorkOrder,
                       WorkOrderReplacement)
from ...models.tag import SemanticRole
from .. import audit
from .bootstrap import bot_user, demo_tenant

log = structlog.get_logger()


async def run_once(db: AsyncSession, now: Optional[datetime] = None) -> list[dict]:
    """One pass. Returns what was done (one entry per replacement)."""
    from ..maintenance import service as ms
    from ..predictive import service as ps
    from ..predictive.defaults import COMPATIBLE_MODES

    now = now or datetime.now(timezone.utc)
    t = await demo_tenant(db)
    if t is None:
        return []
    bot = await bot_user(db, t.id)
    if bot is None:
        return []
    due = now - timedelta(seconds=settings.demo_bot_replace_after_s)
    alerts = (await db.execute(select(PredictiveAlert).where(
        PredictiveAlert.tenant_id == t.id, PredictiveAlert.feature == "stroke_time",
        PredictiveAlert.state.in_(("open", "acknowledged")), PredictiveAlert.opened_at <= due)
        .order_by(PredictiveAlert.opened_at).with_for_update(skip_locked=True))).scalars().all()
    done = []
    for a in alerts:
        pos = await db.get(Position, a.position_id)
        if pos is None or pos.tenant_id != t.id:
            continue
        if a.work_order_id:
            wo = await db.get(WorkOrder, a.work_order_id)
            if wo is not None and wo.state not in ("cancelled", "completed", "closed"):
                continue                  # a person is already handling it
        wo = await ps.convert_alert(db, a, bot, now, estimated_hours=1.0,
                                    note="demo-bot: troca roteirizada da demonstração")
        today = now.astimezone(await ps.plant_tz(db, t.id, a.machine_id)).date()
        await ms.transition(db, wo, "scheduled", bot, scheduled_for=today, at=now, check_permission=False,
                            note="demo-bot: programada")
        wo.assignee_id = bot.id
        await ms.transition(db, wo, "in_progress", bot, at=now, check_permission=False, note="demo-bot: iniciada")
        old = (await db.execute(select(Component).where(
            Component.tenant_id == t.id, Component.position_id == pos.id,
            Component.removed_at.is_(None)))).scalar_one_or_none()
        new = Component(tenant_id=t.id, component_type=pos.component_type,
                        manufacturer=old.manufacturer if old else None, model=old.model if old else None,
                        serial_number=f"DEMO-REP-{wo.number}-{now:%Y%m%d%H%M}",
                        nominal_life=dict(old.nominal_life) if old and old.nominal_life else {})
        db.add(new)
        await db.flush()
        db.add(WorkOrderReplacement(tenant_id=t.id, work_order_id=wo.id, position_id=pos.id,
                                    removed_component_id=old.id if old else None, installed_component_id=new.id,
                                    removal_reason="preventive_replacement", created_by=bot.id))
        mode = (COMPATIBLE_MODES.get("stroke_time") or ("PDE",))[0]
        fr = FailureRecord(tenant_id=t.id, machine_id=a.machine_id, position_id=pos.id, failed_at=a.opened_at,
                           restored_at=now, severity="degraded", failure_mode=mode,
                           detection_method="continuous_condition_monitoring", status="complete",
                           component_id=old.id if old else None, work_order_id=wo.id, detected_at=a.opened_at,
                           repair_started_at=now, repair_finished_at=now,
                           note="demo-bot: desgaste detectado pela preditiva; cilindro trocado")
        db.add(fr)
        await db.flush()
        await ms.transition(db, wo, "completed", bot, at=now, check_permission=False, finding="defect_confirmed",
                            labor_hours=1.0, note="demo-bot: cilindro trocado")
        await ms.transition(db, wo, "closed", bot, at=now, check_permission=False, note="demo-bot: encerrada")
        machine = await db.get(Machine, a.machine_id)
        control_ref = await _control_ref(db, t.id, pos.id)
        await audit.record(db, t.id, bot.id, "demo_bot.replacement", "work_order", wo.id, {
            "alert_id": a.id, "position_id": pos.id, "position": pos.name,
            "machine": machine.name if machine else None, "removed_component_id": old.id if old else None,
            "installed_component_id": new.id, "work_order_number": wo.number, "virtual_plant_ref": control_ref})
        await db.commit()
        done.append({"alert_id": a.id, "work_order_id": wo.id, "position_id": pos.id, "virtual_plant_ref": control_ref})
        if control_ref:
            await _virtual_plant_replace(control_ref)
    return done


async def _control_ref(db: AsyncSession, tenant_id: str, position_id: str) -> Optional[str]:
    """Position reference of the virtual plant control API: the edge key of its stroke tag without the signal
    ("montagem.fixacao.b3.stroke_time" → "montagem.fixacao.b3")."""
    tags = (await db.execute(select(Tag).where(Tag.tenant_id == tenant_id, Tag.position_id == position_id,
                                               Tag.semantic_role == SemanticRole.stroke_time))).scalars().all()
    for tg in tags:
        key = (tg.edge or {}).get("key")
        if key and "." in key:
            return key.rsplit(".", 1)[0]
    return None


async def _virtual_plant_replace(ref: str) -> None:
    url = settings.demo_virtual_plant_control_url.rstrip("/")
    if not url:
        return
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{url}/replace", json={"position": ref, "reset_counter": False,
                                                     "reason": "preventive_replacement"})
        log.info("demo_bot.virtual_plant_replace", ref=ref, status=r.status_code)
    except Exception as e:  # noqa: BLE001 — the WO is done; the virtual plant keeps wearing until next time
        log.warning("demo_bot.virtual_plant_replace_failed", ref=ref, error=str(e)[:200])


async def run_demo_bot() -> None:
    """Background loop (main lifespan). Cheap when there is no demo tenant (one indexed query per tick)."""
    from ...db import AsyncSessionLocal
    while True:
        try:
            async with AsyncSessionLocal() as db:
                done = await run_once(db)
                if done:
                    log.info("demo_bot.replacements", count=len(done))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("demo_bot.tick_failed", error=str(e)[:300])
        await asyncio.sleep(settings.demo_bot_tick_s)
