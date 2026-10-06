"""
Downtime detector (domain.md §4.3): turns the machine signals stored in InfluxDB into
DowntimeEvents, FailureRecord drafts, micro-stop accumulators and data-coverage spans.

Per machine, a cursor (`downtime_detector_states`) holds the checkpoint before which everything is
final and the signal state at that instant (inside a long stop: the stop's start, so a stopped
machine is not re-read from the start of its stop on every tick). Each tick re-reads the readings
after the checkpoint, runs the pure detection (detection.py) and reconciles everything after it.

Late / out-of-order readings (Sprint 3 QA): the MQTT handler processes messages concurrently and
gateways flush buffers after a reconnect, so readings reach InfluxDB out of order.
  * the checkpoint only passes readings that were already visible `downtime_settle_s` ago (the
    tick records, in wall time, the latest reading it saw);
  * a reading written more than `downtime_settle_s` after its timestamp is noted by the MQTT
    handler (`note_late` → `late_from`); the next tick rewinds to the latest snapshot
    (`downtime_detector_snapshots`) before it and re-detects — events are upserted by start, stale
    automatic ones removed, micro-stop buckets restored from the snapshot, spans re-derived;
  * micro-stops after the checkpoint are provisional: undone and recounted on every tick.

Reconciliation:
  * stops ≥ micro_stop_threshold_s → one DowntimeEvent per shift instance (upsert by
    (machine_id, start); operator fields — reason_code, comment, planned — are never overwritten).
    Open while the machine is still stopped (end NULL);
  * `EQP-*` suggestion (fault_state ≠ 0 during the stop) → FailureRecord draft (failed_at = moment
    fault_state went non-zero; restored_at = end of the stop);
  * final stops < threshold → micro_stop_buckets (count, loss, short_faults), exactly once;
  * data spans → machine_data_spans (coverage), re-derived after the checkpoint;
  * events whose shift ended without a reason (and no suggestion) → UNK-01 (regra 4).
Every open / close / change of an event is broadcast as {type: 'downtime', ...}.

Signals: run_state of the machine (or, when the machine has none, of its first position with one),
fault_state tags of the machine, and — without run_state — a cycle source (machine cycle counter, else
part_counter_total, else part_counter_good) for the cycle-gap rule.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

import structlog
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import (DowntimeDetectorSnapshot, DowntimeDetectorState, DowntimeEvent, FailureRecord, MachineDataSpan, MicroStopBucket,
                       Position, Tag)
from ...models.tag import SemanticRole
from ..influx import influx
from ..metrics import DOWNTIME_EVENTS
from ..validation import ensure_uuid
from ..usage.core import counter_step, CounterTrack
from .calendar import Calendar
from .detection import (Params, Reading, SignalState, Stop, detect, micro_pieces, restoration, segments,
                        short_fault_piece, short_offtime_fault,
                        suggested_reason)
from .engine import MachineInfo, UNJUSTIFIED, load_calendar, machine_infos, product_pri

log = structlog.get_logger()

Fetch = Callable[[str, list[str], datetime, datetime], Awaitable[list[tuple[str, datetime, float]]]]


# ── signal tags ─────────────────────────────────────────────────────────────

@dataclass
class SignalTags:
    run: Optional[str] = None
    faults: list[str] = field(default_factory=list)
    cycle: Optional[str] = None
    cycle_counter_max: Optional[int] = None

    @property
    def all(self) -> list[str]:
        return [t for t in [self.run, *self.faults, self.cycle] if t]


async def signal_tags(db: AsyncSession, tenant_id: str, machine_id: str) -> SignalTags:
    rows = (await db.execute(
        select(Tag.id, Tag.semantic_role, Tag.position_id, Tag.counter_max, Position.created_at)
        .outerjoin(Position, Position.id == Tag.position_id)
        .where(Tag.tenant_id == tenant_id, Tag.machine_id == machine_id, Tag.is_active.is_(True))
        .order_by(Tag.created_at, Tag.id)
    )).all()
    st = SignalTags()
    pos_runs = []
    cycles: dict[str, tuple[str, Optional[int]]] = {}
    for tid, role, pid, cmax, pcreated in rows:
        if role == SemanticRole.run_state:
            if pid is None and st.run is None:
                st.run = tid
            elif pid is not None:
                pos_runs.append((pcreated, tid))
        elif role == SemanticRole.fault_state and pid is None:
            st.faults.append(tid)
        elif pid is None and role in (SemanticRole.cycle_counter, SemanticRole.cycle_pulse,
                                      SemanticRole.part_counter_total, SemanticRole.part_counter_good):
            cycles.setdefault(role.value, (tid, cmax))
    if st.run is None and pos_runs:
        st.run = sorted(pos_runs, key=lambda r: (r[0] or datetime.min.replace(tzinfo=timezone.utc), r[1]))[0][1]
    if st.run is None:
        for role in ("cycle_counter", "cycle_pulse", "part_counter_total", "part_counter_good"):
            if role in cycles:
                st.cycle, st.cycle_counter_max = cycles[role]
                break
    return st


# ── Influx source ──────────────────────────────────────────────────────────

async def influx_fetch(tenant_id: str, tag_ids: list[str], after: datetime, stop: datetime
                       ) -> list[tuple[str, datetime, float]]:
    """Readings of the tags with after < ts ≤ stop, sorted by ts."""
    tenant_id = ensure_uuid(tenant_id, "tenant_id")
    ids = [ensure_uuid(t, "tag_id") for t in tag_ids]
    if not ids:
        return []
    s = ", ".join(f'"{t}"' for t in ids)
    flux = f"""
    from(bucket: "{settings.influx_bucket}")
      |> range(start: {after.astimezone(timezone.utc).isoformat()}, stop: {(stop + timedelta(microseconds=1)).astimezone(timezone.utc).isoformat()})
      |> filter(fn: (r) => r["_measurement"] == "sensors")
      |> filter(fn: (r) => r["tenant_id"] == "{tenant_id}")
      |> filter(fn: (r) => contains(value: r["tag_id"], set: [{s}]))
      |> filter(fn: (r) => r["_field"] == "value")
      |> keep(columns: ["_time", "_value", "tag_id"])
    """
    out = []
    async for rec in await influx._client.query_api().query_stream(flux, org=settings.influx_org):
        ts = rec.get_time()
        if ts > after:
            out.append((rec.values.get("tag_id"), ts, float(rec.get_value())))
    out.sort(key=lambda r: (r[1], r[0]))
    return out


# ── reconciliation ─────────────────────────────────────────────────────────

def event_message(ev: DowntimeEvent, action: str) -> dict:
    return {"type": "downtime", "action": action, "event": {
        "id": ev.id, "machine_id": ev.machine_id,
        "start": ev.start.isoformat(), "end": ev.end.isoformat() if ev.end else None,
        "duration_s": ev.duration_s, "source": ev.source,
        "reason_code": ev.reason_code, "suggested_reason_code": ev.suggested_reason_code,
        "planned": bool(ev.planned), "shift_id": ev.shift_id,
        "productive_day": ev.productive_day.isoformat() if ev.productive_day else None,
    }}


@dataclass
class TickResult:
    messages: list = field(default_factory=list)
    events_touched: int = 0
    events_removed: int = 0
    micro_stops: int = 0
    readings: int = 0
    rewound: bool = False
    failures_kept: set = field(default_factory=set)


_NEVER = datetime.min.replace(tzinfo=timezone.utc)
# ISO 14224 detection method of the drafts the detector creates (marks them as "stop of production" failures)
AUTO_DETECTION = "production_interference"
# Micro-stop pieces still pending at a checkpoint start after `checkpoint - threshold` (≤ 300 s):
# the snapshot keeps the buckets from one hour before it.
_MICRO_MARGIN = timedelta(hours=1)


def _iso(d: Optional[datetime]) -> Optional[str]:
    return d.isoformat() if d else None


def _dt(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s) if s else None


def _hour(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _params(info: MachineInfo, mode: str, ideal: Optional[float]) -> Params:
    return Params(offline_timeout_s=settings.machine_offline_timeout_s, debounce_s=settings.run_state_debounce_s,
                  merge_gap_s=settings.downtime_merge_gap_s, finalize_delay_s=settings.downtime_finalize_delay_s,
                  mode=mode, ideal_cycle_s=ideal)


def _to_readings(rows, tags: SignalTags, counter: Optional[dict]) -> tuple[list[Reading], list[tuple[datetime, Optional[dict]]]]:
    """Influx rows → detection readings. Counter readings become `cycle` (delta > 0) or `data`
    via the same delta rules as usage (§3.3). Returns the readings and the counter track after each."""
    out: list[Reading] = []
    tracks: list[tuple[datetime, Optional[dict]]] = []
    track = None if not counter else CounterTrack(int(counter["v"]), datetime.fromisoformat(counter["ts"]))
    for tag_id, ts, v in rows:
        if tag_id == tags.run:
            out.append(Reading(ts, "run", v))
        elif tag_id in tags.faults:
            out.append(Reading(ts, "fault", v))
        elif tag_id == tags.cycle:
            res = counter_step(track, v, ts, tags.cycle_counter_max, None)
            if not res.ignored:
                track = res.track
            out.append(Reading(ts, "cycle" if res.delta > 0 else "data", float(res.delta)))
            tracks.append((ts, {"v": track.last_value, "ts": track.last_ts.isoformat()} if track else None))
    return out, tracks


def _operator_touched(ev: DowntimeEvent) -> bool:
    return bool(ev.justified_by or ev.justified_at or ev.comment or ev.planned
                or (ev.reason_code and ev.reason_code != UNJUSTIFIED))


async def _upsert_events(db: AsyncSession, info: MachineInfo, cal: Calendar, stop: Stop, now: datetime,
                         res: TickResult) -> set[str]:
    sugg = suggested_reason(stop.fault_code, info.fault_code_map)
    segs = segments(stop, cal, now)
    restored = restoration(stop, cal)
    if not segs:
        # Sprint 5 (kpis.md S5-1): an equipment fault that starts during a planned break or off-shift has no
        # DowntimeEvent yet (no production time lost), but the FailureRecord (and so the emergency call) exists
        # right away; the event of the productive part, if any, is linked to it when production time resumes.
        if (sugg and sugg.startswith("EQP-") and stop.fault_since is not None
                and not short_offtime_fault(stop, cal, info.threshold_s, now)):   # S5-1b: short → no failure
            fr = await ensure_failure(db, [], failed_at=stop.fault_since, restored_at=restored,
                                      tenant_id=info.tenant_id, machine_id=info.id)
            res.failures_kept.add(fr.id)
        return set()
    existing = {e.start: e for e in (await db.execute(
        select(DowntimeEvent).where(DowntimeEvent.tenant_id == info.tenant_id, DowntimeEvent.machine_id == info.id,
                                    DowntimeEvent.source != "manual",
                                    DowntimeEvent.start.in_([s.start for s in segs]))
    )).scalars().all()}
    rows: list[DowntimeEvent] = []
    for sg in segs:
        ev = existing.get(sg.start)
        action = None
        if ev is None:
            ev = DowntimeEvent(tenant_id=info.tenant_id, machine_id=info.id, start=sg.start, source=stop.source,
                               planned=False)
            db.add(ev)
            action = "open" if sg.end is None else "close"
            DOWNTIME_EVENTS.labels("stop").inc()
        elif (ev.end is None) != (sg.end is None) or (ev.end and sg.end and ev.end != sg.end):
            action = "close" if sg.end is not None else "update"
        elif ev.suggested_reason_code != sugg or ev.source != stop.source:
            action = "update"
        ev.end = sg.end
        ev.duration_s = (sg.end - sg.start).total_seconds() if sg.end else None
        ev.stop_start = stop.start
        ev.source = stop.source
        ev.suggested_reason_code = sugg
        ev.fault_code = stop.fault_code
        ev.fault_since = stop.fault_since
        ev.shift_id = sg.shift_id
        ev.productive_day = sg.productive_day
        rows.append(ev)
        if action:
            res.events_touched += 1
            res.messages.append(("event", ev, action))
    await db.flush()
    eff = rows[0].reason_code or sugg
    # S5-1b: a short fault that began and cleared during the break before this stop reached production time is
    # not a failure (an operator-set EQP reason still is: the operator says the equipment failed)
    auto_short = rows[0].reason_code in (None, UNJUSTIFIED) and short_offtime_fault(stop, cal, info.threshold_s, now) \
        and stop.fault_until is not None
    if eff and eff.startswith("EQP-") and not auto_short:
        fr = await ensure_failure(db, rows, failed_at=stop.fault_since or stop.start, restored_at=restored)
        res.failures_kept.add(fr.id)
    return {e.id for e in rows}


async def _drop_stale_events(db: AsyncSession, info: MachineInfo, after: datetime, now: datetime,
                             kept: set[str], res: TickResult, resumed: Optional[datetime] = None) -> None:
    """Automatic events this pass re-derives but no longer produces (readings that arrived late or
    out of order changed the picture) are removed — unless an operator already worked on them (kept,
    logged). Their FailureRecord drafts go with them.

    Re-derived here: events of stops that began after the checkpoint, and — when the checkpoint sits
    inside a stop (`resumed` = its `down_since`) — every segment of that stop, including those
    created while it was still open (e.g. the in-shift piece of an off-shift stop whose restart
    reading landed late, before the shift start: the stop no longer reaches the shift)."""
    scope = [DowntimeEvent.stop_start >= after, DowntimeEvent.stop_start.is_(None) & (DowntimeEvent.start >= after)]
    if resumed is not None:
        scope.append(DowntimeEvent.stop_start == resumed)
    q = select(DowntimeEvent).where(
        DowntimeEvent.tenant_id == info.tenant_id, DowntimeEvent.machine_id == info.id,
        DowntimeEvent.source != "manual", DowntimeEvent.start < now, or_(*scope))
    if kept:
        q = q.where(DowntimeEvent.id.not_in(kept))
    for ev in (await db.execute(q)).scalars().all():
        if _operator_touched(ev):
            log.warning("downtime.detector.stale_event_kept", machine_id=info.id[:8], event_id=ev.id[:8],
                        start=ev.start.isoformat())
            continue
        if ev.failure_id:
            others = (await db.execute(select(DowntimeEvent.id).where(
                DowntimeEvent.failure_id == ev.failure_id, DowntimeEvent.id != ev.id).limit(1))).first()
            fr = None if others else await db.get(FailureRecord, ev.failure_id)
            if fr is not None and fr.status == "draft":
                ev.failure_id = None
                await db.flush()
                await db.delete(fr)
        res.messages.append(("raw", event_message(ev, "delete"), None))
        res.events_removed += 1
        await db.delete(ev)
    await db.flush()


async def ensure_failure(db: AsyncSession, events: list[DowntimeEvent], failed_at: datetime,
                         restored_at: Optional[datetime], tenant_id: Optional[str] = None,
                         machine_id: Optional[str] = None) -> FailureRecord:
    """FailureRecord draft of a (possibly split) EQP-* stop; one per stop (domain.md §5.5).

    `events` may be empty (Sprint 5, S5-1): a fault that began during a planned break / off-shift gets its
    draft before any DowntimeEvent exists (`tenant_id`/`machine_id` given). When the productive part of the
    stop later produces an event, the event-less draft of the same fault (same machine, same failed_at) is
    found and linked to it instead of creating a second one."""
    first = events[0] if events else None
    tid = first.tenant_id if first is not None else tenant_id
    mid = first.machine_id if first is not None else machine_id
    fid = next((e.failure_id for e in events if e.failure_id), None)
    fr = await db.get(FailureRecord, fid) if fid else None
    if fr is None:
        fr = (await db.execute(select(FailureRecord).where(
            FailureRecord.tenant_id == tid, FailureRecord.machine_id == mid, FailureRecord.failed_at == failed_at,
            FailureRecord.downtime_event_id.is_(None), FailureRecord.detection_method == AUTO_DETECTION)
            .limit(1))).scalar_one_or_none()
        if fr is not None:
            fr.restored_at = restored_at
    if fr is None:
        fr = FailureRecord(tenant_id=tid, machine_id=mid, downtime_event_id=first.id if first is not None else None,
                           failed_at=failed_at, restored_at=restored_at, severity="critical", status="draft",
                           detection_method=AUTO_DETECTION)
        db.add(fr)
        await db.flush()
    else:
        was_restored = fr.restored_at
        fr.failed_at = failed_at
        fr.restored_at = restored_at
        if restored_at is not None and was_restored != restored_at:
            # machine back in production: integrations (CMMS/ERP) learn the restoration (failure.updated)
            from .. import clock, webhooks
            await webhooks.enqueue(db, fr.tenant_id, "failure.updated", {
                "id": fr.id, "machine_id": fr.machine_id, "failed_at": clock.iso(fr.failed_at),
                "restored_at": clock.iso(restored_at), "severity": fr.severity, "status": fr.status,
                "downtime_event_id": fr.downtime_event_id, "work_order_id": fr.work_order_id})
    if first is not None and fr.downtime_event_id is None:
        fr.downtime_event_id = first.id
        if fr.work_order_id:      # emergency WO opened during the break: now it also points at the stop
            from ...models import WorkOrder
            wo = await db.get(WorkOrder, fr.work_order_id)
            if wo is not None and wo.downtime_event_id is None:
                wo.downtime_event_id = first.id
    for e in events:
        e.failure_id = fr.id
    return fr


async def _drop_stale_failures(db: AsyncSession, info: MachineInfo, floor: datetime, kept: set[str]) -> None:
    """Event-less automatic drafts (S5-1) whose fault this pass no longer re-derives (late data changed the
    picture) are removed — unless someone already acted on them (WO / emergency call)."""
    q = select(FailureRecord).where(
        FailureRecord.tenant_id == info.tenant_id, FailureRecord.machine_id == info.id,
        FailureRecord.downtime_event_id.is_(None), FailureRecord.detection_method == AUTO_DETECTION,
        FailureRecord.status == "draft", FailureRecord.work_order_id.is_(None),
        FailureRecord.emergency_notified_at.is_(None), FailureRecord.failed_at >= floor)
    if kept:
        q = q.where(FailureRecord.id.not_in(kept))
    for fr in (await db.execute(q)).scalars().all():
        await db.delete(fr)
    await db.flush()


# micro-stop pieces: {"b": bucket_start, "slot", "shift_id", "day", "ps": piece_start, "n", "loss", "sf"}

async def _bump_micro(db: AsyncSession, info: MachineInfo, piece: dict, sign: int) -> None:
    n, loss, sf = sign * int(piece["n"]), sign * float(piece["loss"]), sign * int(piece["sf"])
    stmt = pg_insert(MicroStopBucket).values(
        machine_id=info.id, bucket_start=_dt(piece["b"]), shift_slot=piece["slot"], tenant_id=info.tenant_id,
        shift_id=piece.get("shift_id"), productive_day=date.fromisoformat(piece["day"]) if piece.get("day") else None,
        piece_start=_dt(piece.get("ps")), count=n, loss_s=loss, short_faults=sf)
    stmt = stmt.on_conflict_do_update(
        index_elements=["machine_id", "bucket_start", "shift_slot"],
        set_={"count": MicroStopBucket.count + stmt.excluded.count,
              "loss_s": MicroStopBucket.loss_s + stmt.excluded.loss_s,
              "short_faults": MicroStopBucket.short_faults + stmt.excluded.short_faults})
    await db.execute(stmt)


async def _add_short_fault(db: AsyncSession, info: MachineInfo, cal: Calendar, stop: Stop) -> list[dict]:
    p = short_fault_piece(stop, cal)
    if p is None:
        return []
    piece = {"b": _iso(p.bucket_start), "slot": p.shift_slot, "shift_id": p.shift_id,
             "day": p.productive_day.isoformat() if p.productive_day else None, "ps": _iso(p.bucket_start),
             "n": 0, "loss": 0.0, "sf": 1}
    await _bump_micro(db, info, piece, +1)
    return [piece]


async def _undo_micro(db: AsyncSession, info: MachineInfo, pieces: list[dict]) -> None:
    """Removes the provisional micro-stop pieces counted by the previous pass (they are re-detected)."""
    if not pieces:
        return
    for p in pieces:
        await _bump_micro(db, info, p, -1)
    await db.execute(delete(MicroStopBucket).where(
        MicroStopBucket.tenant_id == info.tenant_id, MicroStopBucket.machine_id == info.id,
        MicroStopBucket.bucket_start.in_({_dt(p["b"]) for p in pieces}),
        MicroStopBucket.count <= 0, MicroStopBucket.loss_s <= 1e-9, MicroStopBucket.short_faults <= 0))


async def _add_micro(db: AsyncSession, info: MachineInfo, cal: Calendar, stop: Stop, res: TickResult) -> list[dict]:
    out: list[dict] = []
    for p in micro_pieces(stop, cal):
        inst_start = None
        for inst in cal.day_instances(p.productive_day) if p.productive_day else []:
            if inst.slot == p.shift_slot:
                inst_start = inst.start
                break
        piece_start = max(p.bucket_start, inst_start) if inst_start else p.bucket_start
        piece = {"b": _iso(p.bucket_start), "slot": p.shift_slot, "shift_id": p.shift_id,
                 "day": p.productive_day.isoformat() if p.productive_day else None, "ps": _iso(piece_start),
                 "n": p.count, "loss": p.loss_s, "sf": p.short_faults}
        await _bump_micro(db, info, piece, +1)
        out.append(piece)
    if out:
        res.micro_stops += 1
    return out


async def _replace_spans(db: AsyncSession, info: MachineInfo, spans, after: datetime) -> Optional[datetime]:
    """Data spans after `after` are re-derived on every pass (late readings may fill a gap)."""
    base = (MachineDataSpan.tenant_id == info.tenant_id, MachineDataSpan.machine_id == info.id)
    await db.execute(delete(MachineDataSpan).where(*base, MachineDataSpan.start >= after))
    await db.execute(update(MachineDataSpan).where(*base, MachineDataSpan.end > after).values(end=after))
    await db.flush()
    last = (await db.execute(select(MachineDataSpan).where(*base).order_by(MachineDataSpan.end.desc()).limit(1)
                             )).scalar_one_or_none()
    for a, b in spans:
        if b <= after:
            continue
        a = max(a, after)
        if last is not None and last.end >= a:
            last.end = max(last.end, b)
        else:
            last = MachineDataSpan(tenant_id=info.tenant_id, machine_id=info.id, start=a, end=b)
            db.add(last)
    await db.flush()
    return last.end if last is not None else None


async def _close_out_shifts(db: AsyncSession, info: MachineInfo, cal: Calendar, now: datetime, res: TickResult) -> None:
    """Regra 4: sem motivo após o fim do turno → UNK-01 (events with an EQP suggestion keep it).
    Conditional update: a justification committed meanwhile by an operator is never overwritten."""
    rows = (await db.execute(
        select(DowntimeEvent.id, DowntimeEvent.start, DowntimeEvent.productive_day).where(
            DowntimeEvent.tenant_id == info.tenant_id, DowntimeEvent.machine_id == info.id,
            DowntimeEvent.reason_code.is_(None), DowntimeEvent.suggested_reason_code.is_(None),
            DowntimeEvent.end.is_not(None))
    )).all()
    due = []
    for eid, start, day in rows:
        if day is None:
            continue
        inst_end = next((i.end for i in cal.day_instances(day) if i.start <= start < i.end), None)
        if inst_end is not None and inst_end <= now:
            due.append(eid)
    if not due:
        return
    done = (await db.execute(
        update(DowntimeEvent).where(DowntimeEvent.tenant_id == info.tenant_id, DowntimeEvent.id.in_(due),
                                    DowntimeEvent.reason_code.is_(None),
                                    DowntimeEvent.suggested_reason_code.is_(None))
        .values(reason_code=UNJUSTIFIED).returning(DowntimeEvent.id)
        .execution_options(synchronize_session=False)
    )).scalars().all()
    for ev in (await db.execute(select(DowntimeEvent).where(DowntimeEvent.id.in_(done)))).scalars().all():
        await db.refresh(ev)
        res.messages.append(("event", ev, "update"))


# ── checkpoint state, snapshots, late data ─────────────────────────────────

async def _create_state(db: AsyncSession, tenant_id: str, machine_id: str, checkpoint: datetime) -> None:
    """Detector cursor of a machine + its origin snapshot (nothing detected before `checkpoint`)."""
    await db.execute(pg_insert(DowntimeDetectorState).values(
        machine_id=machine_id, tenant_id=tenant_id, checkpoint=checkpoint, covered_until=None, state={}
    ).on_conflict_do_nothing(index_elements=["machine_id"]))
    await db.execute(pg_insert(DowntimeDetectorSnapshot).values(
        machine_id=machine_id, tenant_id=tenant_id, checkpoint=checkpoint, state={"origin": True},
        micro_from=_hour(checkpoint), micro=[]
    ).on_conflict_do_nothing(index_elements=["machine_id", "checkpoint"]))


_late: dict[tuple[str, str], datetime] = {}


def note_late(tenant_id: str, machine_id: str, ts: datetime) -> None:
    """MQTT handler: a detector signal reading reached InfluxDB more than downtime_settle_s after its
    own timestamp. The next detector tick rewinds that machine if its checkpoint already passed ts."""
    k = (tenant_id, machine_id)
    cur = _late.get(k)
    if cur is None or ts < cur:
        _late[k] = ts


async def flush_late(db: AsyncSession, now: datetime) -> int:
    """Persists the late-data notes (downtime_detector_states.late_from). Machines without a cursor
    get one that starts before the late reading. Returns how many machines must rewind."""
    items = list(_late.items())
    _late.clear()
    n = 0
    for (tid, mid), ts in items:
        hit = (await db.execute(
            update(DowntimeDetectorState)
            .where(DowntimeDetectorState.tenant_id == tid, DowntimeDetectorState.machine_id == mid,
                   DowntimeDetectorState.checkpoint >= ts)
            .values(late_from=func.least(func.coalesce(DowntimeDetectorState.late_from, ts), ts))
            .returning(DowntimeDetectorState.machine_id)
        )).first()
        if hit is not None:
            n += 1
            continue
        exists = (await db.execute(select(DowntimeDetectorState.machine_id).where(
            DowntimeDetectorState.machine_id == mid))).first()
        if exists is None:
            await _create_state(db, tid, mid, min(now - timedelta(hours=settings.downtime_initial_lookback_h),
                                                  ts - timedelta(microseconds=1)))
    await db.commit()
    return n


async def _rewind(db: AsyncSession, info: MachineInfo, state: DowntimeDetectorState, late: datetime) -> dict:
    """Moves the cursor back to the latest snapshot before `late` and restores the micro-stop
    buckets as they were there. Events, spans and micro-stops after it are re-derived by the pass."""
    snaps = (await db.execute(
        select(DowntimeDetectorSnapshot).where(DowntimeDetectorSnapshot.tenant_id == info.tenant_id,
                                               DowntimeDetectorSnapshot.machine_id == info.id)
        .order_by(DowntimeDetectorSnapshot.checkpoint)
    )).scalars().all()
    base = None
    for s in snaps:
        if s.checkpoint < late:
            base = s
    if base is None:
        oldest = snaps[0] if snaps else None
        if oldest is None or (oldest.state or {}).get("origin"):
            # nothing was ever detected before the origin: move the origin back
            origin = late - timedelta(microseconds=1)
            await db.execute(delete(DowntimeDetectorSnapshot).where(
                DowntimeDetectorSnapshot.tenant_id == info.tenant_id, DowntimeDetectorSnapshot.machine_id == info.id))
            base = DowntimeDetectorSnapshot(machine_id=info.id, tenant_id=info.tenant_id, checkpoint=origin,
                                            state={"origin": True}, micro_from=_hour(origin), micro=[])
            db.add(base)
        else:
            log.warning("downtime.detector.late_beyond_horizon", machine_id=info.id[:8], late=late.isoformat(),
                        oldest=oldest.checkpoint.isoformat())
            base = oldest
    await db.execute(delete(DowntimeDetectorSnapshot).where(
        DowntimeDetectorSnapshot.tenant_id == info.tenant_id, DowntimeDetectorSnapshot.machine_id == info.id,
        DowntimeDetectorSnapshot.checkpoint > base.checkpoint))
    if base.micro_from is not None:
        await db.execute(delete(MicroStopBucket).where(
            MicroStopBucket.tenant_id == info.tenant_id, MicroStopBucket.machine_id == info.id,
            MicroStopBucket.bucket_start >= base.micro_from))
        for piece in base.micro or []:
            await _bump_micro(db, info, piece, +1)
    await db.flush()
    log.info("downtime.detector.rewind", machine_id=info.id[:8], late=late.isoformat(),
             to=base.checkpoint.isoformat(), was=state.checkpoint.isoformat())
    state.checkpoint = base.checkpoint
    return {k: v for k, v in (base.state or {}).items() if k != "origin"}


async def _snapshot(db: AsyncSession, info: MachineInfo, cp: datetime, js: dict, pending: list[dict],
                    now: datetime) -> None:
    last = (await db.execute(
        select(func.max(DowntimeDetectorSnapshot.checkpoint)).where(
            DowntimeDetectorSnapshot.tenant_id == info.tenant_id, DowntimeDetectorSnapshot.machine_id == info.id)
    )).scalar_one_or_none()
    if last is not None and (cp - last).total_seconds() < settings.downtime_snapshot_interval_s:
        return
    micro_from = _hour(cp - _MICRO_MARGIN)
    rows = (await db.execute(select(MicroStopBucket).where(
        MicroStopBucket.tenant_id == info.tenant_id, MicroStopBucket.machine_id == info.id,
        MicroStopBucket.bucket_start >= micro_from))).scalars().all()
    acc: dict[tuple[str, str], dict] = {}
    for r in rows:
        acc[(_iso(r.bucket_start), r.shift_slot)] = {
            "b": _iso(r.bucket_start), "slot": r.shift_slot, "shift_id": r.shift_id,
            "day": r.productive_day.isoformat() if r.productive_day else None, "ps": _iso(r.piece_start),
            "n": r.count, "loss": r.loss_s, "sf": r.short_faults}
    for p in pending:                       # provisional pieces are not part of the snapshot
        a = acc.get((p["b"], p["slot"]))
        if a is not None:
            a["n"] -= p["n"]
            a["loss"] -= p["loss"]
            a["sf"] -= p["sf"]
    micro = [a for a in acc.values() if a["n"] > 0 or a["loss"] > 1e-9]
    db.add(DowntimeDetectorSnapshot(machine_id=info.id, tenant_id=info.tenant_id, checkpoint=cp, state=js,
                                    micro_from=micro_from, micro=micro))
    # prune: keep the horizon plus the newest snapshot older than it
    horizon = now - timedelta(hours=settings.downtime_rewind_horizon_h)
    keep_old = (await db.execute(select(func.max(DowntimeDetectorSnapshot.checkpoint)).where(
        DowntimeDetectorSnapshot.tenant_id == info.tenant_id, DowntimeDetectorSnapshot.machine_id == info.id,
        DowntimeDetectorSnapshot.checkpoint < horizon))).scalar_one_or_none()
    if keep_old is not None:
        await db.execute(delete(DowntimeDetectorSnapshot).where(
            DowntimeDetectorSnapshot.tenant_id == info.tenant_id, DowntimeDetectorSnapshot.machine_id == info.id,
            DowntimeDetectorSnapshot.checkpoint < keep_old))


def _matured(seen: list, wall: datetime) -> Optional[datetime]:
    """Latest reading ts that was already visible downtime_settle_s ago (readings up to it have had
    time to land in Influx even when processed out of order)."""
    limit = wall - timedelta(seconds=settings.downtime_settle_s)
    vals = [_dt(ts) for w, ts in seen if _dt(w) <= limit and ts]
    return max(vals) if vals else None


def _seen_append(seen: list, wall: datetime, max_ts: Optional[datetime]) -> list:
    limit = wall - timedelta(seconds=settings.downtime_settle_s)
    matured = [e for e in seen if _dt(e[0]) <= limit]
    fresh = [e for e in seen if _dt(e[0]) > limit]
    out = ([matured[-1]] if matured else []) + fresh + [[_iso(wall), _iso(max_ts)]]
    return out[-50:]


async def detect_machine(db: AsyncSession, info: MachineInfo, cal: Calendar, now: datetime,
                         fetch: Fetch = influx_fetch, tags: Optional[SignalTags] = None,
                         wall: Optional[datetime] = None) -> TickResult:
    """One detector pass for one machine. Commits. Skips machines locked by another pass.

    `now` bounds the readings (data time); `wall` is the real clock, used to know which readings are
    settled (see _matured). Each pass re-derives everything after the checkpoint: events (upsert by
    start, stale ones removed), micro-stops still provisional, data spans."""
    res = TickResult()
    wall = wall or datetime.now(timezone.utc)
    tags = tags or await signal_tags(db, info.tenant_id, info.id)
    if not tags.all:
        return res
    lock = (select(DowntimeDetectorState).where(DowntimeDetectorState.machine_id == info.id,
                                                DowntimeDetectorState.tenant_id == info.tenant_id)
            .with_for_update(skip_locked=True))
    state = (await db.execute(lock)).scalar_one_or_none()
    if state is None:
        exists = (await db.execute(select(DowntimeDetectorState.machine_id).where(
            DowntimeDetectorState.machine_id == info.id))).scalar_one_or_none()
        if exists:
            return res                      # locked by a concurrent pass
        await _create_state(db, info.tenant_id, info.id,
                            now - timedelta(hours=settings.downtime_initial_lookback_h))
        await db.flush()
        state = (await db.execute(lock)).scalar_one_or_none()
        if state is None:
            return res

    js = dict(state.state or {})
    seen = js.pop("seen", None) or []
    pending = js.pop("micro_pending", None) or []
    if state.late_from is not None:
        late, state.late_from = state.late_from, None
        if late <= state.checkpoint:
            await _undo_micro(db, info, pending)
            pending = []
            js = await _rewind(db, info, state, late)
            res.rewound = True
    old_cp = state.checkpoint

    mode, ideal = "run", None
    if tags.run is None and tags.cycle is not None:
        pri = await product_pri(db, info.tenant_id, info)
        ideal = pri.fallback_pri
        mode = "cycle" if ideal else "run"
    rows = await fetch(info.tenant_id, tags.all, old_cp, now)
    res.readings = len(rows)
    readings, ctracks = _to_readings(rows, tags, js.get("counter"))
    initial = SignalState.from_json(js)
    if initial.at is None:
        initial = SignalState(at=None)
    det = detect(initial, readings, now, _params(info, mode, ideal), cap=_matured(seen, wall) or _NEVER)
    new_cp = det.checkpoint if det.checkpoint is not None and det.checkpoint > old_cp else old_cp

    kept: set[str] = set()
    for stop in det.stops:
        if stop.duration_s >= info.threshold_s:
            kept |= await _upsert_events(db, info, cal, stop, now, res)
    resumed = initial.down_since if initial.at is not None else None
    await _drop_stale_events(db, info, old_cp, now, kept, res, resumed=resumed)
    await _drop_stale_failures(db, info, min(old_cp, resumed) if resumed else old_cp, res.failures_kept)

    # micro-stops: final ones after the checkpoint are (re)counted on every pass; those the new
    # checkpoint passes become definitive, the others stay provisional (undone next pass)
    await _undo_micro(db, info, pending)
    new_pending: list[dict] = []
    for stop in det.stops:
        if stop.end is None or stop.seen_end <= old_cp:
            continue
        if stop.duration_s >= info.threshold_s:
            # S5-1b: the short fault of a long (break) stop is counted in short_faults, once, like a micro-stop's
            if (stop.final or stop.seen_end <= new_cp) and short_offtime_fault(stop, cal, info.threshold_s, now):
                pieces = await _add_short_fault(db, info, cal, stop)
                if stop.seen_end > new_cp:
                    new_pending += pieces
            continue
        if stop.final or stop.seen_end <= new_cp:
            pieces = await _add_micro(db, info, cal, stop, res)
            if stop.seen_end > new_cp:
                new_pending += pieces
            elif pieces:
                DOWNTIME_EVENTS.labels("micro_stop").inc()      # definitive: counted once

    covered = await _replace_spans(db, info, det.spans, old_cp)
    if new_cp > old_cp:
        counter = js.get("counter")
        for ts, tr in ctracks:
            if ts <= new_cp:
                counter = tr
        js = det.checkpoint_state.to_json()
        if counter:
            js["counter"] = counter
        state.checkpoint = new_cp
        await db.flush()
        await _snapshot(db, info, new_cp, dict(js), new_pending, now)
    state.covered_until = covered
    state.state = {**js, "seen": _seen_append(seen, wall, det.last_state.at or initial.at),
                   "micro_pending": new_pending}
    await _close_out_shifts(db, info, cal, now, res)
    msgs = [m if kind == "raw" else event_message(m, action) for kind, m, action in res.messages]
    await db.commit()
    res.messages = msgs
    return res


# ── background loop ─────────────────────────────────────────────────────────

_MACHINES_SQL = text("""
SELECT DISTINCT t.tenant_id::text, t.machine_id::text
  FROM tags t JOIN tenants tn ON tn.id = t.tenant_id
 WHERE t.is_active AND t.machine_id IS NOT NULL AND tn.is_active
   AND t.semantic_role IN ('run_state', 'fault_state', 'cycle_counter', 'cycle_pulse',
                           'part_counter_total', 'part_counter_good')
