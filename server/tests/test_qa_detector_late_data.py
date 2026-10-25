"""QA (Sprint 3 gate): the downtime detector must not lose a stop when readings reach InfluxDB late or out of
order relative to each other.

Found in the live e2e run (e2e/scenario_c_replay.py mqtt): the scenario C readings published in order to the
real broker are handled concurrently by the MQTT subscriber (mqtt.backlog inflight=1000), so they land in
InfluxDB out of order. A detector tick that already sees a later heartbeat moves the checkpoint past a reading
that is still in flight; `influx_fetch` only reads `ts > checkpoint`, so the late reading is never seen again.
Result on the live server: the 08:30 MAT-01 stop (360 s) and the 10:05 micro-stop (76 s) vanished, OEE/Pareto
wrong. The same happens to a gateway that flushes its SQLite buffer after reconnecting while the detector runs,
and in the real path (virtual plant playback → gateway) when a tick runs before the first reading arrives (the
checkpoint jumps to `now`, edge report).

Data in Influx is complete (verified), so a full re-detection gives the right answer; only the incremental
checkpoint loses it. Both tests drive `detect_machine` with a synthetic fetch that reveals the rows in the order
they would be visible in Influx.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from oee_helpers import SCENARIO_C, scenario_c_readings, setup_scenario_c, ts
from test_oee_api import _tenant

C = SCENARIO_C
X = C["expected"]


async def _detect(machine_id, tenant_id, visible, now):
    from src.db import AsyncSessionLocal
    from src.services.oee.detector import detect_machine
    from src.services.oee.engine import load_calendar, machine_infos

    async def fetch(tid, tag_ids, after, stop):
        return sorted(((tag, at, float(v)) for tag, v, at in visible if tag in tag_ids and after < at <= stop),
                      key=lambda r: (r[1], r[0]))

    async with AsyncSessionLocal() as s:
        info = (await machine_infos(s, tenant_id, [machine_id]))[machine_id]
        cal = await load_calendar(s, tenant_id, info.plant_id, info.tz)
        return await detect_machine(s, info, cal, now, fetch)


async def _events(client, t, mid):
    from usage_helpers import api
    start, end = ts(C["shift"]["start_utc"]), ts(C["shift"]["end_utc"])
    evs = await api(client, "GET", "/api/downtime/", t.headers("operator"),
                    params={"machine_id": mid, "start": start.isoformat(), "stop": end.isoformat()})
    return sorted((e["start"], e["duration_s"]) for e in evs)


async def test_stop_transition_arriving_after_later_heartbeat_is_not_lost(client):
    t = await _tenant("QLATE")
    p, tags, _shift, _prod = await setup_scenario_c(client, t)
    mid = p.machine["id"]
    rows = scenario_c_readings(tags)
    end = ts(C["shift"]["end_utc"])
    now = end + timedelta(hours=1)
    # the run_state=0 transition at 08:30 local (12:30Z) and its heartbeats are still in flight when a tick
    # already sees the readings up to 08:40 (12:40Z)
    a, b = ts("2026-10-06T12:30:00Z"), ts("2026-10-06T12:36:00Z")
    tick_at = ts("2026-10-06T12:40:00Z")
    late = [r for r in rows if r[0] == tags["run"]["id"] and a <= r[2] < b]
    first = [r for r in rows if r[2] <= tick_at and r not in late]
    await _detect(mid, t.id, first, now)
    await _detect(mid, t.id, rows, now)          # everything has arrived now
    got = await _events(client, t, mid)
    assert len(got) == len(X["downtime_events"]), got
    assert sorted(e["duration_s"] for e in X["downtime_events"]) == sorted(d for _, d in got)


async def test_backfilled_readings_after_an_empty_tick_are_detected(client):
    t = await _tenant("QFILL")
    p, tags, _shift, _prod = await setup_scenario_c(client, t)
    mid = p.machine["id"]
    rows = scenario_c_readings(tags)
    now = ts(C["shift"]["end_utc"]) + timedelta(hours=1)
    await _detect(mid, t.id, [], now)            # tags exist, gateway still buffering: no reading yet
    await _detect(mid, t.id, rows, now + timedelta(seconds=10))   # the buffered shift arrives
    got = await _events(client, t, mid)
    assert len(got) == len(X["downtime_events"]), got
