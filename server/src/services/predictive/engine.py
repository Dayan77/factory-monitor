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
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db import AsyncSessionLocal
from ...models import (ConditionBaseline, ConditionFeature, Position, PredictiveAlert,
                       PredictiveAlertEvent, PredictiveMonitor, ProductionPlanDay, Tag, UsageBucket)
from ...models.tag import CYCLE_ROLES, SemanticRole
from .. import clock
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
                       coverage: Optional[float] = None, i: Optional[int] = None) -> ConditionFeature:
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
                         u=u, running_h=running_h, coverage=coverage, triggers=[])
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
    return fx.time_window_feature(pts.get(m.tag_id, []), pts.get(tags["run_state"]) if tags["run_state"] else None,
                                  start, end, pres, pts.get(m.ambient_tag_id) if m.ambient_tag_id else None)


# ── late data (gateway store-and-forward) ─────────────────────────────────────
# The MQTT handler notes readings that reached Influx after the engine may already have extracted their window
# (`note_late`, in memory, like the downtime detector's); each engine run persists the notes into the monitors'
# status (`late_from`) and re-extracts the stored *invalid* time windows of the current baseline segment that the
# late readings touch. Valid windows are never rewritten (their points already fed alerts and notifications);
# cycle blocks need no note: their cursor never skips ahead of the data (extract_cycles).

_late: dict[tuple[str, str], datetime] = {}


def note_late(tenant_id: str, machine_id: str, ts: datetime) -> None:
    k = (tenant_id, machine_id)
    cur = _late.get(k)
    if cur is None or ts < cur:
        _late[k] = ts


def late_threshold() -> timedelta:
    """A reading older than this when it reaches Influx may belong to a window already extracted."""
    return GRACE


async def flush_late(db: AsyncSession) -> int:
    """Moves the in-memory late notes into `status.late_from` of the machines' time-window monitors."""
    items = list(_late.items())
    _late.clear()
    n = 0
    for (tid, mid), ts in items:
        mons = (await db.execute(select(PredictiveMonitor).where(
            PredictiveMonitor.tenant_id == tid, PredictiveMonitor.machine_id == mid,
            PredictiveMonitor.window_hours.is_not(None)).with_for_update())).scalars().all()
        for m in mons:
            mark_late(m, ts)
            n += 1
    await db.flush()
    return n


def mark_late(m: PredictiveMonitor, ts: datetime) -> None:
    st = dict(m.status or {})
    old = ps.parse_iso(st.get("late_from"))
    if old is None or _utc(ts) < old:
        st["late_from"] = clock.iso(_utc(ts))
        m.status = st


async def reextract_late(db: AsyncSession, m: PredictiveMonitor, reset: ConditionBaseline, tags: dict,
                         since: Optional[datetime] = None) -> int:
    """Re-extracts the invalid windows of the current segment that end after `since` (default: the monitor's
    `late_from` note). A window that becomes valid gets its point; its running hours change the usage `u` of every
    later window by the same amount. Returns how many windows changed."""
    st = dict(m.status or {})
    since = since or ps.parse_iso(st.get("late_from"))
    if since is None:
        return 0
    rows = list((await db.execute(select(ConditionFeature).where(
        ConditionFeature.tenant_id == m.tenant_id, ConditionFeature.monitor_id == m.id,
        ConditionFeature.baseline_id == reset.id, ConditionFeature.valid.is_(False),
        ConditionFeature.end_at > since).order_by(ConditionFeature.i).limit(MAX_WINDOWS_PER_RUN + 1))).scalars().all())
    more = len(rows) > MAX_WINDOWS_PER_RUN
    rows = rows[:MAX_WINDOWS_PER_RUN]
    changed = 0
    for f in rows:
        wf = await _time_window(m, tags, _utc(f.start_at), _utc(f.end_at))
        if wf.valid == bool(f.valid) and (wf.running_h or 0.0) == (f.running_h or 0.0) and wf.n == f.n:
            continue
        delta = (wf.running_h or 0.0) - (f.running_h or 0.0)
        f.x = wf.x if wf.valid else None
        f.valid, f.n, f.running_h, f.coverage = bool(wf.valid and wf.x is not None), wf.n, wf.running_h, wf.coverage
        if delta:
            later = (await db.execute(select(ConditionFeature).where(
                ConditionFeature.tenant_id == m.tenant_id, ConditionFeature.monitor_id == m.id,
                ConditionFeature.i >= f.i))).scalars().all()
            for g in later:
                g.u = (g.u or 0.0) + delta
        changed += 1
    st = dict(m.status or {})
    if more:
        st["late_from"] = clock.iso(_utc(rows[-1].end_at))
    else:
        st.pop("late_from", None)
    m.status = st
    await db.flush()
    if changed:
        log.info("predictive.late_windows", monitor_id=m.id, changed=changed)
    return changed


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
        made += await reextract_late(db, m, reset, tags)
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


