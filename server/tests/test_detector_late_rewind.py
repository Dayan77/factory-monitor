"""Sprint 3 review: downtime detector vs. late / out-of-order data and long stops.

* a reading that reaches Influx after the checkpoint passed it (gateway buffer flush) is noted by the
  MQTT path (`note_late` → `late_from`); the next tick rewinds to a snapshot and re-detects exactly —
  events, micro-stop buckets (restored from the snapshot, never double counted) and spans;
* a machine stopped for 24 h is not re-read from the start of its stop on every tick;
* the shift close-out (UNK-01) never overwrites a justification committed meanwhile.
"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

from oee_helpers import SCENARIO_C, close, scenario_c_readings, setup_scenario_c, ts
from test_oee_api import L, DAY, _plant_with_tags, _tenant
from oee_helpers import add_shift

C = SCENARIO_C
X = C["expected"]
W0 = datetime(2030, 1, 1, tzinfo=timezone.utc)          # wall clock of the ticks (injected)


async def _tick(mid, tid, visible, now, wall, counter=None):
    from src.db import AsyncSessionLocal
    from src.services.oee.detector import detect_machine
    from src.services.oee.engine import load_calendar, machine_infos

    async def fetch(_tid, tag_ids, after, stop):
        out = sorted(((tag, at, float(v)) for tag, v, at in visible if tag in tag_ids and after < at <= stop),
                     key=lambda r: (r[1], r[0]))
        if counter is not None:
            counter.append(len(out))
        return out

    async with AsyncSessionLocal() as s:
        info = (await machine_infos(s, tid, [mid]))[mid]
        cal = await load_calendar(s, tid, info.plant_id, info.tz)
        return await detect_machine(s, info, cal, now, fetch, wall=wall)


async def _state(mid):
    from src.db import AsyncSessionLocal
    from src.models import DowntimeDetectorState
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(DowntimeDetectorState).where(
            DowntimeDetectorState.machine_id == mid))).scalar_one()


async def _micro(mid):
    from src.db import AsyncSessionLocal
    from src.models import MicroStopBucket
    async with AsyncSessionLocal() as s:
        n, loss = (await s.execute(select(func.coalesce(func.sum(MicroStopBucket.count), 0),
                                          func.coalesce(func.sum(MicroStopBucket.loss_s), 0.0))
                                   .where(MicroStopBucket.machine_id == mid))).one()
        return int(n), float(loss)


async def _events(client, t, mid):
    from usage_helpers import api
    start, end = ts(C["shift"]["start_utc"]), ts(C["shift"]["end_utc"])
    evs = await api(client, "GET", "/api/downtime/", t.headers("operator"),
                    params={"machine_id": mid, "start": start.isoformat(), "stop": end.isoformat()})
    return sorted((e["start"], e["duration_s"]) for e in evs)


async def test_buffer_flush_after_checkpoint_rewinds_and_redetects_exactly(client):
    from src.db import AsyncSessionLocal
    from src.services.oee import detector
    t = await _tenant("REWIND")
    p, tags, _shift, _prod = await setup_scenario_c(client, t)
    mid = p.machine["id"]
    rows = scenario_c_readings(tags)
    start, end = ts(C["shift"]["start_utc"]), ts(C["shift"]["end_utc"])
    # the run_state of 08:25–10:15 local (the 08:30 MAT-01 stop and the 10:05 micro-stop) sits in the
    # gateway buffer while everything else is live
    a, b = ts("2026-10-06T12:25:00Z"), ts("2026-10-06T14:15:00Z")
    late = [r for r in rows if r[0] == tags["run"]["id"] and a <= r[2] < b]
    live = [r for r in rows if r not in late]

    # live ticks every 30 min of data, one wall minute apart: the checkpoint follows (snapshots taken)
    k, wall = 1, W0
    while start + timedelta(minutes=30 * k) <= end + timedelta(minutes=30):
        now = start + timedelta(minutes=30 * k)
        await _tick(mid, t.id, [r for r in live if r[2] <= now], now, wall)
        k, wall = k + 1, wall + timedelta(minutes=1)
    now = end + timedelta(hours=1)
    await _tick(mid, t.id, live, now, wall)
    await _tick(mid, t.id, live, now, wall + timedelta(minutes=1))
    st = await _state(mid)
    assert st.checkpoint > b                                 # the checkpoint passed the buffered readings
    assert len(await _events(client, t, mid)) == 5           # the 08:30 stop is not known yet

    # the buffer is flushed: the MQTT handler notes the late readings, the next tick rewinds
    for _tag, _v, at in late:
        detector.note_late(t.id, mid, at)
    async with AsyncSessionLocal() as s:
        assert await detector.flush_late(s, now) == 1
    res = await _tick(mid, t.id, rows, now, wall + timedelta(minutes=2))
    assert res.rewound
    got = await _events(client, t, mid)
    assert sorted(d for _, d in got) == sorted(e["duration_s"] for e in X["downtime_events"])
    n, loss = await _micro(mid)
    assert n == X["micro_stop_count"] and close(loss, X["micro_stop_s"])

    # further ticks (checkpoint moving again) change nothing: micro-stops counted exactly once
    for i in range(3, 6):
        await _tick(mid, t.id, rows, now + timedelta(seconds=10 * i), wall + timedelta(minutes=i))
    n2, loss2 = await _micro(mid)
    assert n2 == X["micro_stop_count"] and close(loss2, X["micro_stop_s"])
    assert sorted(d for _, d in await _events(client, t, mid)) == sorted(e["duration_s"] for e in X["downtime_events"])
    assert (await _state(mid)).checkpoint > b


async def test_out_of_order_transition_inside_settle_window_is_seen(client):
    """A run_state=0 that lands in Influx after a later heartbeat (concurrent MQTT handling) but within
    downtime_settle_s is read again by the next tick — the checkpoint had not passed it."""
    t = await _tenant("OOO")
    p, tags, _shift, _prod = await setup_scenario_c(client, t)
    mid = p.machine["id"]
    rows = scenario_c_readings(tags)
    now = ts(C["shift"]["end_utc"]) + timedelta(hours=1)
    a, b = ts("2026-10-06T12:30:00Z"), ts("2026-10-06T12:36:00Z")
    late = [r for r in rows if r[0] == tags["run"]["id"] and a <= r[2] < b]
    await _tick(mid, t.id, [r for r in rows if r not in late], now, W0)
    await _tick(mid, t.id, rows, now, W0 + timedelta(seconds=5))     # < settle: no rewind needed
    got = await _events(client, t, mid)
    assert sorted(d for _, d in got) == sorted(e["duration_s"] for e in X["downtime_events"])


async def test_long_open_stop_is_not_reread_from_its_start(client):
    t = await _tenant("LONGSTOP")
    p, run, fault = await _plant_with_tags(client, t, "LS")
    mid = p.machine["id"]
    # (inside the 24 h initial lookback): running, then stopped for 22 h with heartbeats every 30 s
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=23)
    stop_at = t0 + timedelta(minutes=30)
    rows, cur = [], t0
    while cur <= stop_at + timedelta(hours=22):
        rows.append((run["id"], 1.0 if cur < stop_at else 0.0, cur))
        rows.append((fault["id"], 0.0, cur))
        cur += timedelta(seconds=30)
    now = stop_at + timedelta(hours=22)
    counts: list[int] = []
    await _tick(mid, t.id, rows, now, W0, counts)
    assert counts[-1] > 5000                                     # first pass reads the history
    await _tick(mid, t.id, rows, now, W0 + timedelta(minutes=1), counts)
    st = await _state(mid)
    assert st.checkpoint > stop_at + timedelta(hours=21)          # inside the open stop
    assert (st.state or {}).get("down_since") == stop_at.isoformat()
    for i in range(1, 4):                                         # 10 s ticks while it stays stopped
        nxt = now + timedelta(seconds=30 * i)
        rows += [(run["id"], 0.0, nxt), (fault["id"], 0.0, nxt)]
        await _tick(mid, t.id, rows, nxt, W0 + timedelta(minutes=1 + i), counts)
        assert counts[-1] <= 10, counts
    # the machine restarts: one event from the real start of the stop
    back = now + timedelta(minutes=5)
    hb = now + timedelta(seconds=120)
    while hb < back:
        rows += [(run["id"], 0.0, hb), (fault["id"], 0.0, hb)]
        hb += timedelta(seconds=30)
    rows += [(run["id"], 1.0, back), (fault["id"], 0.0, back),
             (run["id"], 1.0, back + timedelta(seconds=30)), (fault["id"], 0.0, back + timedelta(seconds=30))]
    await _tick(mid, t.id, rows, back + timedelta(minutes=1), W0 + timedelta(minutes=10), counts)
    assert counts[-1] <= 30
    from src.db import AsyncSessionLocal
    from src.models import DowntimeEvent
    async with AsyncSessionLocal() as s:
        evs = (await s.execute(select(DowntimeEvent).where(DowntimeEvent.machine_id == mid)
                               .order_by(DowntimeEvent.start))).scalars().all()
    # default 24 h calendar: one event per productive day, all from the same stop
    assert evs and all(e.stop_start == stop_at for e in evs)
    assert evs[0].start == stop_at and evs[-1].end == back


async def test_shift_close_out_never_overwrites_a_concurrent_justification(client):
    from src.db import AsyncSessionLocal
    from src.models import DowntimeEvent
    from src.services.oee.detector import TickResult, _close_out_shifts
    from src.services.oee.engine import load_calendar, machine_infos
    from src.config import settings
    t = await _tenant("RACE")
    p, run, fault = await _plant_with_tags(client, t, "RC")
    mid = p.machine["id"]
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00")
    async with AsyncSessionLocal() as s:
        ev = DowntimeEvent(tenant_id=t.id, machine_id=mid, start=L(DAY, "08:30"), end=L(DAY, "08:40"),
                           duration_s=600, source="auto_run_state", planned=False, productive_day=DAY)
        s.add(ev)
        await s.commit()
        eid = ev.id

    def justify_in_other_connection():
        async def go():
            from sqlalchemy.ext.asyncio import create_async_engine
            from sqlalchemy.pool import NullPool
            eng = create_async_engine(settings.database_url, poolclass=NullPool)
            async with eng.begin() as conn:
                await conn.execute(text("UPDATE downtime_events SET reason_code = 'MAT-01', "
                                        "justified_at = now() WHERE id = :id"), {"id": eid})
            await eng.dispose()
        th = threading.Thread(target=lambda: asyncio.run(go()))
        th.start()
        th.join()

    async with AsyncSessionLocal() as s:
        info = (await machine_infos(s, t.id, [mid]))[mid]
        cal = await load_calendar(s, t.id, info.plant_id, info.tz)
        orig = cal.day_instances
        fired = []

        def day_instances(day):              # runs between the detector's SELECT and its UPDATE
            if not fired:
                fired.append(1)
                justify_in_other_connection()
            return orig(day)
        cal.day_instances = day_instances
        res = TickResult()
        await _close_out_shifts(s, info, cal, L(DAY, "15:00"), res)
        await s.commit()
    assert fired and res.messages == []
    async with AsyncSessionLocal() as s:
        ev = await s.get(DowntimeEvent, eid)
        assert ev.reason_code == "MAT-01"


async def test_mqtt_handler_notes_late_signal_readings(client):
    """The real MQTT handler marks a run_state written long after its timestamp; flushing creates the
    cursor of a machine never detected before so the backfill is read from before it."""
    from src.db import AsyncSessionLocal
    from src.services.oee import detector
    from usage_helpers import publish
    t = await _tenant("NOTE")
    p, run, fault = await _plant_with_tags(client, t, "NT")
    mid = p.machine["id"]
    detector._late.clear()
    real_now = datetime.now(timezone.utc)
    at = real_now - timedelta(days=3)                     # older than the 24 h initial lookback
    await publish(t.id, run["id"], 1, real_now - timedelta(seconds=3), received_at=real_now)   # live
    assert (t.id, mid) not in detector._late
    await publish(t.id, run["id"], 0, at, received_at=real_now)                                # buffered
    assert detector._late[(t.id, mid)] == at
    async with AsyncSessionLocal() as s:
        await detector.flush_late(s, real_now)
    assert (await _state(mid)).checkpoint < at