""")


async def run_tick(now: Optional[datetime] = None, fetch: Fetch = influx_fetch) -> int:
    from ...db import AsyncSessionLocal
    from ...websocket.manager import manager

    now = now or datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        if await flush_late(db, now):
            log.info("downtime.detector.late_data")
    async with AsyncSessionLocal() as db:
        pairs = (await db.execute(_MACHINES_SQL)).all()
    by_tenant: dict[str, list[str]] = {}
    for tid, mid in pairs:
        by_tenant.setdefault(tid, []).append(mid)
    n = 0
    for tid, mids in by_tenant.items():
        async with AsyncSessionLocal() as db:
            infos = await machine_infos(db, tid, mids)
            cals: dict[str, Calendar] = {}
        for mid, info in infos.items():
            try:
                async with AsyncSessionLocal() as db:
                    if info.plant_id not in cals:
                        cals[info.plant_id] = await load_calendar(db, tid, info.plant_id, info.tz)
                    res = await asyncio.wait_for(detect_machine(db, info, cals[info.plant_id], now, fetch),
                                                 timeout=settings.downtime_machine_timeout_s)
                for msg in res.messages:
                    await manager.broadcast(tid, msg)
                n += 1
            except asyncio.TimeoutError:
                # nothing is lost: the machine's cursor did not advance, the next tick picks it up again
                log.warning("downtime.detector.timeout", machine_id=mid[:8],
                            timeout_s=settings.downtime_machine_timeout_s)
            except Exception as e:  # noqa: BLE001
                log.warning("downtime.detector.error", machine_id=mid[:8], error=str(e)[:300],
                            error_type=type(e).__name__)
    return n


async def run_downtime_detector() -> None:
    """Long-running task (app lifespan)."""
    while True:
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("downtime.detector.tick_error", error=str(e)[:300])
        await asyncio.sleep(settings.downtime_tick_s)