async def _counter_at(m: PredictiveMonitor, counter: str, at: datetime) -> Optional[float]:
    """Counter value held at `at` (the reading AT `at` included: it is the one that closed the previous block)."""
    got = await fx.last_before(m.tenant_id, [counter], at + _US, COUNTER_LOOKBACK)
    return got.get(counter, (None, None))[1]


async def _next_close(m: PredictiveMonitor, tags: dict, after: datetime, horizon: datetime, carry: float
                      ) -> Optional[datetime]:
    """Instant at which the next block of `window_cycles` closes after `after` (None: still open, or no data).
    Answered with aggregates computed inside Influx, so an open block costs a couple of small queries per tick
    instead of re-reading (and parsing) every raw reading since the last block end."""
    n_block = m.window_cycles
    counter = tags["counter"]
    if not counter:
        probe = await fx.tag_probe(m.tenant_id, m.tag_id, after, horizon)
        if probe["count"] < n_block:
            return None
        return await fx.nth_reading_time(m.tenant_id, m.tag_id, after, horizon, n_block)
    probe = await fx.tag_probe(m.tenant_id, counter, after, horizon)
    if probe["first"] is None:
        return None
    pv = await _counter_at(m, counter, after)
    ref = pv if pv is not None else probe["first"][1]
    if probe["drops"] or probe["first"][1] < ref:
        # a reset/rollover inside the open block (rare): walk the counter readings themselves
        pts = (await fx.influx_points(m.tenant_id, [counter], after, horizon)).get(counter, [])
        cum = fx.cumulative_cycles([p for p in pts if p[0] > after], tags["counter_max"], pv)
        return next((t for t, c in cum if c + carry >= n_block), None)
    if probe["last"][1] - ref + carry < n_block:
        return None
    return await fx.first_reaching(m.tenant_id, counter, after, horizon, ref + n_block - carry)


async def extract_cycles(db: AsyncSession, m: PredictiveMonitor, reset: ConditionBaseline, horizon: datetime,
                         prev: Optional[ConditionFeature], seg_prev: Optional[ConditionFeature], tags: dict) -> int:
    """Blocks of N cycles (P-1: block i = cycles (i−1)·N+1 … i·N since the reset), resumed from the cursor = the
    instant of the reading that closed the last block (and the cycles it overshot, `carry`).

    * The counter reading AT the cursor belongs to the previous block: it is the reference value (`prev_value`)
      and is not counted again (otherwise every resumed block closes one cycle early).
    * Nothing is skipped: while no block closes, the cursor stays at the last block end, so readings that arrive
      late (gateway store-and-forward flush, hours after the fact) still land in the right block."""
    made = 0
    st = dict(m.status or {})
    cur = st.get("cycle_cursor") if (st.get("cycle_cursor") or {}).get("baseline_id") == reset.id else None
    after = ps.parse_iso(cur["end_at"]) if cur else _utc(reset.reset_at)
    carry = float(cur["carry"]) if cur else 0.0
    k0 = (seg_prev.i + 1) if seg_prev else ((prev.i + 1) if prev else 1)
    counter = tags["counter"]
    while after < horizon and made < MAX_WINDOWS_PER_RUN:
        close_at = await _next_close(m, tags, after, horizon, carry)
        if close_at is None:
            break
        # read the raw readings once a block closes (2 days at a time when catching up)
        stop = min(horizon, max(close_at + _US, after + timedelta(days=2)))
        ids = [m.tag_id] + ([counter] if counter else [])
        pts = await fx.influx_points(m.tenant_id, ids, after, stop)
        cum = None
        if counter:
            pv = await _counter_at(m, counter, after)
            cum = fx.cumulative_cycles([p for p in pts.get(counter, []) if p[0] > after], tags["counter_max"], pv)
        blocks, cursor = fx.cycle_blocks(pts.get(m.tag_id, []), m.window_cycles, after, k0, cum, carry)
        for blk in blocks:
            await store_window(db, m, reset, start=blk.start, end=blk.end, x=blk.x, n=blk.n, valid=blk.valid, i=blk.k)
            made += 1
        if not cursor:
            log.warning("predictive.cycle_probe_mismatch", monitor_id=m.id, after=clock.iso(after))
            break
        after, carry, k0 = cursor["end_at"], cursor["carry"], k0 + len(blocks)
        st["cycle_cursor"] = {"baseline_id": reset.id, "end_at": clock.iso(after), "carry": carry}
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
                                         resolution=e.resolution))
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
        rate_fn = core.make_rate_fn(hourly)
        plan_fn = await plan_fn_for(db, m)
    res = core.fold_segment(cfg, wins, warmup=reset.warmup, tz=tz, rate_fn=rate_fn, plan_fn=plan_fn, events=evs,
                            carry=carry, snooze=snooze)
    return cfg, tz, rows, seg_alerts, carry_db, res


