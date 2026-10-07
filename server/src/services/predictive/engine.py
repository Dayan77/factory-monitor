"""Predictive pipeline (P-12): job per closed window (and catch-up of delays):
Influx → feature (P-1) → Postgres `condition_features` → fold of the detectors since the last baseline reset
(P-2 … P-7) → reconciliation of the alerts (P-8) → notifications / WebSocket / condition plans (P-9).

`process_monitor` is idempotent: the fold is recomputed from the segment's stored windows and the recorded
lifecycle events, so running it twice — or `recompute` from scratch — yields the same alerts (same instants and
evidence).
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Optional

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db import AsyncSessionLocal
from ...models import (ConditionBaseline, ConditionFeature, Position, PredictiveAlert, PredictiveAlertEvent,
                       PredictiveDirty, PredictiveMonitor, ProductionPlanDay, Tag, UsageBucket)
from ...models.tag import CYCLE_ROLES, SemanticRole
from .. import clock
from ..ingest import watermarks
from . import core, features as fx
from . import service as ps
from .defaults import FEATURE_LABEL, SEV_RANK, USAGE_MEASURE

log = structlog.get_logger()

EXT_SOURCES = ("user", "work_order", "failure", "plan")
ACTION_OF = {"acknowledge": "acknowledge", "convert": "convert", "unconvert": "unconvert", "close": "close"}
# late readings: a window is extracted this long after it closes (settings.predictive_grace_s, default 2 min)
GRACE = timedelta(seconds=float(getattr(settings, "predictive_grace_s", 120.0)))
MAX_WINDOWS_PER_RUN = 400


def _utc(t: Optional[datetime]) -> Optional[datetime]:
    return ps.aware(t)


# ── window storage ────────────────────────────────────────────────────────────

async def last_window(db: AsyncSession, m: PredictiveMonitor) -> Optional[ConditionFeature]:
    return (await db.execute(select(ConditionFeature).where(
        ConditionFeature.tenant_id == m.tenant_id, ConditionFeature.monitor_id == m.id)
        .order_by(ConditionFeature.i.desc()).limit(1))).scalar_one_or_none()


async def store_window(db: AsyncSession, m: PredictiveMonitor, reset: ConditionBaseline, *, start: datetime,
                       end: datetime, x: Optional[float], n: int = 0, valid: Optional[bool] = None,
                       u: Optional[float] = None, running_h: Optional[float] = None,
                       coverage: Optional[float] = None, i: Optional[int] = None,
                       carry: Optional[float] = None) -> ConditionFeature:
    """Adds one window to the monitor's series (P-1). `u` defaults to the previous window's usage plus this
    window's usage (block cycles, or running hours)."""
    prev = await last_window(db, m)
    if i is None:
        if m.window_hours:
            i = int(round((_utc(start) - _utc(m.anchor_at)).total_seconds() / 3600 / m.window_hours)) + 1
        else:
            i = (prev.i + 1) if prev else 1
    if u is None:
        inc = float(m.window_cycles) if m.window_cycles else float(running_h or 0.0)
        u = (prev.u if prev else 0.0) + inc
    valid = (x is not None) if valid is None else (valid and x is not None)
    f = ConditionFeature(monitor_id=m.id, i=i, tenant_id=m.tenant_id, position_id=m.position_id, feature=m.feature,
                         baseline_id=reset.id, start_at=start, end_at=end, x=x if valid else None, n=n, valid=valid,
                         u=u, running_h=running_h, coverage=coverage, triggers=[], carry=carry)
    db.add(f)
    await db.flush()
    if reset.first_i is None:
        reset.first_i = i
    return f


async def _tags(db: AsyncSession, m: PredictiveMonitor) -> dict:
    pos = await db.get(Position, m.position_id)
    run = (await db.execute(select(Tag.id).where(Tag.tenant_id == m.tenant_id, Tag.position_id == m.position_id,
                                                 Tag.semantic_role == SemanticRole.run_state, Tag.is_active.is_(True))
                            .limit(1))).scalar_one_or_none()
    if run is None:
        run = (await db.execute(select(Tag.id).where(Tag.tenant_id == m.tenant_id, Tag.machine_id == m.machine_id,
                                                     Tag.position_id.is_(None), Tag.is_active.is_(True),
                                                     Tag.semantic_role == SemanticRole.run_state).limit(1))
               ).scalar_one_or_none()
    counter = pos.counter_tag_id if pos is not None else None
    cmax = None
    if counter is None:
        row = (await db.execute(select(Tag.id, Tag.counter_max).where(
            Tag.tenant_id == m.tenant_id, Tag.position_id == m.position_id, Tag.semantic_role.in_(list(CYCLE_ROLES)))
            .limit(1))).first()
        if row:
            counter, cmax = row
    else:
        cmax = (await db.execute(select(Tag.counter_max).where(Tag.id == counter))).scalar_one_or_none()
    return {"run_state": run, "counter": counter, "counter_max": cmax}


def _gap_s(m: PredictiveMonitor) -> float:
    return float((m.params or {}).get("data_gap_s") or settings.machine_offline_timeout_s)


async def _time_window(m: PredictiveMonitor, tags: dict, start: datetime, end: datetime) -> fx.WindowFeature:
    gap = _gap_s(m)
    ids = [m.tag_id] + ([tags["run_state"]] if tags["run_state"] else []) + \
          ([m.ambient_tag_id] if m.ambient_tag_id else [])
    pts = await fx.influx_points(m.tenant_id, ids, start, end)
    held0 = await fx.last_before(m.tenant_id, ids, start, timedelta(seconds=max(gap, 1.0) * 2))
    for tid, (t, v) in held0.items():
        if tid in pts and (start - t).total_seconds() <= gap:
            pts[tid].insert(0, (start, v))
    pres = await fx.presence(m.tenant_id, m.machine_id, start, end, gap)
    args = (pts.get(m.tag_id, []), pts.get(tags["run_state"]) if tags["run_state"] else None, start, end, pres,
            pts.get(m.ambient_tag_id) if m.ambient_tag_id else None)
    if sum(len(v) for v in pts.values()) > 5000:       # pure arithmetic over many samples: off the event loop
        return await asyncio.to_thread(fx.time_window_feature, *args)
    return fx.time_window_feature(*args)


