"""
OEE / Pareto / reliability by scope (machine | line | plant) — kpis.md §1.6:
  * machine: its own events, data and counters, PRI of (machine, product);
  * line: the output machine (Line.output_machine_id) for PQ/GQ and APT, and the line PRI =
    PRI of the bottleneck (Line.bottleneck_machine_id) for the product, else the output machine's;
  * plant: sum of the components of its lines.
All aggregation sums components and recomputes the ratios (never a mean of percentages).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Line, Machine, Plant
from ..usage import queries as uq
from ..validation import is_uuid
from . import kpi
from .calendar import Calendar, ShiftInstance
from .engine import (MachineData, ProductPri, components, failure_rows, load_calendar, load_machines_data,
                     machine_infos, operating_seconds, outside_production, products_pri, reason_meta, short_faults,
                     window_components, windows)

MAX_RANGE_DAYS = 400
MAX_HOUR_POINTS = 24 * 62


@dataclass
class Unit:
    """One production unit of a scope: machine data + the PRI used for it."""
    md: MachineData
    pri: Optional[ProductPri] = None
    label: str = ""


@dataclass
class Scope:
    kind: str
    id: str
    tenant_id: str
    tz: ZoneInfo
    cal: Calendar
    plant_id: str
    machine_ids: list[str] = field(default_factory=list)       # reliability: every machine in scope
    units_spec: list[tuple[str, Optional[str]]] = field(default_factory=list)   # (unit machine, pri machine)
    reason: Optional[str] = None


def _404(label: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} não encontrado(a).")


async def resolve_scope(db: AsyncSession, tenant_id: str, kind: str, scope_id: str) -> Scope:
    if not is_uuid(scope_id or ""):
        raise _404({"machine": "Máquina", "line": "Linha", "plant": "Planta"}.get(kind, "Escopo"))
    if kind == "machine":
        infos = await machine_infos(db, tenant_id, [scope_id])
        if scope_id not in infos:
            raise _404("Máquina")
        info = infos[scope_id]
        cal = await load_calendar(db, tenant_id, info.plant_id, info.tz)
        return Scope(kind, scope_id, tenant_id, info.tz, cal, info.plant_id, [scope_id], [(scope_id, None)])
    if kind == "line":
        line = (await db.execute(select(Line).where(Line.id == scope_id, Line.tenant_id == tenant_id))).scalar_one_or_none()
        if line is None:
            raise _404("Linha")
        return await _line_scope(db, tenant_id, line)
    if kind == "plant":
        plant = (await db.execute(select(Plant).where(Plant.id == scope_id, Plant.tenant_id == tenant_id))).scalar_one_or_none()
        if plant is None:
            raise _404("Planta")
        tz = uq.tzinfo(plant.timezone)
        cal = await load_calendar(db, tenant_id, plant.id, tz)
        lines = (await db.execute(select(Line).where(Line.tenant_id == tenant_id, Line.plant_id == plant.id)
                                  .order_by(Line.name))).scalars().all()
        sc = Scope(kind, scope_id, tenant_id, tz, cal, plant.id)
        for ln in lines:
            ls = await _line_scope(db, tenant_id, ln, cal, tz)
            sc.machine_ids += ls.machine_ids
            sc.units_spec += ls.units_spec
        if not sc.units_spec:
            sc.reason = "no_output_machine"
        return sc
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="scope: machine | line | plant")


async def _line_scope(db: AsyncSession, tenant_id: str, line: Line, cal: Optional[Calendar] = None,
                      tz: Optional[ZoneInfo] = None) -> Scope:
    if tz is None:
        tzname = (await db.execute(select(Plant.timezone).where(Plant.id == line.plant_id))).scalar_one()
        tz = uq.tzinfo(tzname)
    if cal is None:
        cal = await load_calendar(db, tenant_id, line.plant_id, tz)
    mids = (await db.execute(select(Machine.id).where(Machine.tenant_id == tenant_id, Machine.line_id == line.id)
                             .order_by(Machine.name))).scalars().all()
    sc = Scope("line", line.id, tenant_id, tz, cal, line.plant_id, list(mids))
    if line.output_machine_id:
        sc.units_spec = [(line.output_machine_id, line.bottleneck_machine_id)]
    else:
        sc.reason = "no_output_machine"
    return sc


async def load_units(db: AsyncSession, sc: Scope, start: datetime, stop: datetime, now: datetime,
                     mds: Optional[dict[str, MachineData]] = None) -> list[Unit]:
    """Production units of the scope. `mds`: machine data already loaded for the same window (overview, map) —
    reused instead of read again. Batched: one query per table for every unit machine (Sprint 7)."""
    ids = {m for m, _ in sc.units_spec} | {b for _, b in sc.units_spec if b}
    infos = await machine_infos(db, sc.tenant_id, ids)
    need = {m: infos[m] for m, _ in sc.units_spec if m in infos and not (mds and m in mds)}
    loaded = {**(mds or {}), **(await load_machines_data(db, need, sc.cal, start, stop, now) if need else {})}
    botts = {b: infos[b] for m, b in sc.units_spec if b and b != m and b in infos}
    bpris = await products_pri(db, sc.tenant_id, botts) if botts else {}
    units = []
    for mid, bott in sc.units_spec:
        if mid not in infos:
            continue
        md = loaded[mid]
        pri = None
        if bott in bpris:
            bp = bpris[bott]
            # kpis.md §1.6 + S7-4 (audit A-13): line PRI = bottleneck PRI for the product; a product without PRI at
            # the bottleneck uses the output machine's, flagged (`line_pri_fallback`) instead of silently
            fb = {p for p, v in md.pri.pri.items() if p not in bp.pri and v is not None}
            if md.pri.default_product in bp.pri:
                fallback = bp.pri.get(md.pri.default_product)
            elif bp.fallback_pri is not None:
                fallback = bp.fallback_pri
            else:
                fallback = md.pri.fallback_pri
                if fallback is not None:
                    fb.add(None)
            pri = ProductPri(pri={**md.pri.pri, **bp.pri}, default_product=md.pri.default_product,
                             fallback_pri=fallback, fallback_ids=frozenset(fb))
        units.append(Unit(md, pri, infos[mid].name))
    return units


def parse_window(sc: Scope, start: Optional[datetime], stop: Optional[datetime], now: datetime,
                 group_by: str = "none") -> tuple[datetime, datetime]:
    if start is not None and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if stop is not None and stop.tzinfo is None:
        stop = stop.replace(tzinfo=timezone.utc)
    start = start or uq.local_midnight_utc(now, sc.tz)
    stop = stop or now
    if stop <= start:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="stop deve ser após start.")
    if stop - start > timedelta(days=MAX_RANGE_DAYS):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Intervalo máximo: {MAX_RANGE_DAYS} dias.")
    if group_by == "hour" and (stop - start) > timedelta(hours=MAX_HOUR_POINTS):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="group_by=hour: intervalo máximo de 62 dias.")
    return start, stop


def total_components(units: list[Unit], start: datetime, stop: datetime, now: datetime) -> kpi.Components:
    c = kpi.Components()
    for u in units:
        c.add(components(u.md, start, stop, now, u.pri))
    if not units:
        c.calendar_s = max((min(stop, now) - start).total_seconds(), 0.0)
    elif len(units) > 1:
        c.calendar_s = max((min(stop, now) - start).total_seconds(), 0.0) * len(units)
    return c


def oee_payload(sc: Scope, c: kpi.Components, start: datetime, stop: datetime, granularity: str) -> dict:
    r = kpi.oee(c)
    if sc.reason and not c.pot_s:
        r["reason"] = sc.reason
    return {"scope": sc.kind, "id": sc.id, "period_start": start, "period_end": stop, "granularity": granularity,
            "calendar_default": bool(getattr(sc.cal, "default", False)), **r, "points": []}


async def oee_for(db: AsyncSession, sc: Scope, start: datetime, stop: datetime, group_by: str, now: datetime) -> dict:
    units = await load_units(db, sc, start, stop, now)
    points, parts = [], []
    if group_by == "shift_name":
        return _by_shift_name(sc, units, start, stop, now)
    if group_by != "none":
        for w in windows(sc.cal, sc.tz, start, stop, group_by):
            wc = kpi.Components()
            for u in units:
                wc.add(window_components(u.md, w, now, u.pri))
            if len(units) > 1:
                wc.calendar_s = max((min(w.end, now) - w.start).total_seconds(), 0.0) * len(units)
            elif not units:
                wc.calendar_s = max((min(w.end, now) - w.start).total_seconds(), 0.0)
            parts.append(wc)
            r = kpi.oee(wc)
            points.append({"t": w.start, "end": w.end, "label": w.label, "availability": r["availability"],
                           "performance": r["performance"], "quality": r["quality"], "oee": r["oee"],
                           "teep": r["teep"], "pbt_s": r["pbt_s"], "good": wc.good, "scrap": wc.scrap})
    if group_by in ("shift", "day"):
        # S3-8: the total of a grouped query is the sum of its points (same shift instances)
        c = kpi.total(parts)
        # calendar time (TEEP): the days of the window (D10); for shifts, the window itself
        c.calendar_s = (sum(p.calendar_s for p in parts) if group_by == "day"
                        else max((min(stop, now) - start).total_seconds(), 0.0) * max(1, len(units)))
    else:
        c = total_components(units, start, stop, now)
    out = oee_payload(sc, c, start, stop, group_by if group_by != "none" else "interval")
    out["points"] = points
    return out


async def pareto_for(db: AsyncSession, sc: Scope, start: datetime, stop: datetime, now: datetime,
                     by: str = "code") -> list[dict]:
    units = await load_units(db, sc, start, stop, now)
    c = total_components(units, start, stop, now)
    return kpi.pareto(c, await reason_meta(db, sc.tenant_id), by=by)


async def reliability_for(db: AsyncSession, sc: Scope, start: datetime, stop: datetime, now: datetime) -> dict:
    end = min(stop, now)
    op = await operating_seconds(db, sc.tenant_id, sc.machine_ids, start, end, now)
    rows = await failure_rows(db, sc.tenant_id, sc.machine_ids, start, end)
    sf = await short_faults(db, sc.tenant_id, sc.machine_ids, start, end)
    counted = [r for r in rows if r[0] in kpi.COUNTED_SEVERITIES]
    outside = await outside_production(db, sc.tenant_id, [(r[5], r[3], r[4]) for r in counted])
    r = kpi.reliability(op, [(sev, d) for sev, d, *_ in rows], sf, outside_production=len(outside),
                        repair_s=[x[2] for x in counted if x[2] is not None])
    return {"scope": sc.kind, "id": sc.id, "period_start": start, "period_end": stop, **r}


def instances_of_day(cal: Calendar, day: date, shift_id: Optional[str] = None) -> list[ShiftInstance]:
    return [i for i in cal.day_instances(day) if shift_id is None or i.shift_id == shift_id]


def productive_today(cal: Calendar, now: datetime) -> date:
    inst = cal.instance_at(now)
    return inst.productive_day if inst else now.astimezone(cal.tz).date()


def day_components(units: list[Unit], insts: list[ShiftInstance], now: datetime) -> kpi.Components:
    c = kpi.Components()
    for u in units:
        for i in insts:
            c.add(components(u.md, i.start, i.end, now, u.pri))
    return c


def _by_shift_name(sc: Scope, units: list[Unit], start: datetime, stop: datetime, now: datetime) -> dict:
    """group_by=shift_name (ai_assistant.md / S3-8): every instance of the same shift in the period is summed
    (components, never a mean of percentages) — one point per shift name ("Turno A", "Turno C", …)."""
    groups: dict[str, list[kpi.Components]] = {}
    bounds: dict[str, tuple[datetime, datetime]] = {}
    for w in windows(sc.cal, sc.tz, start, stop, "shift"):
        wc = kpi.Components()
        for u in units:
            wc.add(window_components(u.md, w, now, u.pri))
        name = w.instances[0].name if w.instances else w.label
        groups.setdefault(name, []).append(wc)
        a, b = bounds.get(name, (w.start, w.end))
        bounds[name] = (min(a, w.start), max(b, w.end))
    points, parts = [], []
    for name in sorted(groups, key=lambda n: bounds[n][0].astimezone(sc.tz).timetz()):
        c = kpi.total(groups[name])
        parts.append(c)
        r = kpi.oee(c)
        points.append({"t": bounds[name][0], "end": bounds[name][1], "label": name, "availability": r["availability"],
                       "performance": r["performance"], "quality": r["quality"], "oee": r["oee"], "teep": r["teep"],
                       "pbt_s": r["pbt_s"], "good": c.good, "scrap": c.scrap, "instances": len(groups[name])})
    c = kpi.total(parts)
    c.calendar_s = max((min(stop, now) - start).total_seconds(), 0.0) * max(1, len(units))
    out = oee_payload(sc, c, start, stop, "shift_name")
    out["points"] = points
    return out


def oee_delta(cur: dict, prev: dict) -> dict:
    """compare_to=previous_period: differences computed by the server (the assistant never subtracts)."""
    out = {}
    for k in ("oee", "availability", "performance", "quality", "teep"):
        a, b = cur.get(k), prev.get(k)
        out[k] = (a - b) if a is not None and b is not None else None
    for k in ("good", "scrap", "adot_s", "pbt_s"):
        out[k] = (cur.get(k) or 0.0) - (prev.get(k) or 0.0)
    return out