async def process_monitor(db: AsyncSession, m: PredictiveMonitor, now: Optional[datetime] = None,
                          do_extract: bool = True) -> dict:
    now = now or clock.now()
    reset = await ps.latest_reset(db, m)
    if reset is None:
        reset = await ps.create_reset(db, m, _utc(m.anchor_at), "initial", True, "início do monitoramento")
    await ps.sync_resets(db, m, now)
    reset = await ps.latest_reset(db, m)
    made = 0
    noted = _late.get((m.tenant_id, m.machine_id))
    if noted is not None and m.window_hours:
        mark_late(m, noted)          # the note itself stays for the machine's other monitors (flush_late)
    if do_extract and m.enabled:
        try:
            made = await extract(db, m, reset, now)
        except Exception:  # noqa: BLE001 — Influx down: keep the stored series, retry next tick
            log.exception("predictive.extract_failed", monitor_id=m.id)
    await ps.failure_sweep(db, m, now)
    summary = await apply_fold(db, m, reset, now)
    summary["windows_added"] = made
    m.processed_until = now
    return summary


def _alert_key_view(a: dict) -> dict:
    return {k: a.get(k) for k in ("opened_at", "opened_window", "first_triggers", "severity", "escalations",
                                  "escalation_windows", "state", "triggers_seen", "resolution", "closed_at")}


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

async def recompute(db: AsyncSession, m: PredictiveMonitor, now: Optional[datetime] = None,
                    reextract_invalid: bool = True) -> dict:
    """Recomputes the last baseline segment from its stored windows and the recorded events, and reports whether
    the alerts are the same (instants and evidence). Nothing is deleted: alerts are matched by window.
    With `reextract_invalid`, the segment's invalid time windows are first re-read from Influx (readings that
    arrived late, e.g. a gateway buffer flushed after the window was extracted); the windows that changed are
    reported and make `same` false only if they change the alerts."""
    now = now or clock.now()
    reset = await ps.latest_reset(db, m)
    if reset is None:
        return {"monitor_id": m.id, "same": True, "alerts": []}
    before = {a.opened_window: _db_alert_view(a) for a in (await db.execute(select(PredictiveAlert).where(
        PredictiveAlert.tenant_id == m.tenant_id, PredictiveAlert.monitor_id == m.id,
        PredictiveAlert.baseline_id == reset.id))).scalars()}
    reextracted = 0
    if reextract_invalid and m.window_hours and m.tag_id:
        reextracted = await reextract_late(db, m, reset, await _tags(db, m),
                                           since=_utc(reset.reset_at) - timedelta(seconds=1))
    _, _, _, _, _, res = await fold(db, m, reset)
    after = {a["opened_window"]: _alert_key_view(a) | {"evidence": a["evidence"]}
             for a in res.alerts if not a.get("carried")}
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


# ── background loop ───────────────────────────────────────────────────────────

async def run_once(now: Optional[datetime] = None) -> int:
    now = now or clock.now()
    if _late:
        async with AsyncSessionLocal() as db:
            try:
                await flush_late(db)
                await db.commit()
            except Exception:  # noqa: BLE001 — the notes are lost; /recompute re-extracts invalid windows too
                await db.rollback()
                log.exception("predictive.flush_late_failed")
    async with AsyncSessionLocal() as db:
        ids = [(i, t) for i, t in (await db.execute(select(PredictiveMonitor.id, PredictiveMonitor.tenant_id).where(
            PredictiveMonitor.enabled.is_(True)))).all()]
    n = 0
    for mid, tid in ids:
        async with AsyncSessionLocal() as db:
            try:
                # same row lock as POST /monitors/{id}/process: one pipeline run per monitor at a time
                m = (await db.execute(select(PredictiveMonitor).where(
                    PredictiveMonitor.id == mid, PredictiveMonitor.tenant_id == tid).with_for_update())
                     ).scalar_one_or_none()
                if m is None:
                    continue
                await process_monitor(db, m, now)
                await db.commit()
                await ps.deliver(db)
                n += 1
            except Exception:  # noqa: BLE001 — one monitor never stops the others
                await db.rollback()
                ps.discard_pending(db)
                log.exception("predictive.monitor_failed", monitor_id=mid)
    return n


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