# ── late data (gateway store-and-forward, ingestion backlog) ──────────────────
# The ingestion writes per-tag watermarks and the range of readings written long after their own timestamp
# (services/ingest/watermarks.py → ingest_watermarks). Each engine run moves those marks into `predictive_dirty`
# (one row per affected monitor) and every monitor run re-extracts the stored windows a dirty range touches —
# valid or invalid — and refolds its detectors (deterministic: the same as extracting everything again).
# Persisted in Postgres, so it works across a restart and with the engine in its own process (src/worker.py).


def late_threshold() -> timedelta:
    """A reading older than this when it reaches Influx may belong to a window already extracted."""
    return GRACE


async def _flush_ingest_marks() -> None:
    """Watermarks noted by an ingestion running in this same process (tests, PREDICTIVE_WORKER=inline, the API
    process before /process) → Postgres, in their own transaction."""
    if not watermarks.pending():
        return
    async with AsyncSessionLocal() as s2:
        await watermarks.flush(s2)
        await s2.commit()


async def transfer_late(db: AsyncSession, tenant_id: Optional[str] = None) -> int:
    """Moves the ingestion's late marks into `predictive_dirty` for the monitors whose windows they can touch:
    time-window monitors — any tag of the machine (signal, run_state, ambient, and data presence); cycle-block
    monitors — their stroke tag or the position's cycle counter. Same transaction as the marks' clearing."""
    marks = await watermarks.take_late(db, tenant_id)
    by_machine: dict[tuple[str, str], list[tuple[str, datetime, datetime]]] = {}
    for tid, tag, mid, lo, hi in marks:
        if mid:
            by_machine.setdefault((str(tid), str(mid)), []).append((str(tag), _utc(lo), _utc(hi)))
    n = 0
    for (tid, mid), items in by_machine.items():
        mons = (await db.execute(select(PredictiveMonitor).where(
            PredictiveMonitor.tenant_id == tid, PredictiveMonitor.machine_id == mid))).scalars().all()
        for m in mons:
            if m.window_hours:
                rel = items
            else:
                tags = await _tags(db, m)
                rel = [it for it in items if it[0] in (m.tag_id, tags["counter"])]
            if not rel:
                continue
            db.add(PredictiveDirty(tenant_id=tid, monitor_id=m.id, lo=min(r[1] for r in rel),
                                   hi=max(r[2] for r in rel)))
            n += 1
    if n:
        await db.flush()
    return n


async def take_dirty(db: AsyncSession, m: PredictiveMonitor) -> Optional[tuple[datetime, datetime]]:
    rows = (await db.execute(delete(PredictiveDirty).where(
        PredictiveDirty.tenant_id == m.tenant_id, PredictiveDirty.monitor_id == m.id)
        .returning(PredictiveDirty.lo, PredictiveDirty.hi))).all()
    if not rows:
        return None
    return min(_utc(r[0]) for r in rows), max(_utc(r[1]) for r in rows)


def _put_back_dirty(db: AsyncSession, m: PredictiveMonitor, lo: datetime, hi: datetime) -> None:
    db.add(PredictiveDirty(tenant_id=m.tenant_id, monitor_id=m.id, lo=lo, hi=hi))


async def reextract_windows(db: AsyncSession, m: PredictiveMonitor, reset: ConditionBaseline, tags: dict,
                            lo: datetime, hi: Optional[datetime] = None, only_invalid: bool = False
                            ) -> tuple[int, Optional[datetime]]:
    """Re-extracts the stored time windows of the current segment that readings in [lo, hi] can change (a value
    holds for up to the data-gap limit after its reading). A changed window gets its new point; its running hours
    change the usage `u` of every later window by the same amount. Returns (windows changed, where to resume
    when the run limit was reached)."""
    hold = timedelta(seconds=2 * max(_gap_s(m), 1.0))
    q = select(ConditionFeature).where(
        ConditionFeature.tenant_id == m.tenant_id, ConditionFeature.monitor_id == m.id,
        ConditionFeature.baseline_id == reset.id, ConditionFeature.end_at > lo)
    if hi is not None:
        q = q.where(ConditionFeature.start_at <= hi + hold)
    if only_invalid:
        q = q.where(ConditionFeature.valid.is_(False))
    rows = list((await db.execute(q.order_by(ConditionFeature.i).limit(MAX_WINDOWS_PER_RUN + 1))).scalars().all())
    more = len(rows) > MAX_WINDOWS_PER_RUN
    rows = rows[:MAX_WINDOWS_PER_RUN]
    changed = 0
    for f in rows:
        wf = await _time_window(m, tags, _utc(f.start_at), _utc(f.end_at))
        new_valid = bool(wf.valid and wf.x is not None)
        new_x = wf.x if new_valid else None
        if (new_valid == bool(f.valid) and new_x == f.x and (wf.running_h or 0.0) == (f.running_h or 0.0)
                and wf.n == f.n and wf.coverage == f.coverage):
            continue
        delta = (wf.running_h or 0.0) - (f.running_h or 0.0)
        f.x, f.valid, f.n, f.running_h, f.coverage = new_x, new_valid, wf.n, wf.running_h, wf.coverage
        if delta:
            later = (await db.execute(select(ConditionFeature).where(
                ConditionFeature.tenant_id == m.tenant_id, ConditionFeature.monitor_id == m.id,
                ConditionFeature.i >= f.i))).scalars().all()
            for g in later:
                g.u = (g.u or 0.0) + delta
        changed += 1
    await db.flush()
    if changed:
        log.info("predictive.late_windows", monitor_id=m.id, changed=changed)
    return changed, (_utc(rows[-1].end_at) if more else None)


async def reextract_late(db: AsyncSession, m: PredictiveMonitor, reset: ConditionBaseline, tags: dict,
                         since: Optional[datetime] = None) -> int:
    """/recompute: re-reads the segment's *invalid* time windows from Influx (readings that arrived without a
    late mark, e.g. written straight to Influx or noted before a crash)."""
    since = since or _utc(reset.reset_at)
    total = 0
    while True:
        n, resume = await reextract_windows(db, m, reset, tags, since, None, only_invalid=True)
        total += n
        if resume is None:
            return total
        since = resume


