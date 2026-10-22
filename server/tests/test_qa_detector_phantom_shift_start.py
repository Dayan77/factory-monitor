"""QA (Sprint 3 gate): phantom downtime event at shift start after an off-shift stop.

Found replaying golden scenario D through MQTT (e2e/scenario_d_replay.py): the output machine is stopped
overnight (off-shift), restarts exactly when the shift begins, and the server keeps an extra UNK-01 event
from the shift start (2010 s on 10-11 22:00, 4320 s on 10-13 06:00) that is not a stop at all.

Mechanism reproduced here: while the off-shift stop is still open, one tick sees it run past the shift start
(the restart reading is not in Influx yet) and creates the in-shift event of that open stop. The restart
reading arrives (before the checkpoint, which sits inside the open stop since the 22-h optimisation), the
stop now ends before the shift — but `_drop_stale_events` only looks at events whose `stop_start` is after
the checkpoint, and this event's `stop_start` (the start of the overnight stop) is before it. The phantom
event was never removed. Fixed: `_drop_stale_events` also re-derives every segment of the stop the checkpoint
resumes (`stop_start == down_since`).
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from oee_helpers import add_shift
from test_detector_late_rewind import W0, _tick
from test_oee_api import DAY, L, _plant_with_tags, _tenant


async def test_offshift_stop_ending_at_shift_start_leaves_no_event(client):
    from src.db import AsyncSessionLocal
    from src.models import DowntimeEvent
    from src.services.oee import detector

    t = await _tenant("PHANTOM")
    p, run, fault = await _plant_with_tags(client, t, "PH")
    mid = p.machine["id"]
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00")

    stop_at, back = L(DAY, "04:00"), L(DAY, "05:59:50")       # stopped off-shift, restarts before 06:00
    rows, cur = [], L(DAY, "03:30")
    while cur <= L(DAY, "06:10"):
        on = 1.0 if (cur < stop_at or cur >= back) else 0.0
        rows += [(run["id"], on, cur), (fault["id"], 0.0, cur)]
        if cur < back <= cur + timedelta(seconds=30):
            rows += [(run["id"], 1.0, back), (fault["id"], 0.0, back)]
        cur += timedelta(seconds=30)

    # live ticks; at 06:00:30 the restart reading (05:59:50) is still in flight (gateway/broker delay)
    await _tick(mid, t.id, [r for r in rows if r[2] <= L(DAY, "05:00")], L(DAY, "05:00"), W0)
    t1 = L(DAY, "06:00:30")
    await _tick(mid, t.id, [r for r in rows if r[2] < back], t1, W0 + timedelta(minutes=1))
    async with AsyncSessionLocal() as s:
        early = (await s.execute(select(DowntimeEvent).where(DowntimeEvent.machine_id == mid))).scalars().all()
    assert [e.start for e in early] == [L(DAY, "06:00")]          # open stop seen past the shift start

    # the restart reading lands (the real MQTT handler would note it as late; the checkpoint is before it)
    for tag, _v, at in rows:
        if at >= back:
            detector.note_late(t.id, mid, at)
    async with AsyncSessionLocal() as s:
        await detector.flush_late(s, L(DAY, "06:05"))
    for i, now in enumerate((L(DAY, "06:05"), L(DAY, "06:10"))):
        await _tick(mid, t.id, [r for r in rows if r[2] <= now], now, W0 + timedelta(minutes=5 + i))

    async with AsyncSessionLocal() as s:
        evs = (await s.execute(select(DowntimeEvent).where(DowntimeEvent.machine_id == mid))).scalars().all()
    # expected: the stop 04:00–05:59:50 lies entirely outside the shift → no downtime event at all
    assert [(e.start, e.end, e.duration_s, e.stop_start) for e in evs] == []


async def _run_case(client, code, back, ticks_before, final_ticks):
    """Machine stopped 04:00 (off-shift, shift 06:00–14:00) until `back` (None = never restarts).
    Ticks at `ticks_before` see readings < `back` only; then the restart lands late and `final_ticks` run."""
    from src.db import AsyncSessionLocal
    from src.models import DowntimeEvent
    from src.services.oee import detector

    t = await _tenant(code)
    p, run, fault = await _plant_with_tags(client, t, code[:2])
    mid = p.machine["id"]
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00")
    stop_at, end = L(DAY, "04:00"), L(DAY, "07:00")
    rows, cur = [], L(DAY, "03:30")
    while cur <= end:
        on = 1.0 if (cur < stop_at or (back is not None and cur >= back)) else 0.0
        rows += [(run["id"], on, cur), (fault["id"], 0.0, cur)]
        cur += timedelta(seconds=30)
    hidden = back or end + timedelta(days=1)
    w = W0
    for now in ticks_before:
        w += timedelta(minutes=1)
        await _tick(mid, t.id, [r for r in rows if r[2] <= now and r[2] < hidden], now, w)
    if back is not None:
        for _tag, _v, at in rows:
            if at >= back:
                detector.note_late(t.id, mid, at)
        async with AsyncSessionLocal() as s:
            await detector.flush_late(s, final_ticks[0])
    for now in final_ticks:
        w += timedelta(minutes=1)
        await _tick(mid, t.id, [r for r in rows if r[2] <= now], now, w)
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(DowntimeEvent).where(DowntimeEvent.machine_id == mid))).scalars().all()


async def test_offshift_stop_restarting_late_inside_shift_keeps_corrected_event(client):
    # the restart (06:20) is in the shift: the in-shift event stays, closed at the restart
    evs = await _run_case(client, "PHIN", L(DAY, "06:20"), [L(DAY, "05:00"), L(DAY, "06:30")],
                          [L(DAY, "06:35"), L(DAY, "06:40")])
    assert [(e.start, e.end, e.duration_s, e.stop_start) for e in evs] == [
        (L(DAY, "06:00"), L(DAY, "06:20"), 1200.0, L(DAY, "04:00"))]


async def test_resumed_open_stop_keeps_its_in_shift_event_across_ticks(client):
    # still stopped: every tick resumes the stop from the checkpoint; its in-shift event is never dropped
    evs = await _run_case(client, "PHOP", None, [L(DAY, "05:00"), L(DAY, "06:10"), L(DAY, "06:30")],
                          [L(DAY, "06:45"), L(DAY, "07:00")])
    assert [(e.start, e.end, e.stop_start) for e in evs] == [(L(DAY, "06:00"), None, L(DAY, "04:00"))]
