"""
Maintenance background engine (started in the app lifespan).

Every `maintenance_tick_s` (30 s), per tenant:
  * plans: full recompute every `maintenance_plan_recompute_s` (hourly) or as soon as the tenant's
    usage accumulators changed since the last pass ("on usage change") → states, single draft WO at
    ≥ 80 % (S4-4), state-change notifications;
  * component life ≥ 80 % / ≥ 100 % → planner notification (once per component and level);
  * work orders overdue (`scheduled_for < today`, not completed) → planner + assignee, once;
  * emergency call: open EQP-* failure (stopped production, not restored) → on-call technicians of
    the line + supervisors (push with sound, cannot be muted);
  * escalation: failure still open after 2 × MTTR of the position (12 months; fallback machine, then
    the tenant default) → planners + supervisors;
  * daily digest e-mail at 07:00 plant local.
Every `webhook_tick_s`: pending webhook deliveries (with retries).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import FailureRecord, MaintenancePlan, Notification, Tenant, UsageTotal, User, WorkOrder
from ...notifications import service as notif
from .. import clock, webhooks
from . import core
from . import usage as mu
from .kpis import components_life, default_reliability_window
from .service import (PLANNERS, SUPERVISORS, emergency_call, engine_emergency_work_order, machine_line,
                      run_plan_engine, settings_for, wo_label)

log = structlog.get_logger()

_last_full: dict[str, datetime] = {}
_last_usage_seen: dict[str, datetime] = {}


async def tick_tenant(db: AsyncSession, tenant_id: str, now: Optional[datetime] = None, force_plans: bool = False) -> dict:
    now = now or clock.now()
    out = {"plans": 0, "component_alerts": 0, "overdue": 0, "emergency": 0, "escalations": 0, "digests": 0}
    usage_mark = (await db.execute(select(func.max(UsageTotal.updated_at)).where(UsageTotal.tenant_id == tenant_id))).scalar_one()
    last = _last_full.get(tenant_id)
    due = force_plans or last is None or (now - last).total_seconds() >= settings.maintenance_plan_recompute_s
    changed = usage_mark is not None and usage_mark != _last_usage_seen.get(tenant_id)
    if due or changed:
        plans = await _stage(db, tenant_id, "plans", lambda: run_plan_engine(db, tenant_id, now=now))
        out["plans"] = len(plans) if plans is not None else 0
        out["component_alerts"] = await _stage(db, tenant_id, "component_alerts",
                                               lambda: component_alerts(db, tenant_id, now)) or 0
        _last_full[tenant_id] = now
        if usage_mark is not None:
            _last_usage_seen[tenant_id] = usage_mark
    # each stage in its own savepoint: a bad plan can never suppress an emergency call or an escalation
    out["emergency"] = await _stage(db, tenant_id, "emergency", lambda: emergency_calls(db, tenant_id, now)) or 0
    out["escalations"] = await _stage(db, tenant_id, "escalations", lambda: escalations(db, tenant_id, now)) or 0
    out["overdue"] = await _stage(db, tenant_id, "overdue", lambda: overdue_work_orders(db, tenant_id, now)) or 0
    await db.commit()
    await notif.deliver_pending(db)
    tz = await mu.tenant_tz(db, tenant_id)
    cfg = await settings_for(db, tenant_id)
    local = now.astimezone(tz)
    out["digests"] = await notif.send_digests(db, tenant_id, local.date(), local.hour, cfg.digest_hour_local)
    return out


async def _stage(db: AsyncSession, tenant_id: str, name: str, fn):
    """Run one engine stage inside a savepoint; log and swallow its error so the other stages still run."""
    try:
        async with db.begin_nested():
            return await fn()
    except Exception:  # noqa: BLE001 — isolation between stages is the point
        log.exception("maintenance.engine.stage_error", tenant_id=tenant_id, stage=name)
        return None


async def component_alerts(db: AsyncSession, tenant_id: str, now: datetime) -> int:
    n = 0
    for c in await components_life(db, tenant_id, now=now):
        alert = c["alert"]
        if alert is None:
            continue
        key = f"component_life:{c['component_id']}:{alert}"
        seen = (await db.execute(select(Notification.id).where(Notification.tenant_id == tenant_id,
                                                               Notification.group_key == key).limit(1))).first()
        if seen:
            continue
        line_id = await machine_line(db, tenant_id, c["machine_id"])
        planners = await notif.users_with_roles(db, tenant_id, ("maintenance_planner",), line_id)
        label = "vida nominal atingida" if alert == "nominal_reached" else "≥ 80 % da vida"
        await notif.notify(db, tenant_id, planners, "component_life",
                           f"{c['position_name']} ({c['manufacturer'] or ''} {c['serial_number'] or ''}): {label} "
                           f"({c['life_consumed_pct'] * 100:.0f} %)",
                           body=f"Fim projetado {c['projected_end_date'] or '—'}", link="/maintenance/components",
                           data={"component_id": c["component_id"], "alert": alert}, group_key=key, line_id=line_id)
        n += 1
    return n


async def overdue_work_orders(db: AsyncSession, tenant_id: str, now: datetime) -> int:
    wos = (await db.execute(select(WorkOrder).where(
        WorkOrder.tenant_id == tenant_id, WorkOrder.state.in_(core.BEFORE_COMPLETED),
        WorkOrder.scheduled_for.is_not(None), WorkOrder.overdue_notified_on.is_(None)))).scalars().all()
    n = 0
    tzs = await mu.machine_tz(db, tenant_id, [w.machine_id for w in wos])
    for w in wos:
        tz = tzs.get(w.machine_id) or await mu.tenant_tz(db, tenant_id)
        today = now.astimezone(tz).date()
        if not w.scheduled_for < today:
            continue
        w.overdue_notified_on = today
        line_id = await machine_line(db, tenant_id, w.machine_id)
        users = await notif.users_with_roles(db, tenant_id, ("maintenance_planner",), line_id)
        if w.assignee_id:
            a = await db.get(User, w.assignee_id)
            if a is not None and a.tenant_id == tenant_id:
                users.append(a)
        await notif.notify(db, tenant_id, users, "wo_overdue",
                           f"{wo_label(w.number)} vencida (programada para {w.scheduled_for:%d/%m})", body=w.title,
                           link=f"/maintenance/work-orders/{w.id}", data={"work_order_id": w.id},
                           group_key="wo_overdue", line_id=line_id, grouped_title="{count} OS vencidas")
        n += 1
    return n


async def emergency_calls(db: AsyncSession, tenant_id: str, now: datetime) -> int:
    cfg = await settings_for(db, tenant_id)
    frs = (await db.execute(select(FailureRecord).where(
        FailureRecord.tenant_id == tenant_id, FailureRecord.emergency_notified_at.is_(None),
        FailureRecord.downtime_event_id.is_not(None), FailureRecord.restored_at.is_(None),
        FailureRecord.severity.in_(core.COUNTED_SEVERITIES)))).scalars().all()
    n = 0
    for f in frs:
        if (now - f.failed_at).total_seconds() < cfg.emergency_call_after_s:
            continue
        f.emergency_notified_at = now
        from ...models import Machine
        m = await db.get(Machine, f.machine_id)
        line_id = await machine_line(db, tenant_id, f.machine_id)
        planners = await notif.users_with_roles(db, tenant_id, ("maintenance_planner",), line_id)
        await notif.notify(db, tenant_id, planners, "failure_new",
                           f"Falha: {m.name if m else 'máquina'} parou (EQP)", link=f"/maintenance/failures/{f.id}",
                           data={"failure_id": f.id}, group_key="failure_new", line_id=line_id,
                           grouped_title="{count} falhas novas registradas")
        await webhooks.enqueue(db, tenant_id, "failure.created",
                               {"id": f.id, "machine_id": f.machine_id, "failed_at": clock.iso(f.failed_at),
                                "severity": f.severity, "downtime_event_id": f.downtime_event_id})
        mins = max(int((now - f.failed_at).total_seconds() // 60), 0)
        # workflows §3: the call carries the emergency WO so the technician can take it on /m
        wo = await engine_emergency_work_order(db, f, f"Chamado emergencial — {m.name if m else 'máquina'}", now)
        await emergency_call(db, tenant_id, f.machine_id,
                             f"Chamado emergencial: {m.name if m else 'máquina'} — falha há {mins} min",
                             body="Parada por falha de equipamento (EQP).", link=f"/m/work-orders/{wo.id}",
                             data={"failure_id": f.id, "downtime_event_id": f.downtime_event_id,
                                   "machine_id": f.machine_id, "work_order_id": wo.id})
        n += 1
    return n


async def position_mttr_s(db: AsyncSession, tenant_id: str, f: FailureRecord, now: datetime, default_s: float) -> float:
    tz = await mu.tenant_tz(db, tenant_id)
    start, stop = default_reliability_window(now, tz)
    for col, val in ((FailureRecord.position_id, f.position_id), (FailureRecord.machine_id, f.machine_id)):
        if val is None:
            continue
        rows = (await db.execute(select(FailureRecord.failed_at, FailureRecord.restored_at).where(
            FailureRecord.tenant_id == tenant_id, col == val, FailureRecord.restored_at.is_not(None),
            FailureRecord.severity.in_(core.COUNTED_SEVERITIES), FailureRecord.failed_at >= start,
            FailureRecord.failed_at < stop, FailureRecord.id != f.id))).all()
        if rows:
            return sum((r - a).total_seconds() for a, r in rows) / len(rows)
    return default_s


async def escalations(db: AsyncSession, tenant_id: str, now: datetime) -> int:
    cfg = await settings_for(db, tenant_id)
    frs = (await db.execute(select(FailureRecord).where(
        FailureRecord.tenant_id == tenant_id, FailureRecord.escalated_at.is_(None),
        FailureRecord.restored_at.is_(None), FailureRecord.downtime_event_id.is_not(None),
        FailureRecord.severity.in_(core.COUNTED_SEVERITIES)))).scalars().all()
    n = 0
    for f in frs:
        mttr = await position_mttr_s(db, tenant_id, f, now, cfg.default_mttr_min * 60)
        if (now - f.failed_at).total_seconds() <= 2 * mttr:
            continue
        f.escalated_at = now
        line_id = await machine_line(db, tenant_id, f.machine_id)
        users = await notif.users_with_roles(db, tenant_id, ("maintenance_planner",) + SUPERVISORS, line_id)
        mins = int((now - f.failed_at).total_seconds() // 60)
        await notif.notify(db, tenant_id, users, "failure_escalation",
                           f"Falha aberta há {mins} min (> 2 × MTTR de {mttr / 60:.0f} min)",
                           link=f"/maintenance/failures/{f.id}", data={"failure_id": f.id, "mttr_s": mttr},
                           line_id=line_id)
        n += 1
    return n


async def run_maintenance_engine() -> None:
    from ...db import AsyncSessionLocal
    while True:
        try:
            async with AsyncSessionLocal() as db:
                tenants = (await db.execute(select(Tenant.id).where(Tenant.is_active.is_(True)))).scalars().all()
            for tid in tenants:
                try:
                    async with AsyncSessionLocal() as db:
                        await tick_tenant(db, tid)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.error("maintenance.engine.tenant_error", tenant_id=tid[:8], error=f"{type(e).__name__}: {e}"[:300])
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("maintenance.engine.error", error=str(e)[:300])
        await asyncio.sleep(settings.maintenance_tick_s)


async def run_webhook_worker() -> None:
    from ...db import AsyncSessionLocal
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await webhooks.deliver_due(db)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("webhooks.worker.error", error=str(e)[:300])
        await asyncio.sleep(settings.webhook_tick_s)