async def rewind_cycles(db: AsyncSession, m: PredictiveMonitor, reset: ConditionBaseline, lo: datetime) -> int:
    """Late readings at/after `lo` for a cycle-block monitor: the stored blocks of the segment that end at or
    after `lo` may change (a late stroke reading belongs to the block whose (start, end] holds it; a late counter
    reading moves the boundaries from there on). They are removed and the cursor goes back to the end of the block
    before them (its `carry`), so the next extraction cuts them again exactly as a full re-extraction would.
    Returns the highest block index removed (0: nothing to redo)."""
    first = (await db.execute(select(ConditionFeature).where(
        ConditionFeature.tenant_id == m.tenant_id, ConditionFeature.monitor_id == m.id,
        ConditionFeature.baseline_id == reset.id, ConditionFeature.end_at >= lo)
        .order_by(ConditionFeature.i).limit(1))).scalar_one_or_none()
    if first is None:
        return 0                        # only the open block is touched: nothing stored to redo
    prev = (await db.execute(select(ConditionFeature).where(
        ConditionFeature.tenant_id == m.tenant_id, ConditionFeature.monitor_id == m.id,
        ConditionFeature.baseline_id == reset.id, ConditionFeature.i < first.i)
        .order_by(ConditionFeature.i.desc()).limit(1))).scalar_one_or_none()
    from_i = first.i
    if prev is not None and prev.carry is None:
        # block stored before the per-block cursor existed: redo the whole segment
        prev = None
        from_i = (await db.execute(select(func.min(ConditionFeature.i)).where(
            ConditionFeature.tenant_id == m.tenant_id, ConditionFeature.monitor_id == m.id,
            ConditionFeature.baseline_id == reset.id))).scalar_one()
    top = (await db.execute(select(func.max(ConditionFeature.i)).where(
        ConditionFeature.tenant_id == m.tenant_id, ConditionFeature.monitor_id == m.id,
        ConditionFeature.baseline_id == reset.id))).scalar_one()
    await db.execute(delete(ConditionFeature).where(
        ConditionFeature.tenant_id == m.tenant_id, ConditionFeature.monitor_id == m.id,
        ConditionFeature.baseline_id == reset.id, ConditionFeature.i >= from_i))
    st = dict(m.status or {})
    if prev is None:
        st.pop("cycle_cursor", None)
        reset.first_i = None
    else:
        st["cycle_cursor"] = {"baseline_id": reset.id, "end_at": clock.iso(_utc(prev.end_at)),
                              "carry": float(prev.carry)}
    st["rebuild_to_i"] = max(int(top or 0), int(st.get("rebuild_to_i") or 0))
    m.status = st
    await db.flush()
    log.info("predictive.late_blocks", monitor_id=m.id, from_i=from_i, to_i=top, late_from=clock.iso(lo))
    return int(top or 0)


async def apply_dirty(db: AsyncSession, m: PredictiveMonitor, reset: ConditionBaseline, tags: dict,
                      lo: datetime, hi: datetime) -> int:
    if m.window_hours:
        n, resume = await reextract_windows(db, m, reset, tags, lo, hi)
        if resume is not None:
            _put_back_dirty(db, m, resume, hi)
        return n
    return 1 if await rewind_cycles(db, m, reset, lo) else 0


