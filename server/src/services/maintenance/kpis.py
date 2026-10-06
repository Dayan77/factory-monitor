"""Management KPIs (kpis.md §3: PMP, schedule compliance, backlog) and reliability (kpis.md §2:
MTBF/MCBF/MTTR by position, machine, component type, manufacturer; MTTF by manufacturer/model).
All queries filter tenant_id; `plant_id` narrows to the machines of that plant."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import (Component, FailureRecord, Line, Machine, MaintenanceAbsence, MaintenancePlan, Plant,
                       Position, Tag, User, WorkOrder)
from ...models.tag import SemanticRole
from .. import clock
from ..usage import queries as uq
from . import core
from . import usage as mu
from .service import evaluate_plans, settings_for, wo_label


async def scope_machines(db: AsyncSession, tenant_id: str, plant_id: Optional[str] = None,
                         line_id: Optional[str] = None) -> list[str]:
    q = select(Machine.id).join(Line, Line.id == Machine.line_id).where(Machine.tenant_id == tenant_id)
    if plant_id:
        q = q.where(Line.plant_id == plant_id)
    if line_id:
        q = q.where(Line.id == line_id)
    return list((await db.execute(q)).scalars().all())


def _local_midnight(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time(0), tzinfo=tz).astimezone(timezone.utc)


def default_month(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    today = now.astimezone(tz).date()
    return _local_midnight(today.replace(day=1), tz), now


def default_reliability_window(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """S4-8: 12 months ending today 00:00 local."""
    today = now.astimezone(tz).date()
    try:
        first = today.replace(year=today.year - 1)
    except ValueError:                      # 29/02
        first = today.replace(year=today.year - 1, day=28)
    return _local_midnight(first, tz), _local_midnight(today, tz)


def _facts(w: WorkOrder) -> core.WoFacts:
    return core.WoFacts(id=w.id, label=f"{w.number:09d}", type=w.type, urgency=w.urgency, state=w.state,
                        labor_hours=float(w.labor_hours or 0.0), estimated_hours=float(w.estimated_hours or 0.0),
                        remaining_hours=w.remaining_hours, scheduled_for=w.scheduled_for, scheduled_at=w.scheduled_at,
                        completed_at=w.completed_at, cancel_reason=w.cancel_reason, plan_id=w.plan_id)


async def crew_capacity(db: AsyncSession, tenant_id: str, tz: ZoneInfo, now: datetime, hours_per_week: float,
                        line_ids: Optional[set[str]] = None) -> dict:
    """kpis.md §3.3: technicians × hours/week − planned absences of the current (local) week."""
    users = (await db.execute(select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True)))).scalars().all()
    techs = [u for u in users if "technician" in u.role_set
             and (line_ids is None or not u.line_ids or set(map(str, u.line_ids)) & line_ids)]
    today = now.astimezone(tz).date()
    monday = today - timedelta(days=today.weekday())
    absences = 0.0
    if techs:
        rows = (await db.execute(select(MaintenanceAbsence.hours).where(
            MaintenanceAbsence.tenant_id == tenant_id, MaintenanceAbsence.user_id.in_([u.id for u in techs]),
            MaintenanceAbsence.date >= monday, MaintenanceAbsence.date < monday + timedelta(days=7)))).scalars().all()
        absences = float(sum(rows))
    cap = len(techs) * hours_per_week - absences
    return {"technicians": len(techs), "technician_hours_per_week": hours_per_week,
            "planned_absence_hours": absences, "crew_capacity_hours_per_week": max(cap, 0.0)}


async def management_kpis(db: AsyncSession, tenant_id: str, plant_id: Optional[str], start: Optional[datetime],
                          stop: Optional[datetime], now: Optional[datetime] = None) -> dict:
    now = now or clock.now()
    tz = await mu.tenant_tz(db, tenant_id, plant_id)
    if start is None or stop is None:
        ds, de = default_month(now, tz)
        start, stop = start or ds, stop or de
    mids = await scope_machines(db, tenant_id, plant_id)
    cfg = await settings_for(db, tenant_id)
    first_day = start.astimezone(tz).date()
    end_day = (stop - timedelta(microseconds=1)).astimezone(tz).date() + timedelta(days=1)
    wos = []
    if mids:
        wos = list((await db.execute(select(WorkOrder).where(
            WorkOrder.tenant_id == tenant_id, WorkOrder.machine_id.in_(mids),
            or_(and_(WorkOrder.completed_at >= start, WorkOrder.completed_at < stop),
                and_(WorkOrder.scheduled_for >= first_day, WorkOrder.scheduled_for < end_day),
                WorkOrder.state.in_(core.OPEN_STATES))))).scalars().all())
    facts = [_facts(w) for w in wos]
    numbers = {w.id: wo_label(w.number) for w in wos}
    p = core.pmp(facts, start, stop)
    sc = core.schedule_compliance(facts, first_day, end_day, tz, cfg.schedule_tolerance_days)
    lines = None
    if plant_id:
        lines = set((await db.execute(select(Line.id).where(Line.tenant_id == tenant_id,
                                                             Line.plant_id == plant_id))).scalars().all())
    cap = await crew_capacity(db, tenant_id, tz, now, cfg.technician_hours_per_week, lines)
    plans = list((await db.execute(select(MaintenancePlan).where(
        MaintenancePlan.tenant_id == tenant_id, MaintenancePlan.is_active.is_(True),
        MaintenancePlan.machine_id.in_(mids)))).scalars().all()) if mids else []
    overdue_plans = [ev.plan.id for ev in await evaluate_plans(db, tenant_id, plans, now)
                     if ev.status is not None and ev.status.state == "overdue"]
    today = now.astimezone(tz).date()
    bl = core.backlog(facts, today, cap["crew_capacity_hours_per_week"], overdue_plans)
    return {
        "plant_id": plant_id, "period_start": clock.iso(start),
        "period_end": clock.iso(stop), "today": today.isoformat(),
        "pmp": p["pmp"], "pmp_reason": p["reason"], "planned_hours": p["planned_hours"],
        "unplanned_hours": p["unplanned_hours"], "total_hours": p["total_hours"],
        "pmp_work_order_ids": p["work_orders"],
        "schedule_compliance": sc["schedule_compliance"], "compliance_reason": sc["reason"],
        "schedule_tolerance_days": cfg.schedule_tolerance_days,
        "scheduled_count": sc["scheduled"], "on_time_count": sc["on_time"], "late_count": sc["late"],
        "not_done_count": sc["not_done"],
        "compliance_denominator_ids": sc["denominator_ids"], "compliance_on_time_ids": sc["on_time_ids"],
        "compliance_late_ids": sc["late_ids"], "compliance_not_done_ids": sc["not_done_ids"],
        "compliance_excluded": [{**e, "wo_number": numbers.get(e["wo_id"])} for e in sc["compliance_excluded"]],
        "backlog_hours": bl["backlog_hours"], "backlog_count": bl["backlog_count"], "backlog_weeks": bl["backlog_weeks"],
        "backlog_weeks_reason": bl["backlog_weeks_reason"], **cap,
        "backlog_remaining_by_wo": bl["remaining_by_wo"],
        "overdue_count": bl["overdue_count"], "overdue_hours": bl["overdue_hours"], "overdue_ids": bl["overdue_ids"],
        "drafts_not_in_backlog": bl["drafts_not_in_backlog"], "requests_pending": bl["requests_pending"],
        "work_order_numbers": numbers,
        # predictive.md P-9: cost of predictive false alarms, shown apart (PMP formula unchanged)
        "predictive_no_defect_hours": sum(float(w.labor_hours or 0.0) for w in wos
                                          if w.type == "predictive" and w.state in ("completed", "closed")
                                          and w.finding == "no_defect" and w.completed_at is not None
                                          and start <= w.completed_at < stop),
    }


# ── reliability ───────────────────────────────────────────────────────────────

GROUPS = ("position", "machine", "component_type", "manufacturer", "manufacturer_model")


def attribute_component(f: FailureRecord, comps_by_pos: dict[str, list[Component]]) -> Optional[str]:
    """S4-9: the component installed in the position at failed_at."""
    if f.component_id:
        return f.component_id
    for c in comps_by_pos.get(f.position_id or "", []):
        if c.installed_at is not None and c.installed_at <= f.failed_at and (c.removed_at is None or f.failed_at <= c.removed_at):
            return c.id
    return None


async def reliability(db: AsyncSession, tenant_id: str, group_by: str, start: Optional[datetime],
                      stop: Optional[datetime], plant_id: Optional[str] = None, line_id: Optional[str] = None,
                      now: Optional[datetime] = None) -> dict:
    now = now or clock.now()
    tz = await mu.tenant_tz(db, tenant_id, plant_id)
    if start is None or stop is None:
        ds, de = default_reliability_window(now, tz)
        start, stop = start or ds, stop or de
    mids = await scope_machines(db, tenant_id, plant_id, line_id)
    positions = list((await db.execute(select(Position).where(
        Position.tenant_id == tenant_id, Position.machine_id.in_(mids)))).scalars().all()) if mids else []
    pos_by_id = {p.id: p for p in positions}
    machines = {m.id: m for m in (await db.execute(select(Machine).where(
        Machine.tenant_id == tenant_id, Machine.id.in_(mids)))).scalars().all()} if mids else {}
    comps = list((await db.execute(select(Component).where(
        Component.tenant_id == tenant_id, Component.last_position_id.in_(list(pos_by_id))))).scalars().all()) \
        if pos_by_id else []
    comps_by_pos: dict[str, list[Component]] = defaultdict(list)
    for c in comps:
        comps_by_pos[c.last_position_id].append(c)
    comp_by_id = {c.id: c for c in comps}
    frs = list((await db.execute(select(FailureRecord).where(
        FailureRecord.tenant_id == tenant_id, FailureRecord.machine_id.in_(mids),
        FailureRecord.failed_at >= start, FailureRecord.failed_at < stop))).scalars().all()) if mids else []
    facts = [core.FailureFacts(f.id, f.severity, f.failed_at, f.restored_at, f.position_id,
                               attribute_component(f, comps_by_pos), f.machine_id) for f in frs]

    rows: list[dict] = []
    if group_by in ("position", "component_type"):
        use = await mu.window(db, tenant_id, "position", list(pos_by_id), start, stop)
        present = await mu.measures_present(db, tenant_id, "position", list(pos_by_id), start, stop)
        own_run = set((await db.execute(select(Tag.position_id).where(
            Tag.tenant_id == tenant_id, Tag.position_id.in_(list(pos_by_id)),
            Tag.semantic_role == SemanticRole.run_state, Tag.is_active.is_(True)))).scalars().all()) if pos_by_id else set()
        if group_by == "position":
            all_usage = await _comp_usage(db, tenant_id, comps)
            for p in sorted(positions, key=lambda p: (machines[p.machine_id].name if p.machine_id in machines else "", p.name)):
                u = use.get(p.id, {})
                cyc = u.get("cycles") if "cycles" in present.get(p.id, set()) else None
                r = core.reliability_row([f for f in facts if f.position_id == p.id], u.get("hours", 0.0), cyc)
                rows.append({"key": p.id, "label": p.name, "position_id": p.id, "position_name": p.name,
                             "machine_id": p.machine_id, "machine_name": getattr(machines.get(p.machine_id), "name", None),
                             "component_type": p.component_type.value,
                             "hours_source": "position" if p.id in own_run else "machine",
                             "estimated": False, **r, **_mttf_fields(comps_by_pos.get(p.id, []), all_usage)})
        else:
            by_type: dict[str, list[Position]] = defaultdict(list)
            for p in positions:
                by_type[p.component_type.value].append(p)
            for t, ps in sorted(by_type.items()):
                ids = {p.id for p in ps}
                hours = sum(use.get(p.id, {}).get("hours", 0.0) for p in ps)
                has_c = any("cycles" in present.get(p.id, set()) for p in ps)
                cyc = sum(use.get(p.id, {}).get("cycles", 0.0) for p in ps) if has_c else None
                r = core.reliability_row([f for f in facts if f.position_id in ids], hours, cyc)
                type_comps = [c for c in comps if c.component_type.value == t]
                rows.append({"key": t, "label": t, "component_type": t, "positions": len(ps), **r,
                             **_mttf_fields(type_comps, await _comp_usage(db, tenant_id, type_comps))})
    elif group_by == "machine":
        use = await mu.window(db, tenant_id, "machine", list(machines), start, stop)
        present = await mu.measures_present(db, tenant_id, "machine", list(machines), start, stop)
        for m in sorted(machines.values(), key=lambda m: m.name):
            u = use.get(m.id, {})
            cyc = u.get("cycles") if "cycles" in present.get(m.id, set()) else None
            r = core.reliability_row([f for f in facts if f.machine_id == m.id], u.get("hours", 0.0), cyc)
            mc = [c for c in comps if pos_by_id.get(c.last_position_id) is not None
                  and pos_by_id[c.last_position_id].machine_id == m.id]
            rows.append({"key": m.id, "label": m.name, "machine_id": m.id, "machine_name": m.name, **r,
                         **_mttf_fields(mc, await _comp_usage(db, tenant_id, mc))})
    elif group_by in ("manufacturer", "manufacturer_model"):
        use = await mu.window(db, tenant_id, "component", list(comp_by_id), start, stop)
        present = await mu.measures_present(db, tenant_id, "component", list(comp_by_id), start, stop)
        groups: dict[tuple, list[Component]] = defaultdict(list)
        for c in comps:
            key = (c.component_type.value, c.manufacturer or "—") if group_by == "manufacturer" \
                else (c.manufacturer or "—", c.model or "—")
            groups[key].append(c)
        totals = await _comp_usage(db, tenant_id, comps)
        for key, cs in sorted(groups.items()):
            ids = {c.id for c in cs}
            hours = sum(use.get(c.id, {}).get("hours", 0.0) for c in cs)
            has_c = any("cycles" in present.get(c.id, set()) for c in cs)
            cyc = sum(use.get(c.id, {}).get("cycles", 0.0) for c in cs) if has_c else None
            r = core.reliability_row([f for f in facts if f.component_id in ids], hours, cyc)
            head = ({"key": f"{key[0]}|{key[1]}", "label": f"{key[1]} ({key[0]})", "component_type": key[0],
                     "manufacturer": key[1]} if group_by == "manufacturer" else
                    {"key": f"{key[0]}|{key[1]}", "label": f"{key[0]} {key[1]}", "manufacturer": key[0],
                     "model": key[1], "component_type": cs[0].component_type.value})
            rows.append({**head, "components": len(cs), **r, **_mttf_fields(cs, totals)})
    else:
        raise ValueError(group_by)
    return {"group_by": group_by, "period_start": clock.iso(start), "period_end": clock.iso(stop), "plant_id": plant_id, "rows": rows}


async def _comp_usage(db: AsyncSession, tenant_id: str, comps: list[Component]) -> dict[str, dict]:
    return await mu.totals(db, tenant_id, "component", [c.id for c in comps])


def _mttf_fields(cs: list[Component], totals: dict[str, dict]) -> dict:
    life = [core.ComponentLife(c.id, c.manufacturer, c.model, c.component_type.value, c.nominal_life or {},
                               totals.get(c.id, {}), installed=c.position_id is not None and c.removed_at is None,
                               removed=c.removed_at is not None, removal_reason=c.removal_reason.value if c.removal_reason else None)
            for c in cs if c.installed_at is not None or c.removed_at is not None]
    m = core.mttf_row(life)
    return {"mttf_measure": m["measure"], "mttf": m["mttf"], "mttf_h": m["mttf_h"], "mctf": m["mctf"],
            "failures_removed": m["failures"], "suspensions": m["suspensions"], "in_service": m["in_service"],
            "small_sample": m["small_sample"], "mttf_reason": m["reason"]}


# ── components by % of life (S4-12) ──────────────────────────────────────────

async def components_life(db: AsyncSession, tenant_id: str, plant_id: Optional[str] = None,
                          line_id: Optional[str] = None, now: Optional[datetime] = None) -> list[dict]:
    now = now or clock.now()
    mids = await scope_machines(db, tenant_id, plant_id, line_id)
    if not mids:
        return []
    rows = (await db.execute(select(Component, Position, Machine).join(Position, Position.id == Component.position_id)
                             .join(Machine, Machine.id == Position.machine_id)
                             .where(Component.tenant_id == tenant_id, Component.removed_at.is_(None),
                                    Machine.id.in_(mids)))).all()
    tzs = await mu.machine_tz(db, tenant_id, mids)
    out = []
    for c, p, m in rows:
        d = await uq.component_usage_detail(db, tenant_id, c, tzs.get(m.id) or await mu.tenant_tz(db, tenant_id), now)
        life = d["life_consumed_pct"]
        nominal = {k: v for k, v in (c.nominal_life or {}).items() if isinstance(v, (int, float)) and v > 0}
        out.append({
            "component_id": c.id, "serial_number": c.serial_number, "manufacturer": c.manufacturer, "model": c.model,
            "component_type": c.component_type.value, "position_id": p.id, "position_name": p.name,
            "machine_id": m.id, "machine_name": m.name, "installed_at": clock.iso(c.installed_at),
            "nominal_life": nominal,
            "usage_since_install": {"cycles": d["cycles"], "hours": d["hours"], "starts": d["starts"]},
            "life_consumed_pct": life, "life_measure": d["life_measure"], "life_reason": d["life_reason"],
            "life_by_measure": {k: (d.get(k, 0.0) or 0.0) / v for k, v in nominal.items()},
            "rul_days": d["rul_days"], "rul_measure": d["rul_measure"], "rul_reason": d["rul_reason"],
            "projected_end_date": d["projected_end_date"].isoformat() if d["projected_end_date"] else None,
            "low_confidence": d["low_confidence"],
            "alert": None if life is None else ("nominal_reached" if life >= 1.0 else "attention" if life >= 0.8 else None),
        })
    out.sort(key=lambda r: -(r["life_consumed_pct"] or -1))
    return out
