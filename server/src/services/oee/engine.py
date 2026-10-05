"""
OEE engine read side: loads what a machine did in a window (calendar, downtime events, data spans,
micro-stop buckets, part counters, production runs) and turns it into `kpi.Components`.

Every query filters tenant_id. Resolution:
  * times (POT, PDOT, ADOT, sem dados) are exact intervals;
  * part counts, operating seconds and micro-stops come from hourly buckets: a bucket belongs to
    the window that contains its start (micro-stops: the start of the piece inside the hour),
    so adjacent windows never double count.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import (CalendarException, DowntimeDetectorState, DowntimeEvent, FailureRecord, Line, Machine,
                       MachineDataSpan, MachineProduct, MicroStopBucket, Plant, Position, ProductionRun,
                       ReasonCode, Shift, Tag, UsageBucket)
from ...models.tag import SemanticRole
from ..usage import queries as uq
from .calendar import Calendar, ExceptionDef, ShiftDef, ShiftInstance
from .intervals import Interval, clip, intersect, normalize, subtract, total_s
from .kpi import AO_EXCLUDED, Components, is_equipment

UNJUSTIFIED = "UNK-01"
DEFAULT_CALENDAR_NAME = "Calendário padrão 24 h"


# ── calendar ────────────────────────────────────────────────────────────────

async def load_calendar(db: AsyncSession, tenant_id: str, plant_id: str, tz: ZoneInfo) -> Calendar:
    """The plant's shifts + exceptions. A plant with no shift at all gets an implicit 24 h calendar
    (every day 00:00–24:00 local, `calendar_default`), so detection and OEE work before setup."""
    # active shifts + closed versions (still in effect for the days up to their valid_to — D11)
    shifts = (await db.execute(
        select(Shift).where(Shift.tenant_id == tenant_id, Shift.plant_id == plant_id,
                            or_(Shift.is_active.is_(True), Shift.valid_to.is_not(None)))
        .order_by(Shift.start_local)
    )).scalars().all()
    excs = (await db.execute(
        select(CalendarException).where(CalendarException.tenant_id == tenant_id,
                                        CalendarException.plant_id == plant_id)
    )).scalars().all()
    defs = [ShiftDef(s.id, s.name, s.start_local, s.end_local, tuple(s.weekdays or ()), tuple(s.breaks or ()),
                     s.valid_from, s.valid_to) for s in shifts]
    cal = Calendar(tz, defs, [ExceptionDef(e.date, e.kind, e.shift_id, e.start_local, e.end_local,
                                           e.reason_code, e.note) for e in excs])
    if not shifts:
        cal = Calendar(tz, [ShiftDef(None, DEFAULT_CALENDAR_NAME, "00:00", "00:00", tuple(range(7)))],
                       [e for es in cal.exceptions.values() for e in es], default=True)
    return cal


# ── machines ────────────────────────────────────────────────────────────────

@dataclass
class MachineInfo:
    id: str
    tenant_id: str
    name: str
    line_id: str
    plant_id: str
    tz: ZoneInfo
    status: str
    threshold_s: int
    ideal_cycle_time_s: Optional[float]
    fault_code_map: dict


async def machine_infos(db: AsyncSession, tenant_id: str, machine_ids: Optional[Iterable[str]] = None
                        ) -> dict[str, MachineInfo]:
    q = (select(Machine, Line.plant_id, Plant.timezone)
         .join(Line, Line.id == Machine.line_id).join(Plant, Plant.id == Line.plant_id)
         .where(Machine.tenant_id == tenant_id))
    if machine_ids is not None:
        ids = list(machine_ids)
        if not ids:
            return {}
        q = q.where(Machine.id.in_(ids))
    out = {}
    for m, plant_id, tzname in (await db.execute(q)).all():
        out[m.id] = MachineInfo(m.id, m.tenant_id, m.name, m.line_id, plant_id, uq.tzinfo(tzname),
                                m.status.value if hasattr(m.status, "value") else str(m.status),
                                int(m.micro_stop_threshold_s or 120), m.ideal_cycle_time_s,
                                dict(m.fault_code_map or {}))
    return out


async def run_source(db: AsyncSession, tenant_id: str, machine_id: str) -> tuple[str, str]:
    """Where the machine's Σ run_state = 1 lives: ('machine', id) when it has its own run_state tag,
    else the first position (by creation) with a run_state tag — e.g. the output conveyor whose
    run_state is on its motor position (Sprint 2 QA finding). Default ('machine', id)."""
    rows = (await db.execute(
        select(Tag.position_id, Position.created_at)
        .outerjoin(Position, Position.id == Tag.position_id)
        .where(Tag.tenant_id == tenant_id, Tag.machine_id == machine_id, Tag.is_active.is_(True),
               Tag.semantic_role == SemanticRole.run_state)
    )).all()
    if not rows or any(pid is None for pid, _ in rows):
        return "machine", machine_id
    pid = sorted(rows, key=lambda r: (r[1] or datetime.min.replace(tzinfo=timezone.utc), r[0]))[0][0]
    return "position", pid


async def operating_seconds(db: AsyncSession, tenant_id: str, machine_ids: Iterable[str],
                            start: datetime, stop: datetime) -> float:
    """Σ run_state = 1 (C23) from the hourly usage buckets. Hours fully inside [start, stop) count
    whole (exact for hour-aligned windows such as shifts); a partially covered hour at either edge
    counts pro rata (its operation is assumed uniform inside the hour)."""
    tot = 0.0
    first = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for mid in machine_ids:
        scope, eid = await run_source(db, tenant_id, mid)
        rows = (await db.execute(
            select(UsageBucket.bucket_start, UsageBucket.value).where(
                UsageBucket.tenant_id == tenant_id, UsageBucket.scope == scope, UsageBucket.entity_id == eid,
                UsageBucket.measure == "operating_seconds", UsageBucket.bucket_start >= first,
                UsageBucket.bucket_start < stop)
        )).all()
        now = datetime.now(timezone.utc)
        for b, v in rows:
            bucket_end = min(b + timedelta(hours=1), max(now, b + timedelta(seconds=1)))  # current hour: so far
            ov = (min(bucket_end, stop) - max(b, start)).total_seconds()
            span = (bucket_end - b).total_seconds()
            tot += float(v) * max(0.0, min(ov / span, 1.0))
    return tot


# ── reason codes ───────────────────────────────────────────────────────────

async def reason_meta(db: AsyncSession, tenant_id: str) -> dict[str, tuple[str, str, bool]]:
    rows = (await db.execute(
        select(ReasonCode).where(or_(ReasonCode.tenant_id.is_(None), ReasonCode.tenant_id == tenant_id))
        .order_by(ReasonCode.tenant_id.is_(None).desc())     # tenant codes override defaults
    )).scalars().all()
    return {r.code: (r.category, r.label, bool(r.planned)) for r in rows}


def effective_reason(ev) -> str:
    return ev.reason_code or ev.suggested_reason_code or UNJUSTIFIED


# ── machine data in a window ───────────────────────────────────────────────

@dataclass
class EventLite:
    id: str
    start: datetime
    end: Optional[datetime]
    reason: str
    planned: bool


@dataclass
class ProductPri:
    pri: dict[str, Optional[float]] = field(default_factory=dict)      # product_id -> s per part
    default_product: Optional[str] = None
    fallback_pri: Optional[float] = None                                # machine.ideal_cycle_time_s

    def lookup(self, product_id: Optional[str]) -> Optional[float]:
        if product_id is None:
            return self.fallback_pri
        return self.pri.get(product_id)


@dataclass
class MachineData:
    info: MachineInfo
    cal: Calendar
    events: list[EventLite]
    spans: list[Interval]
    micro: list[tuple[datetime, int, float, int]]                       # (piece_start, count, loss, short)
    production: dict[datetime, dict[str, float]]                        # bucket -> good/scrap/total
    runs: list[tuple[str, datetime, Optional[datetime]]]
    pri: ProductPri


async def product_pri(db: AsyncSession, tenant_id: str, info: MachineInfo) -> ProductPri:
    rows = (await db.execute(
        select(MachineProduct).where(MachineProduct.tenant_id == tenant_id, MachineProduct.machine_id == info.id)
    )).scalars().all()
    p = ProductPri(fallback_pri=info.ideal_cycle_time_s)
    for r in rows:
        p.pri[r.product_id] = r.ideal_cycle_time_s / max(1, r.parts_per_cycle or 1)
        if r.is_default:
            p.default_product = r.product_id
    if p.default_product is not None:
        p.fallback_pri = p.pri[p.default_product]
    return p


async def load_machine_data(db: AsyncSession, info: MachineInfo, cal: Calendar, start: datetime,
                            stop: datetime, now: datetime) -> MachineData:
    tid, mid = info.tenant_id, info.id
    evs = (await db.execute(
        select(DowntimeEvent).where(
            DowntimeEvent.tenant_id == tid, DowntimeEvent.machine_id == mid, DowntimeEvent.start < stop,
            or_(DowntimeEvent.end.is_(None), DowntimeEvent.end > start))
        .order_by(DowntimeEvent.start)
    )).scalars().all()
    events = [EventLite(e.id, e.start, e.end, effective_reason(e), bool(e.planned)) for e in evs]
    span_rows = (await db.execute(
        select(MachineDataSpan.start, MachineDataSpan.end).where(
            MachineDataSpan.tenant_id == tid, MachineDataSpan.machine_id == mid,
            MachineDataSpan.start < stop, MachineDataSpan.end > start)
    )).all()
    spans = [(a, b) for a, b in span_rows]
    det = (await db.execute(select(DowntimeDetectorState).where(
        DowntimeDetectorState.tenant_id == tid, DowntimeDetectorState.machine_id == mid))).scalar_one_or_none()
    if det is not None and det.covered_until is not None:
        last = (det.state or {}).get("last_data")
        if last:
            live_until = min(now, datetime.fromisoformat(last) + timedelta(seconds=settings.machine_offline_timeout_s))
            if live_until > det.covered_until:
                spans.append((det.covered_until, live_until))
    spans = normalize(spans)
    micro = [(r.piece_start if r.piece_start else r.bucket_start, r.count, r.loss_s, r.short_faults)
             for r in (await db.execute(select(MicroStopBucket).where(
                 MicroStopBucket.tenant_id == tid, MicroStopBucket.machine_id == mid,
                 MicroStopBucket.bucket_start >= start - timedelta(hours=1), MicroStopBucket.bucket_start < stop)
             )).scalars().all()]
    has_good = await uq.machine_has_good_tag(db, tid, mid)
    prod_rows = (await db.execute(
        select(UsageBucket.bucket_start, UsageBucket.measure, UsageBucket.value).where(
            UsageBucket.tenant_id == tid, UsageBucket.scope == "machine", UsageBucket.entity_id == mid,
            UsageBucket.measure.in_(("good", "scrap", "total")),
            UsageBucket.bucket_start >= start - timedelta(hours=1), UsageBucket.bucket_start < stop)
    )).all()
    raw: dict[datetime, dict[str, float]] = defaultdict(dict)
    for b, m, v in prod_rows:
        raw[b][m] = float(v)
    production = {b: uq.production(v, has_good) for b, v in raw.items()}
    run_rows = (await db.execute(
        select(ProductionRun.product_id, ProductionRun.started_at, ProductionRun.ended_at).where(
            ProductionRun.tenant_id == tid, ProductionRun.machine_id == mid, ProductionRun.started_at < stop,
            or_(ProductionRun.ended_at.is_(None), ProductionRun.ended_at > start - timedelta(hours=1)))
    )).all()
    runs = [(p, a, b) for p, a, b in run_rows]
    return MachineData(info, cal, events, spans, micro, production, runs, await product_pri(db, tid, info))


# ── components ──────────────────────────────────────────────────────────────

def _break_by_reason(insts: list[ShiftInstance], ws: datetime, we: datetime) -> dict[str, list[Interval]]:
    out: dict[str, list[Interval]] = defaultdict(list)
    for i in insts:
        for b in i.breaks:
            out[b.reason_code].append((b.start, b.end))
    return {k: clip(normalize(v), ws, we) for k, v in out.items()}


def components(md: MachineData, ws: datetime, we: datetime, now: datetime,
               pri: Optional[ProductPri] = None, calendar_window: Optional[Interval] = None) -> Components:
    """kpi.Components of machine data `md` for the window [ws, we) (clipped to `now`)."""
    c = Components()
    end = min(we, now)
    cw = calendar_window or (ws, we)
    c.calendar_s = max((min(cw[1], now) - cw[0]).total_seconds(), 0.0)
    if end <= ws:
        return c
    insts = md.cal.instances(ws, end)
    pot = clip(normalize(i.interval for i in insts), ws, end)
    brk_by = _break_by_reason(insts, ws, end)
    brk = intersect(normalize(iv for v in brk_by.values() for iv in v), pot)
    sched = subtract(pot, brk)
    c.pot_s = total_s(pot)

    planned_ivs, unplanned = [], []
    for e in md.events:
        iv = clip([(e.start, e.end or now)], ws, end)
        if not iv:
            continue
        (planned_ivs if e.planned else unplanned).append((e, iv[0]))
    planned_u = intersect(normalize(iv for _, iv in planned_ivs), sched)
    productive = subtract(sched, planned_u)
    unplanned_pieces = []
    for e, iv in unplanned:
        piece = intersect([iv], productive)
        secs = total_s(piece)
        if secs <= 0:
            continue
        unplanned_pieces += piece
        cur = c.losses.setdefault(e.reason, [0.0, 0])
        cur[0] += secs
        cur[1] += 1
    unplanned_u = normalize(unplanned_pieces)
    c.adot_s = total_s(unplanned_u)
    c.pdot_s = total_s(brk) + total_s(planned_u)
    c.nodata_s = total_s(subtract(subtract(productive, unplanned_u), md.spans))
    declared = normalize([iv for _, iv in planned_ivs + unplanned])
    c.covered_s = total_s(intersect(pot, normalize(list(md.spans) + declared)))

    # §2.6 Ao parts
    c.ao_excluded_s = sum(total_s(intersect(v, pot)) for k, v in brk_by.items() if k in AO_EXCLUDED)
    for e, iv in planned_ivs:
        if e.reason in AO_EXCLUDED:
            c.ao_excluded_s += total_s(intersect([iv], sched))
    eq = [iv for e, iv in planned_ivs + unplanned if is_equipment(e.reason)]
    c.ao_equipment_s = total_s(intersect(normalize(eq), sched))

    for piece_start, n, loss, short in md.micro:
        if ws <= piece_start < we:
            c.micro_count += n
            c.micro_s += loss
            c.short_faults += short

    _production(c, md, ws, end, now, pot, pri or md.pri)
    return c


def _production(c: Components, md: MachineData, ws: datetime, end: datetime, now: datetime,
                pot: list[Interval], pri: ProductPri) -> None:
    """Parts of the hourly buckets (D7) that fall in [ws, end): only the part inside the shift
    instances (POT) enters PQ/GQ (S3-7) — the rest is `produced_outside_schedule`. A bucket only partly
    inside the window or the schedule counts pro rata of the overlap (parts assumed uniform within the
    hour; the current hour by the time elapsed) and flags `counts_prorated`; hour-aligned windows and
    shifts are exact."""
    for b, vals in md.production.items():
        hour_end = b + timedelta(hours=1)
        if hour_end <= ws or b >= end:
            continue
        total, good, scrap = vals["total"], vals["good"], vals["scrap"]
        if total <= 0 and good <= 0 and scrap <= 0:
            continue
        eff_end = min(hour_end, max(now, b + timedelta(seconds=1)))
        span = (eff_end - b).total_seconds()
        in_window = max((min(eff_end, end) - max(b, ws)).total_seconds(), 0.0) / span
        in_pot = total_s(intersect([(b, eff_end)], pot)) / span
        in_window, in_pot = min(in_window, 1.0), min(in_pot, in_window, 1.0)
        if in_window <= 0:
            continue
        outside = in_window - in_pot
        if outside > 1e-9:
            c.out_produced += total * outside
            c.out_good += good * outside
            c.out_scrap += scrap * outside
        if 1e-9 < in_pot < 1.0 - 1e-9:
            c.counts_prorated = True
        if in_pot <= 1e-9:
            continue
        total, good, scrap = total * in_pot, good * in_pot, scrap * in_pot
        c.produced += total
        c.good += good
        c.scrap += scrap
        shares: dict[Optional[str], float] = defaultdict(float)
        covered = 0.0
        for pid, a, z in md.runs:
            ov = (min(z or hour_end, hour_end) - max(a, b)).total_seconds()
            if ov > 0:
                shares[pid] += ov / 3600.0
                covered += ov / 3600.0
        if covered < 1.0 - 1e-9:
            shares[pri.default_product] += 1.0 - covered
            c.product_unknown = True
        tot_share = sum(shares.values())
        for pid, share in shares.items():
            f = share / tot_share
            p = pri.lookup(pid)
            if p is None:
                c.pri_missing = True
                continue
            c.ideal_produced_s += p * total * f
            c.ideal_good_s += p * good * f


# ── windows for group_by ─────────────────────────────────────────────────────

@dataclass
class Window:
    start: datetime
    end: datetime
    label: str
    instances: Optional[list[ShiftInstance]] = None     # shift/day: sum over these instances
    bound: Optional[Interval] = None                     # query window the instances are clipped to


def windows(cal: Calendar, tz: ZoneInfo, start: datetime, stop: datetime, group_by: str) -> list[Window]:
    out: list[Window] = []
    if group_by == "hour":
        cur = start.astimezone(tz).replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        while cur < stop:
            nxt = cur + timedelta(hours=1)
            out.append(Window(max(cur, start), min(nxt, stop), cur.astimezone(tz).strftime("%Y-%m-%d %H:00")))
            cur = nxt
    elif group_by == "shift":
        for i in cal.instances(start, stop):
            out.append(Window(max(i.start, start), min(i.end, stop),
                              f"{i.name} {i.productive_day.strftime('%d/%m')}", [i], (start, stop)))
    elif group_by == "day":
        d = start.astimezone(tz).date()
        last = (stop - timedelta(microseconds=1)).astimezone(tz).date()
        while d <= last:
            day_start = datetime.combine(d, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
            day_end = datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
            insts = [i for i in cal.day_instances(d) if i.end > start and i.start < stop]
            out.append(Window(max(day_start, start), min(day_end, stop), d.isoformat(), insts, (start, stop)))
            d += timedelta(days=1)
    return out


def window_components(md: MachineData, w: Window, now: datetime, pri: Optional[ProductPri] = None) -> Components:
    """Components of a group_by window. Shift/day windows sum their shift instances (a shift that
    crosses midnight belongs whole to the productive day it starts on, C19); calendar time (TEEP)
    is the window itself."""
    if w.instances is None:
        return components(md, w.start, w.end, now, pri)
    qs, qe = w.bound or (w.start, w.end)
    c = Components()
    for i in w.instances:
        c.add(components(md, max(i.start, qs), min(i.end, qe), now, pri))
    c.calendar_s = max((min(w.end, now) - w.start).total_seconds(), 0.0)
    return c


# ── reliability inputs ─────────────────────────────────────────────────────

async def failures(db: AsyncSession, tenant_id: str, machine_ids: list[str], start: datetime, stop: datetime
                   ) -> list[tuple[str, Optional[float]]]:
    if not machine_ids:
        return []
    rows = (await db.execute(
        select(FailureRecord.severity, FailureRecord.failed_at, FailureRecord.restored_at).where(
            FailureRecord.tenant_id == tenant_id, FailureRecord.machine_id.in_(machine_ids),
            FailureRecord.failed_at >= start, FailureRecord.failed_at < stop)
    )).all()
    return [(sev, (rest - failed).total_seconds() if rest else None) for sev, failed, rest in rows]


async def short_faults(db: AsyncSession, tenant_id: str, machine_ids: list[str], start: datetime, stop: datetime) -> int:
    if not machine_ids:
        return 0
    rows = (await db.execute(
        select(MicroStopBucket.short_faults, MicroStopBucket.piece_start, MicroStopBucket.bucket_start).where(
            MicroStopBucket.tenant_id == tenant_id, MicroStopBucket.machine_id.in_(machine_ids),
            MicroStopBucket.bucket_start >= start - timedelta(hours=1), MicroStopBucket.bucket_start < stop)
    )).all()
    return int(sum(n for n, ps, b in rows if start <= (ps or b) < stop))


def day_of(cal: Calendar, ts: datetime) -> date:
    inst = cal.instance_at(ts)
    return inst.productive_day if inst else ts.astimezone(cal.tz).date()