async def extract(db: AsyncSession, m: PredictiveMonitor, reset: ConditionBaseline, now: datetime) -> int:
    """Closed windows not yet stored (P-1), from Influx."""
    if not m.tag_id:
        return 0
    horizon = now - GRACE
    prev = await last_window(db, m)
    seg_prev = prev if (prev is not None and prev.baseline_id == reset.id) else None
    tags = await _tags(db, m)
    made = 0
    if m.window_hours:
        dur = timedelta(hours=m.window_hours)
        anchor = _utc(m.anchor_at)
        if seg_prev is not None:
            start = _utc(seg_prev.end_at)
        else:
            k = max(0, -(-int((_utc(reset.reset_at) - anchor).total_seconds()) // int(dur.total_seconds())))
            start = anchor + k * dur
            if prev is not None and _utc(prev.end_at) > start:
                start = _utc(prev.end_at)
        while start + dur <= horizon and made < MAX_WINDOWS_PER_RUN:
            end = start + dur
            wf = await _time_window(m, tags, start, end)
            await store_window(db, m, reset, start=start, end=end, x=wf.x, n=wf.n, valid=wf.valid,
                               running_h=wf.running_h, coverage=wf.coverage)
            made += 1
            start = end
        return made
    return await extract_cycles(db, m, reset, horizon, prev, seg_prev, tags)


_US = timedelta(microseconds=1)
COUNTER_LOOKBACK = timedelta(days=366)
COUNTER_SLICE = timedelta(hours=6)


async def _counter_at(m: PredictiveMonitor, counter: str, at: datetime) -> Optional[float]:
    """Counter value held at `at` (the reading AT `at` included: it is the one that closed the previous block)."""
    got = await fx.last_before(m.tenant_id, [counter], at + _US, COUNTER_LOOKBACK)
    return got.get(counter, (None, None))[1]


async def _next_close(m: PredictiveMonitor, tags: dict, after: datetime, horizon: datetime, carry: float
                      ) -> Optional[tuple[datetime, float]]:
    """Where the next block of N = `window_cycles` closes after the cursor (`after`, `carry`): (instant of the
    reading that closes it, cycles counted since the previous block boundary at that reading — ≥ N, the excess is
    the next `carry`). None: still open (or no data). Answered with aggregates computed inside Influx (first/last
    reading, drops, first reading reaching the target): no raw rows are read while a block is open.

    Boundary rules (P-12): the counter reading AT `after` closed the previous block — it is the reference value and
    is not counted again; a reading belongs to the block whose (start, end] holds it (ties at the closing instant
    go to the closing block)."""
    n_block = m.window_cycles
    if carry >= n_block:
        return after, carry             # one counter step covered several blocks: the next one closes at once
    counter = tags["counter"]
    if not counter:
        probe = await fx.tag_probe(m.tenant_id, m.tag_id, after, horizon)
        if probe["count"] < n_block:
            return None
        t = await fx.nth_reading_time(m.tenant_id, m.tag_id, after, horizon, n_block)
        return (t, float(n_block)) if t is not None else None
    probe = await fx.tag_probe(m.tenant_id, counter, after, horizon)
    if probe["first"] is None:
        return None
    pv = await _counter_at(m, counter, after)
    ref = pv if pv is not None else probe["first"][1]
    if probe["drops"] or probe["first"][1] < ref:
        # a reset/rollover inside the open block (rare): walk the counter readings themselves, in slices
        acc, last = 0.0, pv
        async for t, v in fx.counter_points(m.tenant_id, counter, after, horizon, COUNTER_SLICE):
            if last is not None:
                d = v - last
                if d < 0:
                    d = (tags["counter_max"] + 1 - last + v) if tags["counter_max"] else v
                acc += d
            last = v
            if acc + carry >= n_block:
                return t, acc + carry
        return None
    if probe["last"][1] - ref + carry < n_block:
        return None
    hit = await fx.first_reaching(m.tenant_id, counter, after, horizon, ref + n_block - carry)
    return (hit[0], hit[1] - ref + carry) if hit is not None else None


async def extract_cycles(db: AsyncSession, m: PredictiveMonitor, reset: ConditionBaseline, horizon: datetime,
                         prev: Optional[ConditionFeature], seg_prev: Optional[ConditionFeature], tags: dict) -> int:
    """Blocks of N cycles (P-1: block i = cycles (i−1)·N+1 … i·N since the reset), resumed from the cursor = the
    instant of the reading that closed the last block (and the cycles it overshot, `carry`, also stored with the
    block). Each block's feature (count, median of the stroke times in (start, end]) is computed inside Influx.

    Nothing is skipped: while no block closes, the cursor stays at the last block end, so readings that arrive
    late (gateway store-and-forward flush, hours after the fact) still land in the right block; late readings
    inside blocks already stored rewind the cursor (rewind_cycles)."""
    made = 0
    st = dict(m.status or {})
    cur = st.get("cycle_cursor") if (st.get("cycle_cursor") or {}).get("baseline_id") == reset.id else None
    after = ps.parse_iso(cur["end_at"]) if cur else _utc(reset.reset_at)
    carry = float(cur["carry"]) if cur else 0.0
    k0 = (seg_prev.i + 1) if seg_prev else ((prev.i + 1) if prev else 1)
    caught_up = False
    while made < MAX_WINDOWS_PER_RUN:
        nxt = await _next_close(m, tags, after, horizon, carry) if after < horizon else None
        if nxt is None:
            caught_up = True
            break
        close_at, total = nxt
        n, med = await fx.block_stats(m.tenant_id, m.tag_id, after, close_at)
        valid = n > 0 and n >= fx.MIN_BLOCK_READINGS * m.window_cycles
        new_carry = total - m.window_cycles
        await store_window(db, m, reset, start=after, end=close_at, x=med if valid else None, n=n, valid=valid,
                           i=k0, carry=new_carry)
        made += 1
        after, carry, k0 = close_at, new_carry, k0 + 1
        st["cycle_cursor"] = {"baseline_id": reset.id, "end_at": clock.iso(after), "carry": carry}
    if caught_up or (st.get("rebuild_to_i") and k0 - 1 >= int(st["rebuild_to_i"])):
        st.pop("rebuild_to_i", None)
    m.status = st
    return made


# ── usage rate and production plan (P-7) ──────────────────────────────────────

async def hourly_usage(db: AsyncSession, m: PredictiveMonitor, start: datetime, stop: datetime) -> dict:
    measure = "cycles" if USAGE_MEASURE[m.feature] == "cycles" else "operating_seconds"
    scale = 1.0 if measure == "cycles" else 1 / 3600.0
    for scope, eid in (("position", m.position_id), ("machine", m.machine_id)):
        rows = (await db.execute(select(UsageBucket.bucket_start, UsageBucket.value).where(
            UsageBucket.tenant_id == m.tenant_id, UsageBucket.scope == scope, UsageBucket.entity_id == eid,
            UsageBucket.measure == measure, UsageBucket.bucket_start >= start - timedelta(hours=1),
            UsageBucket.bucket_start < stop))).all()
        if rows:
            return {_utc(t): float(v) * scale for t, v in rows}
    return {}


async def plan_fn_for(db: AsyncSession, m: PredictiveMonitor):
    rows = (await db.execute(select(ProductionPlanDay).where(
        ProductionPlanDay.tenant_id == m.tenant_id, ProductionPlanDay.machine_id == m.machine_id))).scalars().all()
    if not rows:
        return None
    pos = await db.get(Position, m.position_id)
    cpp = float(pos.cycles_per_part or 1) if pos else 1.0
    plan: dict[date, float] = {}
    for r in rows:
        if USAGE_MEASURE[m.feature] == "cycles":
            if r.planned_parts is not None:
                plan[r.day] = r.planned_parts * cpp
        elif r.planned_hours is not None:
            plan[r.day] = r.planned_hours
    if not plan:
        return None
    horizon = max(plan)

    def fn(today: date, remaining: float, rate: Optional[float]):
        if not any(d > today for d in plan):
            return None
        return core.project_with_plan(today, remaining, plan, horizon, rate)

    return fn


# ── fold + reconcile ──────────────────────────────────────────────────────────

async def segment_inputs(db: AsyncSession, m: PredictiveMonitor, reset: ConditionBaseline):
    rows = list((await db.execute(select(ConditionFeature).where(
        ConditionFeature.tenant_id == m.tenant_id, ConditionFeature.monitor_id == m.id,
        ConditionFeature.baseline_id == reset.id).order_by(ConditionFeature.i))).scalars().all())
    wins = [core.WindowIn(i=f.i, end=_utc(f.end_at), x=f.x if f.valid else None, u=f.u, start=_utc(f.start_at))
            for f in rows]
    alerts = list((await db.execute(select(PredictiveAlert).where(
        PredictiveAlert.tenant_id == m.tenant_id, PredictiveAlert.monitor_id == m.id))).scalars().all())
    seg_alerts = {a.opened_window: a for a in alerts if a.baseline_id == reset.id}
    carry_db = next((a for a in alerts if a.baseline_id != reset.id and
                     (a.state != "closed" or (a.closed_at is not None and _utc(a.closed_at) > _utc(reset.reset_at)))
                     and _utc(a.opened_at) <= _utc(reset.reset_at)), None)
    key_of = {a.id: f"w{a.opened_window}" for a in seg_alerts.values()}
    carry = None
    if carry_db is not None:
        key_of[carry_db.id] = carry_db.id
        carry = core.CarryAlert(key=carry_db.id, state="open", severity=carry_db.severity,
                                opened_at=_utc(carry_db.opened_at), opened_window=carry_db.opened_window,
                                acknowledged=False, triggers_seen=dict(carry_db.triggers_seen or {}),
                                evidence=dict(carry_db.evidence or {}), escalations=dict(carry_db.escalations or {}),
                                escalation_windows=dict(carry_db.escalation_windows or {}),
                                first_triggers=list(carry_db.first_triggers or []))
        # replay the carried alert's events of before the reset to know its state at the segment start
        pre = (await db.execute(select(PredictiveAlertEvent).where(
            PredictiveAlertEvent.alert_id == carry_db.id, PredictiveAlertEvent.source.in_(EXT_SOURCES),
            PredictiveAlertEvent.at <= reset.reset_at).order_by(PredictiveAlertEvent.at))).scalars().all()
        for e in pre:
            if e.action == "acknowledge" and carry.state == "open":
                carry.state, carry.acknowledged = "acknowledged", True
            elif e.action == "convert":
                carry.state, carry.acknowledged = "converted", True
            elif e.action == "unconvert" and carry.state == "converted":
                carry.state = "acknowledged"
            elif e.action == "close":
                carry.state = "closed"
        if carry.state == "closed":
            carry = None
    ids = list(key_of)
    evs = []
    if ids:
        for e in (await db.execute(select(PredictiveAlertEvent).where(
                PredictiveAlertEvent.tenant_id == m.tenant_id, PredictiveAlertEvent.alert_id.in_(ids),
                PredictiveAlertEvent.source.in_(EXT_SOURCES), PredictiveAlertEvent.at > reset.reset_at)
                .order_by(PredictiveAlertEvent.at, PredictiveAlertEvent.recorded_at))).scalars():
            if e.action in ACTION_OF:
                evs.append(core.ExtEvent(at=_utc(e.at), alert_key=key_of[e.alert_id], action=e.action,
                                         resolution=e.resolution, source=e.source))
    # P-8 snooze: the last false_alarm of the (position, signal)
    fa = (await db.execute(select(PredictiveAlert).where(
        PredictiveAlert.tenant_id == m.tenant_id, PredictiveAlert.monitor_id == m.id,
        PredictiveAlert.resolution == "false_alarm", PredictiveAlert.closed_at <= reset.reset_at)
        .order_by(PredictiveAlert.closed_at.desc()).limit(1))).scalar_one_or_none()
    snooze = None
    if fa is not None:
        snooze = (_utc(fa.closed_at) + timedelta(days=ps.DEFAULTS["false_alarm_snooze_days"]), fa.severity)
    return rows, wins, seg_alerts, carry_db if carry is not None else None, carry, evs, snooze


async def fold(db: AsyncSession, m: PredictiveMonitor, reset: ConditionBaseline):
    rules = await ps.rules_of(db, m)
    label = await ps.position_name(db, m)
    cfg = ps.monitor_cfg(m, rules, label)
    tz = await ps.plant_tz(db, m.tenant_id, m.machine_id)
    rows, wins, seg_alerts, carry_db, carry, evs, snooze = await segment_inputs(db, m, reset)
    rate_fn = None
    plan_fn = None
    if wins:
        hourly = await hourly_usage(db, m, wins[0].end - timedelta(days=15), wins[-1].end + timedelta(hours=1))
        from ..usage.queries import data_since
        rate_fn = core.make_rate_fn(hourly, data_since=await data_since(db, m.tenant_id, m.position_id, m.machine_id))
        plan_fn = await plan_fn_for(db, m)
    res = core.fold_segment(cfg, wins, warmup=reset.warmup, tz=tz, rate_fn=rate_fn, plan_fn=plan_fn, events=evs,
                            carry=carry, snooze=snooze)
    return cfg, tz, rows, seg_alerts, carry_db, res


async def process_monitor(db: AsyncSession, m: PredictiveMonitor, now: Optional[datetime] = None,
                          do_extract: bool = True, transfer: bool = True) -> dict:
    now = now or clock.now()
    reset = await ps.latest_reset(db, m)
    if reset is None:
        reset = await ps.create_reset(db, m, _utc(m.anchor_at), "initial", True, "início do monitoramento")
    await ps.sync_resets(db, m, now)
    reset = await ps.latest_reset(db, m)
    if transfer:                      # run_once transfers once for every monitor
        await _flush_ingest_marks()
        await transfer_late(db, m.tenant_id)
    made = late = 0
    if do_extract and m.enabled:
        dirty = await take_dirty(db, m)
        try:
            if dirty is not None:
                late = await apply_dirty(db, m, reset, await _tags(db, m), *dirty)
            made = await extract(db, m, reset, now)
        except Exception:  # noqa: BLE001 — Influx down: keep the stored series (and the dirty range), retry next tick
            if dirty is not None:
                _put_back_dirty(db, m, *dirty)
            log.exception("predictive.extract_failed", monitor_id=m.id)
    await ps.failure_sweep(db, m, now)
    if (m.status or {}).get("rebuild_to_i"):
        # blocks removed because of late data are not all cut again yet (run limit / Influx error): folding a
        # truncated series would close the alerts of the missing blocks — keep everything as is until caught up
        summary = {"monitor_id": m.id, "points": None, "alerts": None, "opened": [], "escalated": [], "closed": [],
                   "mismatch": [], "unmatched_events": 0, "baseline_established": reset.established_at is not None,
                   "rebuilding": True}
    else:
        summary = await apply_fold(db, m, reset, now)
    summary["windows_added"] = made
    summary["late_windows"] = late
    m.processed_until = now
    return summary


def _alert_key_view(a: dict) -> dict:
    return {k: a.get(k) for k in ("opened_at", "opened_window", "first_triggers", "severity", "escalations",
                                  "escalation_windows", "state", "triggers_seen", "resolution", "closed_at",
                                  "closed_by")}


async def apply_fold(db: AsyncSession, m: PredictiveMonitor, reset: ConditionBaseline, now: datetime) -> dict:
    cfg, tz, rows, seg_alerts, carry_db, res = await fold(db, m, reset)
    # 1) points
    by_i = {p["i"]: p for p in res.points}
    for f in rows:
        p = by_i.get(f.i)
        if p is None:
            continue
        f.phase = p.get("phase") if p.get("valid") else "invalid"
        f.z, f.ewma, f.cusum = p.get("z"), p.get("ewma"), p.get("cusum")
        f.rolling_z, f.anomaly, f.slope = p.get("rolling_z"), p.get("anomaly"), p.get("slope")
        f.triggers, f.severity = p.get("triggers", []), p.get("severity")
        f.rul = p.get("rul")
    # 2) baseline
    b = res.baseline
    if b is not None:
        reset.m0, reset.mad, reset.sigma, reset.sigma_floor = b["m0"], b["mad"], b["sigma"], b["sigma_floor"]
        reset.from_i, reset.to_i, reset.n = b["from_i"], b["to_i"], b["n"]
        reset.established_at = b["established_at_dt"]
    else:
        reset.m0 = reset.mad = reset.sigma = reset.sigma_floor = reset.from_i = reset.to_i = reset.n = None
        reset.established_at = None
    # 3) alerts
    changes = {"opened": [], "escalated": [], "closed": [], "updated": [], "mismatch": []}
    seen_ids = set()
    for fa in res.alerts:
        if fa.get("carried"):
            a = carry_db
        else:
            a = seg_alerts.get(fa["opened_window"])
        point = fa.get("point")
        expl = core.explain(cfg, fa, point, core._public_base(b), tz, FEATURE_LABEL.get(m.feature, m.feature)) \
            if point else None
        if a is None:
            a = PredictiveAlert(tenant_id=m.tenant_id, monitor_id=m.id, baseline_id=reset.id, position_id=m.position_id,
                                machine_id=m.machine_id, feature=m.feature, state=fa["state"], severity=fa["severity"],
                                opened_at=ps.parse_iso(fa["opened_at"]), opened_window=fa["opened_window"],
                                first_triggers=fa["first_triggers"], escalations=fa["escalations"],
                                escalation_windows=fa["escalation_windows"], triggers_seen=fa["triggers_seen"],
                                evidence=fa["evidence"], explanation=expl, rul=fa.get("rul"),
                                last_point=_slim(point), acknowledged=False,
                                resolution=fa.get("resolution"), closed_at=ps.parse_iso(fa.get("closed_at")))
            try:
                async with db.begin_nested():
                    db.add(a)
                    await db.flush()
            except IntegrityError:
                log.warning("predictive.alert_conflict", monitor_id=m.id, window=fa["opened_window"])
                changes["mismatch"].append(fa["opened_window"])
                continue
            for ev in fa.get("auto", []):
                ps.add_event(db, a, ps.parse_iso(ev["at"]), "open" if ev["kind"] == "open" else ev["kind"], "engine",
                             to_state="open" if ev["kind"] == "open" else ("closed" if ev["kind"] == "auto_cleared"
                                                                           else None),
                             severity=ev.get("severity"), window_i=ev["i"],
                             resolution="auto_cleared" if ev["kind"] == "auto_cleared" else None)
            changes["opened"].append(a)
            if a.state == "closed":
                changes["closed"].append(a)
        else:
            seen_ids.add(a.id)
            old_state = a.state
            known = {(e.action, e.window_i) for e in (await db.execute(select(PredictiveAlertEvent).where(
                PredictiveAlertEvent.alert_id == a.id, PredictiveAlertEvent.source == "engine"))).scalars()}
            for ev in fa.get("auto", []):
                act = "open" if ev["kind"] == "open" else ev["kind"]
                if (act, ev["i"]) not in known:
                    ps.add_event(db, a, ps.parse_iso(ev["at"]), act, "engine", severity=ev.get("severity"),
                                 window_i=ev["i"], from_state=old_state if act == "auto_cleared" else None,
                                 to_state="closed" if act == "auto_cleared" else None,
                                 resolution="auto_cleared" if act == "auto_cleared" else None)
            if SEV_RANK[fa["severity"]] > SEV_RANK[a.severity]:
                a.severity = fa["severity"]
                changes["escalated"].append(a)
            a.escalations, a.escalation_windows = fa["escalations"], fa["escalation_windows"]
            a.triggers_seen, a.evidence = fa["triggers_seen"], fa["evidence"]
            if expl:
                a.explanation = expl
            if fa.get("rul") is not None:
                a.rul = fa["rul"]
            if point:
                a.last_point = _slim(point)
            if fa["state"] == "closed" and a.state != "closed" and fa.get("resolution") == "auto_cleared":
                a.state, a.resolution, a.closed_at = "closed", "auto_cleared", ps.parse_iso(fa["closed_at"])
                changes["closed"].append(a)
            elif fa["state"] != a.state:
                changes["mismatch"].append(a.opened_window)
            elif a not in changes["escalated"]:
                changes["updated"].append(a)
    # alerts of this segment the fold no longer produces (its inputs changed: a failure date corrected, late
    # data re-extracted): one that only the engine and failure closures touched was opened on stale inputs — it
    # is closed as `duplicate` (kept for audit, excluded from P-10); one a person or a WO acted on is reported.
    for a in seg_alerts.values():
        if a.id in seen_ids or a.resolution == "duplicate":
            continue
        srcs = set((await db.execute(select(PredictiveAlertEvent.source).where(
            PredictiveAlertEvent.tenant_id == m.tenant_id, PredictiveAlertEvent.alert_id == a.id))).scalars())
        if a.work_order_id or srcs - {"engine", "failure", "failure_void"}:
            changes["mismatch"].append(a.opened_window)
            continue
        frm = a.state
        a.state, a.resolution, a.closed_at = "closed", "duplicate", a.closed_at or now
        ps.add_event(db, a, now, "close", "engine", from_state=frm, to_state="closed", resolution="duplicate",
                     comment="recálculo: o alerta não se reproduz com os dados atuais (ex.: data da falha corrigida)")
        log.info("predictive.alert_superseded", alert_id=a.id, opened_window=a.opened_window)
        changes["closed"].append(a)
    await db.flush()
    # 4) condition plans (P-9) and notifications / websocket
    for a in changes["opened"] + changes["escalated"]:
        if a.state in ("open", "acknowledged"):
            plan = await ps.condition_plan(db, a)
            if plan is not None:
                min_sev = (plan.condition_trigger or {}).get("min_severity", "warning")
                if SEV_RANK.get(a.severity, 0) >= SEV_RANK.get(min_sev, 1):
                    reached = min((ps.parse_iso(t) for s_, t in (a.escalations or {}).items()
                                   if SEV_RANK[s_] >= SEV_RANK.get(min_sev, 1)), default=_utc(a.opened_at))
                    try:
                        async with db.begin_nested():
                            await ps.convert_alert(db, a, None, now, source="plan", at=reached)
                    except Exception:  # noqa: BLE001 — the alert stays open; the planner converts by hand
                        log.exception("predictive.condition_plan_failed", alert_id=a.id)
                    from ..maintenance.service import run_plan_engine
                    await run_plan_engine(db, m.tenant_id, plan_ids=[plan.id], now=now)
    for a in changes["opened"]:
        if a.state != "closed":
            await ps.notify_alert(db, a, "opened")
        await ps.queue_ws(db, a, "opened")
    for a in changes["escalated"]:
        await ps.notify_alert(db, a, "escalated")
        await ps.queue_ws(db, a, "escalated")
    for a in changes["closed"]:
        if a not in changes["opened"]:
            await ps.queue_ws(db, a, "closed")
    # 5) monitor status cache
    last = next((p for p in reversed(res.points) if p.get("valid")), None)
    open_a = (await db.execute(select(PredictiveAlert.id).where(
        PredictiveAlert.monitor_id == m.id, PredictiveAlert.state != "closed"))).scalar_one_or_none()
    st = dict(m.status or {})
    st.update(phase=res.state["phase"], learning=res.learning, ewma=res.state["ewma"], cusum=res.state["cusum"],
              ewma_limit=res.state["ewma_limit"], cusum_h=res.state["cusum_h"],
              baseline_id=reset.id, last=_slim(last), open_alert_id=open_a, evaluated_at=clock.iso(now))
    m.status = st
    await db.flush()
    return {"monitor_id": m.id, "points": len(res.points), "alerts": len(res.alerts),
            "opened": [a.id for a in changes["opened"]], "escalated": [a.id for a in changes["escalated"]],
            "closed": [a.id for a in changes["closed"]], "mismatch": changes["mismatch"],
            "unmatched_events": len(res.unmatched_events), "baseline_established": b is not None}


def _slim(p: Optional[dict]) -> Optional[dict]:
    if not p:
        return None
    return {k: v for k, v in p.items() if k != "baseline"}


# ── recompute (P-12) ──────────────────────────────────────────────────────────

async def _closed_by(db: AsyncSession, a: PredictiveAlert) -> Optional[str]:
    if a.state != "closed":
        return None
    if a.resolution == "auto_cleared":
        return "engine"
    return (await db.execute(select(PredictiveAlertEvent.source).where(
        PredictiveAlertEvent.tenant_id == a.tenant_id, PredictiveAlertEvent.alert_id == a.id,
        PredictiveAlertEvent.action == "close", PredictiveAlertEvent.source != "failure_void")
        .order_by(PredictiveAlertEvent.at.desc(), PredictiveAlertEvent.recorded_at.desc()).limit(1))
    ).scalar_one_or_none()


def _block_view(f: ConditionFeature) -> tuple:
    return (f.i, clock.iso(_utc(f.start_at)), clock.iso(_utc(f.end_at)), f.n, bool(f.valid), f.x)


async def reextract_cycles_full(db: AsyncSession, m: PredictiveMonitor, reset: ConditionBaseline, now: datetime
                                ) -> int:
    """Cuts every block of the segment again from Influx (as if the monitor had just been created) and returns how
    many blocks differ from the stored ones. Incremental extraction must give the same blocks (P-12)."""
    old = [_block_view(f) for f in (await db.execute(select(ConditionFeature).where(
        ConditionFeature.tenant_id == m.tenant_id, ConditionFeature.monitor_id == m.id,
        ConditionFeature.baseline_id == reset.id).order_by(ConditionFeature.i))).scalars()]
    if not old:
        return 0
    await rewind_cycles(db, m, reset, _utc(reset.reset_at) - timedelta(days=1))
    tags = await _tags(db, m)
    for _ in range(10_000):
        prev = await last_window(db, m)
        seg_prev = prev if (prev is not None and prev.baseline_id == reset.id) else None
        made = await extract_cycles(db, m, reset, now - GRACE, prev, seg_prev, tags)
        if made < MAX_WINDOWS_PER_RUN:
            break
    new = [_block_view(f) for f in (await db.execute(select(ConditionFeature).where(
        ConditionFeature.tenant_id == m.tenant_id, ConditionFeature.monitor_id == m.id,
        ConditionFeature.baseline_id == reset.id).order_by(ConditionFeature.i))).scalars()]
    old_by, new_by = {b[0]: b for b in old}, {b[0]: b for b in new}
    return sum(1 for i in set(old_by) | set(new_by) if old_by.get(i) != new_by.get(i))


async def recompute(db: AsyncSession, m: PredictiveMonitor, now: Optional[datetime] = None,
                    reextract_invalid: bool = True, reextract_all: bool = False) -> dict:
    """Recomputes the last baseline segment from its stored windows and the recorded events, and reports whether
    the alerts are the same (instants, evidence, and how/when/by whom they were closed). Nothing is deleted:
    alerts are matched by window.
    With `reextract_invalid`, the segment's invalid time windows are first re-read from Influx (readings that
    arrived late, e.g. a gateway buffer flushed after the window was extracted); with `reextract_all`, every
    window / cycle block of the segment is extracted again from Influx (full recompute). The windows that changed
    are reported and make `same` false only if they change the alerts."""
    now = now or clock.now()
    reset = await ps.latest_reset(db, m)
    if reset is None:
        return {"monitor_id": m.id, "same": True, "alerts": []}
    before = {}
    for a in (await db.execute(select(PredictiveAlert).where(
            PredictiveAlert.tenant_id == m.tenant_id, PredictiveAlert.monitor_id == m.id,
            PredictiveAlert.baseline_id == reset.id))).scalars():
        before[a.opened_window] = _db_alert_view(a) | {"closed_by": await _closed_by(db, a)}
    # closures by failure follow the current failure records (a corrected failed_at moves them)
    await ps.failure_sweep(db, m, now)
    reextracted = 0
    if m.tag_id and (reextract_invalid or reextract_all):
        if m.window_hours:
            tags = await _tags(db, m)
            if reextract_all:
                since = _utc(reset.reset_at) - timedelta(seconds=1)
                while True:
                    n, resume = await reextract_windows(db, m, reset, tags, since, None)
                    reextracted += n
                    if resume is None:
                        break
                    since = resume
            else:
                reextracted = await reextract_late(db, m, reset, tags,
                                                   since=_utc(reset.reset_at) - timedelta(seconds=1))
        elif reextract_all:
            reextracted = await reextract_cycles_full(db, m, reset, now)
    _, _, _, _, _, res = await fold(db, m, reset)
    after = {a["opened_window"]: _alert_key_view(a) | {"evidence": a["evidence"]}
             for a in res.alerts if not a.get("carried")}
    # an alert already superseded by an earlier recompute (closed `duplicate`, no longer produced by the fold)
    for w in [w for w, v in before.items() if v["resolution"] == "duplicate" and w not in after]:
        del before[w]
    diff = []
    for w in sorted(set(before) | set(after)):
        if before.get(w) != after.get(w):
            diff.append({"opened_window": w, "stored": before.get(w), "recomputed": after.get(w)})
    summary = await apply_fold(db, m, reset, now)
    return {"monitor_id": m.id, "baseline_id": reset.id, "same": not diff, "differences": diff,
            "windows_reextracted": reextracted,
            "alerts": [after[w] for w in sorted(after)], **{k: summary[k] for k in ("points",)}}


def _db_alert_view(a: PredictiveAlert) -> dict:
    return {"opened_at": core._iso(_utc(a.opened_at)), "opened_window": a.opened_window,
            "first_triggers": a.first_triggers, "severity": a.severity, "escalations": a.escalations,
            "escalation_windows": a.escalation_windows, "state": a.state, "triggers_seen": a.triggers_seen,
            "resolution": a.resolution, "closed_at": core._iso(_utc(a.closed_at)) if a.closed_at else None,
            "evidence": a.evidence}


async def on_failure_changed(db: AsyncSession, tenant_id: str, position_ids, now: Optional[datetime] = None) -> int:
    """A failure was registered or edited (failed_at, severity, position, detection): the predictive alerts of the
    monitors at the positions involved are re-evaluated now (P-8: closure `failed_before_action` follows the current
    failure records — moved, reopened or closed) and refolded, instead of waiting for the next engine tick. A monitor
    the engine is processing right now is skipped (its own run re-reads the failures after extracting)."""
    ids = sorted({p for p in position_ids if p})
    if not ids:
        return 0
    now = now or clock.now()
    mons = (await db.execute(select(PredictiveMonitor).where(
        PredictiveMonitor.tenant_id == tenant_id, PredictiveMonitor.position_id.in_(ids))
        .order_by(PredictiveMonitor.id).with_for_update(skip_locked=True))).scalars().all()
    n = 0
    for m in mons:
        reset = await ps.latest_reset(db, m)
        if reset is None:
            continue
        changed = await ps.failure_sweep(db, m, now)
        if not (m.status or {}).get("rebuild_to_i"):
            await apply_fold(db, m, reset, now)
        n += 1 if changed else 0
    return n


# ── background loop ───────────────────────────────────────────────────────────

async def run_once(now: Optional[datetime] = None) -> int:
    now = now or clock.now()
    try:
        await _flush_ingest_marks()
        async with AsyncSessionLocal() as db:
            await transfer_late(db)
            await db.commit()
    except Exception:  # noqa: BLE001 — the marks stay in ingest_watermarks for the next run
        log.exception("predictive.transfer_late_failed")
    async with AsyncSessionLocal() as db:
        ids = [(i, t) for i, t in (await db.execute(select(PredictiveMonitor.id, PredictiveMonitor.tenant_id).where(
            PredictiveMonitor.enabled.is_(True)))).all()]
    # Sprint 7: monitors run with bounded concurrency and a per-monitor timeout — a monitor catching up a backlog
    # (bounded by MAX_WINDOWS_PER_RUN per run, resumed from its cursor next tick) never delays the other monitors /
    # tenants; interleaved by tenant so one big tenant does not take every slot first.
    by_t: dict[str, list[str]] = {}
    for mid, tid in ids:
        by_t.setdefault(tid, []).append(mid)
    order: list[tuple[str, str]] = []
    while any(by_t.values()):
        for tid, q in by_t.items():
            if q:
                order.append((q.pop(0), tid))
    sem = asyncio.Semaphore(max(1, int(getattr(settings, "detector_concurrency", 4))))
    timeout = float(getattr(settings, "predictive_monitor_timeout_s", 240.0))

    async def one(mid: str, tid: str) -> int:
        async with sem:
            async with AsyncSessionLocal() as db:
                try:
                    async def work() -> int:
                        # same row lock as POST /monitors/{id}/process: one pipeline run per monitor at a time
                        m = (await db.execute(select(PredictiveMonitor).where(
                            PredictiveMonitor.id == mid, PredictiveMonitor.tenant_id == tid).with_for_update())
                             ).scalar_one_or_none()
                        if m is None:
                            return 0
                        await process_monitor(db, m, now, transfer=False)
                        await db.commit()
                        await ps.deliver(db)
                        return 1
                    return await asyncio.wait_for(work(), timeout=timeout)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — one monitor never stops the others (timeout: resumed next tick)
                    await db.rollback()
                    ps.discard_pending(db)
                    log.exception("predictive.monitor_failed", monitor_id=mid)
                    return 0

    return sum(await asyncio.gather(*(one(mid, tid) for mid, tid in order)))


async def run_predictive_engine() -> None:
    interval = float(getattr(settings, "predictive_tick_s", 300.0))
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("predictive.engine_error")
        await asyncio.sleep(interval)
