"""Fase 2 read/act endpoints: downtime (list, justify, manual), OEE, loss Pareto, reliability,
shift report and the plant 'raio-x' overview. Every query filters tenant_id."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, require_roles
from ...db import get_db
from ...models import (Component, DowntimeEvent, FailureRecord, Line, Machine, MachineStatus, Plant, Position,
                       ReasonCode, User, UserRole)
from ...services.oee import kpi
from ...services.oee.detector import ensure_failure, event_message
from ...services.oee.engine import (UNJUSTIFIED, components as machine_components, effective_reason, load_calendar,
                                    load_machine_data, machine_infos, reason_meta)
from ...services.oee.service import (oee_delta, day_components, instances_of_day, load_units, oee_for, oee_payload,
                                     pareto_for, parse_window, productive_today, reliability_for, resolve_scope)
from ...services.usage import queries as uq
from ...services.validation import is_uuid
from ...websocket.manager import manager
from ..schemas_production import (DowntimeCreate, DowntimeOut, DowntimePatch, MachineProduction, OeeOut,
                                  OverviewLine, OverviewOut, ParetoRow, ReliabilityOut, ShiftReportOut)
from ..tenant_refs import ensure_owned

CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]
OPERATOR = [Depends(require_roles(UserRole.operator))]
CLOCK_SKEW_S = 300

downtime_router = APIRouter()
kpi_router = APIRouter()


def _aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _422(msg: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg)


# ── downtime ────────────────────────────────────────────────────────────────

def _unjustified(ev: DowntimeEvent) -> bool:
    return ev.reason_code is None or ev.reason_code == UNJUSTIFIED


def _out(ev: DowntimeEvent, names: dict[str, tuple[str, str]]) -> DowntimeOut:
    name, line_id = names.get(ev.machine_id, (None, None))
    return DowntimeOut(
        id=ev.id, machine_id=ev.machine_id, machine_name=name, line_id=line_id, start=ev.start, end=ev.end,
        duration_s=ev.duration_s, stop_start=ev.stop_start, source=ev.source, reason_code=ev.reason_code,
        suggested_reason_code=ev.suggested_reason_code, effective_reason_code=effective_reason(ev),
        planned=bool(ev.planned), fault_code=ev.fault_code, fault_since=ev.fault_since, failure_id=ev.failure_id,
        comment=ev.comment, shift_id=ev.shift_id, productive_day=ev.productive_day,
        justified_by=ev.justified_by, justified_at=ev.justified_at, unjustified=_unjustified(ev))


async def _names(db: AsyncSession, tenant_id: str, mids) -> dict[str, tuple[str, str]]:
    mids = list(set(mids))
    if not mids:
        return {}
    rows = (await db.execute(select(Machine.id, Machine.name, Machine.line_id).where(
        Machine.tenant_id == tenant_id, Machine.id.in_(mids)))).all()
    return {i: (n, l) for i, n, l in rows}


async def query_downtime(db: AsyncSession, tenant_id: str, machine_ids: Optional[list[str]] = None,
                         start: Optional[datetime] = None, stop: Optional[datetime] = None,
                         open_: Optional[bool] = None, unjustified: Optional[bool] = None,
                         limit: int = 500) -> list[DowntimeEvent]:
    q = select(DowntimeEvent).where(DowntimeEvent.tenant_id == tenant_id)
    if machine_ids is not None:
        if not machine_ids:
            return []
        q = q.where(DowntimeEvent.machine_id.in_(machine_ids))
    if start is not None:
        q = q.where(or_(DowntimeEvent.end.is_(None), DowntimeEvent.end > start))
    if stop is not None:
        q = q.where(DowntimeEvent.start < stop)
    if open_ is not None:
        q = q.where(DowntimeEvent.end.is_(None) if open_ else DowntimeEvent.end.is_not(None))
    if unjustified is not None:
        cond = or_(DowntimeEvent.reason_code.is_(None), DowntimeEvent.reason_code == UNJUSTIFIED)
        q = q.where(cond if unjustified else ~cond)
    return list((await db.execute(q.order_by(DowntimeEvent.start.desc()).limit(max(1, min(limit, 5000))))).scalars())


@downtime_router.get("/", response_model=list[DowntimeOut])
async def list_downtime(user: CurrentUser, db: DB, machine_id: Optional[str] = None, line_id: Optional[str] = None,
                        start: Optional[datetime] = None, stop: Optional[datetime] = None,
                        open: Optional[bool] = None, unjustified: Optional[bool] = None,
                        limit: int = Query(default=500, ge=1, le=5000)):
    mids: Optional[list[str]] = None
    if machine_id:
        if not is_uuid(machine_id):
            return []
        mids = [machine_id]
    if line_id:
        if not is_uuid(line_id):
            return []
        lm = (await db.execute(select(Machine.id).where(Machine.tenant_id == user.tenant_id,
                                                        Machine.line_id == line_id))).scalars().all()
        mids = [m for m in lm if mids is None or m in mids]
    evs = await query_downtime(db, user.tenant_id, mids, _aware(start), _aware(stop), open, unjustified, limit)
    names = await _names(db, user.tenant_id, [e.machine_id for e in evs])
    return [_out(e, names) for e in evs]


async def _reason(db: AsyncSession, tenant_id: str, code: str) -> ReasonCode:
    rows = (await db.execute(select(ReasonCode).where(
        ReasonCode.code == code, ReasonCode.is_active.is_(True),
        or_(ReasonCode.tenant_id.is_(None), ReasonCode.tenant_id == tenant_id)))).scalars().all()
    if not rows:
        raise _422(f"reason_code desconhecido: {code}")
    return sorted(rows, key=lambda r: r.tenant_id is None)[0]


async def _sync_failure(db: AsyncSession, ev: DowntimeEvent) -> None:
    """EQP-* (domain.md §4.4): one FailureRecord draft per stop; a draft is removed when the
    operator reclassifies the stop as not an equipment failure."""
    siblings = [ev]
    if ev.source != "manual" and ev.stop_start is not None:
        siblings = list((await db.execute(select(DowntimeEvent).where(
            DowntimeEvent.tenant_id == ev.tenant_id, DowntimeEvent.machine_id == ev.machine_id,
            DowntimeEvent.stop_start == ev.stop_start, DowntimeEvent.source != "manual")
            .order_by(DowntimeEvent.start))).scalars())
        if ev not in siblings:
            siblings.insert(0, ev)
    eff = effective_reason(ev)
    if eff.startswith("EQP-"):
        last_end = siblings[-1].end
        await ensure_failure(db, siblings, failed_at=ev.fault_since or ev.stop_start or ev.start,
                             restored_at=last_end)
    elif ev.failure_id:
        fr = await db.get(FailureRecord, ev.failure_id)
        for s in siblings:
            s.failure_id = None
        if fr is not None and fr.status == "draft" and fr.tenant_id == ev.tenant_id:
            await db.flush()
            await db.delete(fr)


PREVENTIVE = "PLN-03"
PREVENTIVE_UNPLANNED = "MNT-02"


async def _check_preventive(db: AsyncSession, tenant_id: str, machine_id: str, code: str, start: datetime,
                            end: Optional[datetime]) -> None:
    """S3-4: PLN-03 (scheduled preventive) only for a stop covered by a PLN-03 planned stop of the
    calendar; otherwise it was not planned before the shift — it is MNT-02 (domain.md §4.1)."""
    if not code.startswith(PREVENTIVE):
        return
    info = (await machine_infos(db, tenant_id, [machine_id]))[machine_id]
    cal = await load_calendar(db, tenant_id, info.plant_id, info.tz)
    stop = end or datetime.now(timezone.utc)
    if stop <= start:
        stop = start + timedelta(seconds=1)
    for inst in cal.instances(start, stop):
        for b in inst.breaks:
            if b.reason_code.startswith(PREVENTIVE) and b.start < stop and start < b.end:
                return
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
        "message": f"{code} (preventiva programada) só vale para uma parada coberta por uma parada planejada "
                   f"{PREVENTIVE} do calendário. Preventiva não programada antes do turno é {PREVENTIVE_UNPLANNED}.",
        "suggested_reason_code": PREVENTIVE_UNPLANNED})


@downtime_router.patch("/{event_id}", response_model=DowntimeOut, dependencies=OPERATOR)
async def justify_downtime(event_id: str, body: DowntimePatch, user: CurrentUser, db: DB):
    if not is_uuid(event_id):
        raise HTTPException(status_code=404, detail="Parada não encontrada.")
    ev = (await db.execute(select(DowntimeEvent).where(DowntimeEvent.id == event_id,
                                                       DowntimeEvent.tenant_id == user.tenant_id)
                           .with_for_update())).scalar_one_or_none()
    if ev is None:
        raise HTTPException(status_code=404, detail="Parada não encontrada.")
    sent = body.model_fields_set
    if "reason_code" in sent and body.reason_code is not None:
        rc = await _reason(db, user.tenant_id, body.reason_code)
        await _check_preventive(db, user.tenant_id, ev.machine_id, rc.code, ev.start, ev.end)
        ev.reason_code = rc.code
        ev.planned = bool(rc.planned)
        ev.justified_by = user.id
        ev.justified_at = datetime.now(timezone.utc)
    if "comment" in sent:
        ev.comment = body.comment
    await _sync_failure(db, ev)
    await db.commit()
    await db.refresh(ev)
    await manager.broadcast(user.tenant_id, event_message(ev, "justify"))
    return _out(ev, await _names(db, user.tenant_id, [ev.machine_id]))


@downtime_router.post("/", response_model=DowntimeOut, status_code=status.HTTP_201_CREATED, dependencies=OPERATOR)
async def create_downtime(body: DowntimeCreate, user: CurrentUser, db: DB):
    """Manual stop (e.g. declared by the operator, or during a data gap)."""
    machine_id = await ensure_owned(db, Machine, body.machine_id, user.tenant_id, "machine_id")
    start, end = _aware(body.start), _aware(body.end)
    now = datetime.now(timezone.utc)
    if (start - now).total_seconds() > CLOCK_SKEW_S or (end and (end - now).total_seconds() > CLOCK_SKEW_S):
        raise _422("A parada não pode estar no futuro.")
    if end is not None and end <= start:
        raise _422("end deve ser após start.")
    rc = await _reason(db, user.tenant_id, body.reason_code)
    await _check_preventive(db, user.tenant_id, machine_id, rc.code, start, end)
    info = (await machine_infos(db, user.tenant_id, [machine_id]))[machine_id]
    cal = await load_calendar(db, user.tenant_id, info.plant_id, info.tz)
    inst = cal.instance_at(start)
    ev = DowntimeEvent(tenant_id=user.tenant_id, machine_id=machine_id, start=start, end=end,
                       duration_s=(end - start).total_seconds() if end else None, stop_start=start, source="manual",
                       reason_code=rc.code, planned=bool(rc.planned), comment=body.comment,
                       shift_id=inst.shift_id if inst else None,
                       productive_day=inst.productive_day if inst else start.astimezone(info.tz).date(),
                       justified_by=user.id, justified_at=now)
    db.add(ev)
    await db.flush()
    await _sync_failure(db, ev)
    await db.commit()
    await db.refresh(ev)
    await manager.broadcast(user.tenant_id, event_message(ev, "open" if end is None else "close"))
    return _out(ev, await _names(db, user.tenant_id, [machine_id]))


# ── OEE / Pareto / reliability ─────────────────────────────────────────────

Scope = Literal["machine", "line", "plant"]


@kpi_router.get("/oee", response_model=OeeOut)
async def get_oee(user: CurrentUser, db: DB, scope: Scope = "machine", id: str = Query(...),
                  start: Optional[datetime] = None, stop: Optional[datetime] = None,
                  group_by: Literal["none", "hour", "shift", "day", "shift_name"] = "none",
                  compare_to: Literal["none", "previous_period"] = "none"):
    """group_by=shift_name sums every instance of the same shift (S3-8); compare_to=previous_period adds the
    same-length period just before (`previous`) and the server-computed `delta` (Sprint 5, AI assistant)."""
    now = datetime.now(timezone.utc)
    sc = await resolve_scope(db, user.tenant_id, scope, id)
    s, e = parse_window(sc, start, stop, now, group_by)
    out = await oee_for(db, sc, s, e, group_by, now)
    if compare_to == "previous_period":
        ps = s - (e - s)
        prev = await oee_for(db, sc, ps, s, group_by, now)
        prev.pop("points", None)
        out["previous"] = prev
        out["delta"] = oee_delta(out, prev)
    return out


@kpi_router.get("/losses/pareto", response_model=list[ParetoRow])
async def get_pareto(user: CurrentUser, db: DB, scope: Scope = "machine", id: str = Query(...),
                     start: Optional[datetime] = None, stop: Optional[datetime] = None,
                     by: Literal["code", "category"] = "code"):
    now = datetime.now(timezone.utc)
    sc = await resolve_scope(db, user.tenant_id, scope, id)
    s, e = parse_window(sc, start, stop, now)
    return await pareto_for(db, sc, s, e, now, by)


@kpi_router.get("/reliability", response_model=ReliabilityOut)
async def get_reliability(user: CurrentUser, db: DB, scope: Literal["machine", "line"] = "machine",
                          id: str = Query(...), start: Optional[datetime] = None, stop: Optional[datetime] = None):
    now = datetime.now(timezone.utc)
    sc = await resolve_scope(db, user.tenant_id, scope, id)
    if start is None:
        start = now - timedelta(days=90)     # kpis.md §2.1: ≥ 3 months for a useful MTBF
    s, e = parse_window(sc, start, stop, now)
    return await reliability_for(db, sc, s, e, now)


# ── shift report ───────────────────────────────────────────────────────────

@kpi_router.get("/reports/shift", response_model=ShiftReportOut)
async def shift_report(user: CurrentUser, db: DB, line_id: str = Query(...), productive_day: date = Query(...),
                       shift_id: Optional[str] = None):
    now = datetime.now(timezone.utc)
    sc = await resolve_scope(db, user.tenant_id, "line", line_id)
    if shift_id is not None and not is_uuid(shift_id):
        raise HTTPException(status_code=404, detail="Turno não encontrado.")
    insts = instances_of_day(sc.cal, productive_day, shift_id)
    out = ShiftReportOut(line_id=sc.id, productive_day=productive_day, shift_id=shift_id,
                         shift_name=insts[0].name if len(insts) == 1 else None, period_start=None, period_end=None,
                         oee=None, pareto=[], downtime=[], production=[])
    if not insts:
        out.reason = "no_shift"
        return out
    ps, pe = min(i.start for i in insts), max(i.end for i in insts)
    out.period_start, out.period_end = ps, pe
    units = await load_units(db, sc, ps, pe, now)
    c = day_components(units, insts, now)
    c.calendar_s = max((min(pe, now) - ps).total_seconds(), 0.0)
    out.oee = OeeOut(**oee_payload(sc, c, ps, pe, "shift" if shift_id else "day"))
    out.pareto = [ParetoRow(**r) for r in kpi.pareto(c, await reason_meta(db, user.tenant_id))]
    evs = await query_downtime(db, user.tenant_id, sc.machine_ids, ps, pe)
    evs = [e for e in evs if any(i.start <= e.start < i.end for i in insts)]
    names = await _names(db, user.tenant_id, sc.machine_ids)
    out.downtime = [_out(e, names) for e in sorted(evs, key=lambda e: e.start)]
    infos = await machine_infos(db, user.tenant_id, sc.machine_ids)
    for mid in sc.machine_ids:
        if mid not in infos:
            continue
        md = await load_machine_data(db, infos[mid], sc.cal, ps, pe, now)
        mc = kpi.Components()
        for i in insts:
            mc.add(machine_components(md, i.start, i.end, now))
        r = kpi.oee(mc)
        out.production.append(MachineProduction(
            machine_id=mid, machine_name=infos[mid].name, good=mc.good, scrap=mc.scrap, total=mc.produced,
            availability=r["availability"], performance=r["performance"], quality=r["quality"], oee=r["oee"]))
    return out


# ── overview (raio-x) ──────────────────────────────────────────────────────

@kpi_router.get("/overview", response_model=OverviewOut)
async def overview(user: CurrentUser, db: DB, plant_id: Optional[str] = None):
    tid = user.tenant_id
    if plant_id is None:
        plant = (await db.execute(select(Plant).where(Plant.tenant_id == tid).order_by(Plant.created_at)
                                  .limit(1))).scalar_one_or_none()
        if plant is None:
            raise HTTPException(status_code=404, detail="Planta não encontrada.")
    else:
        if not is_uuid(plant_id):
            raise HTTPException(status_code=404, detail="Planta não encontrada.")
        plant = (await db.execute(select(Plant).where(Plant.id == plant_id, Plant.tenant_id == tid))).scalar_one_or_none()
        if plant is None:
            raise HTTPException(status_code=404, detail="Planta não encontrada.")
    now = datetime.now(timezone.utc)
    tz = uq.tzinfo(plant.timezone)
    cal = await load_calendar(db, tid, plant.id, tz)
    today = productive_today(cal, now)
    today_insts = [i for i in cal.day_instances(today) if i.start < now]
    current = cal.instance_at(now)
    meta = await reason_meta(db, tid)
    lines = (await db.execute(select(Line).where(Line.tenant_id == tid, Line.plant_id == plant.id)
                              .order_by(Line.name))).scalars().all()
    machines = (await db.execute(select(Machine).join(Line, Line.id == Machine.line_id).where(
        Machine.tenant_id == tid, Line.plant_id == plant.id))).scalars().all()
    by_line: dict[str, list[Machine]] = {}
    for m in machines:
        by_line.setdefault(m.line_id, []).append(m)
    infos = await machine_infos(db, tid, [m.id for m in machines])
    ws = min((i.start for i in today_insts), default=uq.local_midnight_utc(now, tz))
    we = max((i.end for i in today_insts), default=now)
    plant_c = kpi.Components()
    out_lines = []
    open_rows = (await db.execute(select(DowntimeEvent.machine_id, func.count()).where(
        DowntimeEvent.tenant_id == tid, DowntimeEvent.end.is_(None)).group_by(DowntimeEvent.machine_id))).all()
    open_by_m = dict(open_rows)
    unj_rows = (await db.execute(select(DowntimeEvent.machine_id, func.count()).where(
        DowntimeEvent.tenant_id == tid, DowntimeEvent.productive_day == today,
        or_(DowntimeEvent.reason_code.is_(None), DowntimeEvent.reason_code == UNJUSTIFIED))
        .group_by(DowntimeEvent.machine_id))).all()
    unj_by_m = dict(unj_rows)
    for ln in lines:
        sc = await resolve_scope(db, tid, "line", ln.id)
        units = await load_units(db, sc, ws, max(we, now), now)
        c = day_components(units, today_insts, now)
        plant_c.add(c)
        r = kpi.oee(c)
        cur_oee = None
        if current is not None:
            cc = day_components(units, [current], now)
            cur_oee = kpi.oee(cc)["oee"]
        worst = None
        for m in by_line.get(ln.id, []):
            md = await load_machine_data(db, infos[m.id], cal, ws, max(we, now), now)
            mc = kpi.Components()
            for i in today_insts:
                mc.add(machine_components(md, i.start, i.end, now))
            mo = kpi.oee(mc)["oee"]
            if mo is not None and (worst is None or mo < worst["oee"]):
                worst = {"machine_id": m.id, "machine_name": m.name, "oee": mo}
        mids = [m.id for m in by_line.get(ln.id, [])]
        out_lines.append(OverviewLine(
            line_id=ln.id, line_name=ln.name, output_machine_id=ln.output_machine_id, oee_today=r["oee"],
            oee_current_shift=cur_oee, current_shift_name=current.name if current else None,
            availability=r["availability"], performance=r["performance"], quality=r["quality"],
            good=c.good, scrap=c.scrap, cadence_per_hour=r["cadence_planned_per_hour"],
            data_coverage=r["data_coverage"],
            open_downtime_count=sum(open_by_m.get(m, 0) for m in mids),
            unjustified_count=sum(unj_by_m.get(m, 0) for m in mids), worst_machine=worst))
    top = kpi.pareto(plant_c, meta)
    top = [ParetoRow(**r) for r in top if r["duration_s"] > 0][:5]
    err_machines = [m for m in machines if m.status == MachineStatus.error]
    open_evs = {}
    if err_machines:
        for ev in (await db.execute(select(DowntimeEvent).where(
                DowntimeEvent.tenant_id == tid, DowntimeEvent.end.is_(None),
                DowntimeEvent.machine_id.in_([m.id for m in err_machines])))).scalars():
            open_evs[ev.machine_id] = ev
    in_error = []
    for m in err_machines:
        ev = open_evs.get(m.id)
        since = (ev.fault_since or ev.start) if ev else None
        in_error.append({"machine_id": m.id, "machine_name": m.name, "line_id": m.line_id,
                         "since": since.isoformat() if since else None,
                         "fault_code": ev.fault_code if ev else None,
                         "suggested_reason_code": ev.suggested_reason_code if ev else None,
                         "last_seen_at": m.last_seen_at.isoformat() if m.last_seen_at else None})
    near_end = []
    mid_names = {m.id: m.name for m in machines}
    if machines:
        rows = (await db.execute(select(Component, Position).join(Position, Position.id == Component.position_id).where(
            Component.tenant_id == tid, Component.removed_at.is_(None),
            Position.machine_id.in_([m.id for m in machines])))).all()
        comps = [c for c, _ in rows]
        usages = await uq.component_usages(db, tid, comps)
        for comp_row, pos in rows:
            life, measure = uq.component_life(comp_row, usages[comp_row.id])
            if life is not None and life >= 0.8:
                detail = await uq.component_usage_detail(db, tid, comp_row, tz, now)
                end_date = detail.get("projected_end_date")
                near_end.append({"component_id": comp_row.id, "position_id": pos.id, "position_name": pos.name,
                                 "machine_id": pos.machine_id, "machine_name": mid_names.get(pos.machine_id),
                                 "life_consumed_pct": life, "life_measure": measure,
                                 "projected_end_date": end_date.isoformat() if end_date else None})
        near_end.sort(key=lambda x: -x["life_consumed_pct"])
    from ...models import Gateway
    from ...services.gateways import effective_status
    gws = (await db.execute(select(Gateway).where(Gateway.tenant_id == tid, Gateway.plant_id == plant.id)
                            .order_by(Gateway.name))).scalars().all()
    gateways = [{"id": g.id, "name": g.name, "status": effective_status(g, now), "version": g.version,
                 "last_seen_at": g.last_seen_at.isoformat() if g.last_seen_at else None,
                 "config_version": g.config_version} for g in gws]
    return OverviewOut(plant_id=plant.id, plant_name=plant.name, productive_day=today, generated_at=now,
                       lines=out_lines, top_losses=top, machines_in_error=in_error, components_near_end=near_end,
                       gateways=gateways)
