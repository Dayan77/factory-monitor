"""Sprint 7 — downtime detector after a long outage (QA finding: after ~40 h without data the checkpoint never
advanced: every pass re-derived the whole gap and hit downtime_machine_timeout_s, and the serial loop over every
tenant let one stuck machine slow all of them).

Scenario: two identical machines of tenant A receive the same signals for 76 h (stops of every kind: micro-stops,
stops ≥ threshold, fault stops → FailureRecords, a 2 h stop, a shift with a break). Machine R is detected live,
tick by tick (uninterrupted). Machine M loses its gateway link at +2 h: its readings stay in the gateway buffer and
are flushed 72 h later (store-and-forward), all at once. Tenant B has a live machine detected meanwhile.

Expected: M catches up in bounded slices (the checkpoint advances on every pass, no timeout), the results — events,
failure drafts, micro-stop buckets, data spans — are equal to R's, and B's machine is detected on every tick with
its own small latency while M is catching up.
"""
from __future__ import annotations

import bisect
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from oee_helpers import add_shift
from test_oee_api import _tenant
from usage_helpers import add_tag, build_plant

UTC = timezone.utc
HB = 10                                    # heartbeat (s)
HOURS = 76
OUTAGE = (2, 74)                           # hours after T0 without visible data for machine M


class FakeInflux:
    """Readings per tag, sorted; `hidden` tags only expose readings outside the outage until `flushed`."""

    def __init__(self):
        self.series: dict[str, list[tuple[datetime, float]]] = {}
        self.hidden: dict[str, tuple[datetime, datetime]] = {}
        self.flushed = False
        self.calls = 0

    def add(self, tag: str, pts: list[tuple[datetime, float]]):
        self.series[tag] = sorted(pts)

    async def fetch(self, tid, tag_ids, after, stop):
        self.calls += 1
        out = []
        for t in tag_ids:
            pts = self.series.get(t, [])
            i = bisect.bisect_right(pts, (after, float("inf")))
            j = bisect.bisect_right(pts, (stop, float("inf")))
            hid = None if self.flushed else self.hidden.get(t)
            for ts, v in pts[i:j]:
                if hid and hid[0] <= ts < hid[1]:
                    continue
                out.append((t, ts, v))
        out.sort(key=lambda r: (r[1], r[0]))
        return out


def signals(t0: datetime) -> tuple[list, list]:
    """(run, fault) readings every HB s over HOURS hours. Each hour: a 60 s micro-stop at :10, a 5 min stop at :30,
    every 3rd hour a 4 min fault stop (code 7) at :45; a 2 h stop from +30 h."""
    run, fault = [], []
    t, end = t0, t0 + timedelta(hours=HOURS)
    while t < end:
        h = (t - t0).total_seconds() / 3600
        m = t.minute + t.second / 60
        r, f = 1.0, 0.0
        if 10 <= m < 11 or 30 <= m < 35:
            r = 0.0
        if int(h) % 3 == 0 and 45 <= m < 49:
            r, f = 0.0, 7.0
        if 30 <= h < 32:
            r = 0.0
        run.append((t, r))
        fault.append((t, f))
        t += timedelta(seconds=HB)
    return run, fault


async def _snapshot_results(tenant_id: str, machine_id: str):
    from src.db import AsyncSessionLocal
    from src.models import DowntimeEvent, FailureRecord, MachineDataSpan, MicroStopBucket
    async with AsyncSessionLocal() as s:
        evs = sorted((e.start, e.end, e.suggested_reason_code, e.reason_code, e.fault_code, e.source, e.productive_day)
                     for e in (await s.execute(select(DowntimeEvent).where(
                         DowntimeEvent.tenant_id == tenant_id, DowntimeEvent.machine_id == machine_id))).scalars())
        frs = sorted((f.failed_at, f.restored_at, f.severity)
                     for f in (await s.execute(select(FailureRecord).where(
                         FailureRecord.tenant_id == tenant_id, FailureRecord.machine_id == machine_id))).scalars())
        micro = sorted((b.bucket_start, b.shift_slot, b.count, round(b.loss_s, 6), b.short_faults)
                       for b in (await s.execute(select(MicroStopBucket).where(
                           MicroStopBucket.tenant_id == tenant_id, MicroStopBucket.machine_id == machine_id)))
                       .scalars() if b.count or b.loss_s > 1e-9 or b.short_faults)
        spans = sorted((a, b) for a, b in (await s.execute(select(MachineDataSpan.start, MachineDataSpan.end).where(
            MachineDataSpan.tenant_id == tenant_id, MachineDataSpan.machine_id == machine_id))).all())
    from src.services.oee.intervals import normalize
    return evs, frs, micro, normalize(spans)


async def _checkpoint(machine_id: str):
    from src.db import AsyncSessionLocal
    from src.models import DowntimeDetectorState
    async with AsyncSessionLocal() as s:
        st = await s.get(DowntimeDetectorState, machine_id)
        return st.checkpoint if st else None


async def test_72h_outage_flush_catches_up_in_slices_equal_to_live_run(client, monkeypatch):
    from src.config import settings
    from src.services.oee import detector
    monkeypatch.setattr(settings, "downtime_settle_s", 0.0)          # wall clock of the test ≠ data time
    monkeypatch.setattr(settings, "downtime_slice_readings", 1500)   # ~4 h per slice: many slices for the backlog
    monkeypatch.setattr(settings, "downtime_catchup_budget_s", 3.0)
    monkeypatch.setattr(settings, "detector_concurrency", 2)

    ta, tb = await _tenant("CUA"), await _tenant("CUB")
    pa = await build_plant(client, ta, "CUA")
    await add_shift(client, ta, pa.plant["id"], "Turno", "06:00", "22:00",
                    breaks=[{"start_local": "12:00", "end_local": "12:30", "reason_code": "PLN-01"}])
    h = ta.headers("admin")
    m_r = pa.machine
    r_run = await add_tag(client, ta, pa, "r_run", "run_state", data_type="bool")
    r_fault = await add_tag(client, ta, pa, "r_fault", "fault_state")
    r = await client.post("/api/machines/", headers=h, json={"line_id": pa.line["id"], "name": "Máquina M"})
    assert r.status_code == 201
    m_m = r.json()
    pa.machine = m_m
    m_run = await add_tag(client, ta, pa, "m_run", "run_state", data_type="bool")
    m_fault = await add_tag(client, ta, pa, "m_fault", "fault_state")
    pb = await build_plant(client, tb, "CUB")
    b_run = await add_tag(client, tb, pb, "b_run", "run_state", data_type="bool")

    now0 = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    t0 = now0 - timedelta(hours=HOURS + 4)
    run, fault = signals(t0)
    fx = FakeInflux()
    for tag, pts in ((r_run, run), (r_fault, fault), (m_run, run), (m_fault, fault), (b_run, run)):
        fx.add(tag["id"], pts)
    for tag in (m_run, m_fault):
        fx.hidden[tag["id"]] = (t0 + timedelta(hours=OUTAGE[0]), t0 + timedelta(hours=OUTAGE[1]))

    from src.db import AsyncSessionLocal
    from src.models import DowntimeDetectorState
    async with AsyncSessionLocal() as s:          # cursors start at T0 (no initial look-back)
        for tid, mid in ((ta.id, m_r["id"]), (ta.id, m_m["id"]), (tb.id, pb.machine["id"])):
            s.add(DowntimeDetectorState(machine_id=mid, tenant_id=tid, checkpoint=t0 - timedelta(seconds=1), state={}))
        await s.commit()
    tenants = {ta.id, tb.id}

    # phase 1 — live, hourly ticks for the whole period (M sees nothing during the outage)
    t = t0 + timedelta(hours=1)
    while t <= t0 + timedelta(hours=HOURS):
        await detector.run_tick(t, fx.fetch, tenant_ids=tenants)
        t += timedelta(hours=1)
    end = t0 + timedelta(hours=HOURS)
    cp_m_before = await _checkpoint(m_m["id"])
    # the link came back at +74 h (live readings again) but the buffered 72 h are still missing: M went past the gap
    assert cp_m_before > t0 + timedelta(hours=OUTAGE[1])

    # phase 2 — the gateway flushes its 72 h buffer: the MQTT handler notes the late readings
    fx.flushed = True
    detector.note_late(ta.id, m_m["id"], t0 + timedelta(hours=OUTAGE[0]))

    # phase 3 — ticks until M is caught up (data time keeps the same `now`: nothing new arrives)
    ticks, passes, b_durations = 0, 0, []
    while ticks < 40:
        await detector.run_tick(end, fx.fetch, tenant_ids=tenants)
        ticks += 1
        st = detector.last_tick_stats
        assert not st[m_m["id"]]["timeout"]
        passes += st[m_m["id"]]["passes"]
        b_durations.append(st[pb.machine["id"]]["duration_s"])
        assert st[pb.machine["id"]]["caught_up"]                            # tenant B detected on every tick
        if st[m_m["id"]]["caught_up"] and await _checkpoint(m_m["id"]) >= await _checkpoint(m_r["id"]):
            break
    assert ticks < 40, "M never caught up"
    assert passes > 10                                                      # bounded slices, not one huge pass
    assert max(b_durations) < 2.0, b_durations                              # B's latency unaffected
    assert await _checkpoint(m_m["id"]) == await _checkpoint(m_r["id"])
    assert detector.last_tick_stats[m_m["id"]]["lag_s"] < 120

    got_m = await _snapshot_results(ta.id, m_m["id"])
    got_r = await _snapshot_results(ta.id, m_r["id"])
    evs_r = got_r[0]
    assert len(evs_r) > 50 and len(got_r[1]) >= 10 and len(got_r[2]) > 30      # the scenario exercises everything
    for name, a, b in zip(("events", "failures", "micro", "spans"), got_m, got_r):
        assert a == b, f"{name} differ after catch-up: {set(a) ^ set(b) if name != 'spans' else (a[:3], b[:3])}"


def test_slice_bounds_every_tag():
    from src.services.oee.detector import _slice
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    rows = sorted([("a", t0 + timedelta(seconds=k), 1.0) for k in range(10)]
                  + [("b", t0 + timedelta(seconds=2 * k), 0.0) for k in range(10)], key=lambda r: (r[1], r[0]))
    out, end = _slice(rows, 4)
    assert end == t0 + timedelta(seconds=3)                 # 4th reading of the densest tag
    assert all(r[1] <= end for r in out) and len(out) == 4 + 2
    assert _slice(rows, 100) == (rows, None)
    assert _slice(rows, 0) == (rows, None)


async def test_lag_sampler_reports_backlog_and_alerts(client, monkeypatch):
    from src.config import settings
    from src.db import AsyncSessionLocal
    from src.models import DowntimeDetectorState
    from src.services.oee import lag
    t = await _tenant("LAG")
    p = await build_plant(client, t, "LAG")
    await add_tag(client, t, p, "run", "run_state", data_type="bool")
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as s:
        s.add(DowntimeDetectorState(machine_id=p.machine["id"], tenant_id=t.id, checkpoint=now - timedelta(hours=40),
                                    state={"seen": [[now.isoformat(), (now - timedelta(seconds=5)).isoformat()]]}))
        await s.commit()
    monkeypatch.setattr(settings, "detector_lag_alert_s", 900.0)
    async with AsyncSessionLocal() as s:
        out = await lag.sample(s, now)
    mine = [x for x in out["downtime"] if x[1] == p.machine["id"]]
    assert len(mine) == 1 and abs(mine[0][2] - (40 * 3600 - 5)) < 1
    assert lag.DETECTOR_LAG.labels("downtime", t.id, p.machine["id"])._value.get() == mine[0][2]
    assert lag.DETECTOR_LAGGING.labels("downtime")._value.get() >= 1
    assert ("downtime", p.machine["id"]) in lag._alerted
    assert lag.downtime_lag(now, {}) == 0.0                                  # offline machine: no lag


async def test_influx_slices_equal_one_unbounded_pass(client, monkeypatch):
    """Same as above through the real InfluxDB queries (limit per tag + latest-reading aggregate): 8 h of readings
    of twin machines; one is detected with a 300-reading slice (many passes), the other in one unbounded pass."""
    from src.config import settings
    from src.db import AsyncSessionLocal
    from src.models import DowntimeDetectorState
    from src.services.influx import influx
    from src.services.oee import detector
    from src.services.oee.engine import load_calendar, machine_infos
    monkeypatch.setattr(settings, "downtime_settle_s", 0.0)
    t = await _tenant("CUI")
    p = await build_plant(client, t, "CUI")
    a = p.machine
    a_run = await add_tag(client, t, p, "a_run", "run_state", data_type="bool")
    a_fault = await add_tag(client, t, p, "a_fault", "fault_state")
    r = await client.post("/api/machines/", headers=t.headers("admin"), json={"line_id": p.line["id"], "name": "B"})
    b = r.json()
    p.machine = b
    b_run = await add_tag(client, t, p, "b_run", "run_state", data_type="bool")
    b_fault = await add_tag(client, t, p, "b_fault", "fault_state")
    t0 = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=10)
    run, fault = signals(t0)
    end = t0 + timedelta(hours=8)
    for tags, mid in (((a_run, a_fault), a["id"]), ((b_run, b_fault), b["id"])):
        for (ts, rv), (_, fv) in zip(run, fault):
            if ts >= end:
                break
            await influx.write(t.id, tags[0]["id"], rv, machine_id=mid, ts=ts)
            await influx.write(t.id, tags[1]["id"], fv, machine_id=mid, ts=ts)
    async with AsyncSessionLocal() as s:
        for mid in (a["id"], b["id"]):
            s.add(DowntimeDetectorState(machine_id=mid, tenant_id=t.id, checkpoint=t0 - timedelta(seconds=1),
                                        state={"seen": [[(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                                                         end.isoformat()]]}))
        await s.commit()
        infos = await machine_infos(s, t.id, [a["id"], b["id"]])
        cal = await load_calendar(s, t.id, infos[a["id"]].plant_id, infos[a["id"]].tz)
    monkeypatch.setattr(settings, "downtime_slice_readings", 300)
    sliced = await detector.run_machine(infos[a["id"]], cal, end, budget_s=120)
    assert sliced["caught_up"] and sliced["passes"] >= 8 and not sliced["timeout"]
    monkeypatch.setattr(settings, "downtime_slice_readings", 0)            # unbounded: one pass
    whole = await detector.run_machine(infos[b["id"]], cal, end, budget_s=120)
    assert whole["passes"] == 1 and whole["caught_up"]
    assert sliced["checkpoint"] == whole["checkpoint"]
    ra, rb = await _snapshot_results(t.id, a["id"]), await _snapshot_results(t.id, b["id"])
    assert len(ra[0]) >= 8 and len(ra[2]) >= 4
    assert ra == rb


async def test_tenant_prefetch_equals_one_query_per_machine(client, monkeypatch):
    """Sprint 7 (scale): caught-up machines of a tenant share ONE Influx query per tick (TenantPrefetch). Twin
    tenants receive the same 40 min of signals (a 5-min stop, a fault stop, a micro-stop); one is detected with the
    tenant prefetch, the other with one query per machine, tick by tick: identical results."""
    from src.config import settings
    from src.db import AsyncSessionLocal
    from src.models import DowntimeDetectorState
    from src.services.influx import influx
    from src.services.oee import detector
    monkeypatch.setattr(settings, "downtime_settle_s", 0.0)
    t0 = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=45)
    ten = {}
    for label in ("PFA", "PFB"):
        t = await _tenant(label)
        p = await build_plant(client, t, label)
        ms = []
        for k in range(2):
            if k:
                r = await client.post("/api/machines/", headers=t.headers("admin"),
                                      json={"line_id": p.line["id"], "name": f"{label}{k}"})
                p.machine = r.json()
            run = await add_tag(client, t, p, f"{label}{k}_run", "run_state", data_type="bool")
            fault = await add_tag(client, t, p, f"{label}{k}_fault", "fault_state")
            ms.append((p.machine["id"], run["id"], fault["id"]))
        for mid, run_id, fault_id in ms:
            for s in range(0, 40 * 60, HB):
                ts = t0 + timedelta(seconds=s)
                rv = 0.0 if (600 <= s < 900 or 1200 <= s < 1290 or 1800 <= s < 1840) else 1.0
                fv = 3.0 if 1200 <= s < 1290 else 0.0
                await influx.write(t.id, run_id, rv, machine_id=mid, ts=ts)
                await influx.write(t.id, fault_id, fv, machine_id=mid, ts=ts)
        async with AsyncSessionLocal() as s:
            for mid, _, _ in ms:
                s.add(DowntimeDetectorState(machine_id=mid, tenant_id=t.id, checkpoint=t0 - timedelta(seconds=1),
                                            state={}))
            await s.commit()
        ten[label] = (t, ms)
    used = []
    real = detector._prefetch_tenants

    async def spy(jobs, now):
        pf, sig = await real(jobs, now)
        used.extend(pf.values())
        return pf, sig
    monkeypatch.setattr(detector, "_prefetch_tenants", spy)
    for step in range(1, 9):                                   # ticks every 5 min of data time
        now = t0 + timedelta(minutes=5 * step)
        monkeypatch.setattr(settings, "downtime_prefetch_window_s", 600.0)
        await detector.run_tick(now=now, tenant_ids={ten["PFA"][0].id})
        monkeypatch.setattr(settings, "downtime_prefetch_window_s", 0.0)
        await detector.run_tick(now=now, tenant_ids={ten["PFB"][0].id})
    assert used and sum(p.hits for p in used) >= 8               # really served from the tenant query
    for (ma, _, _), (mb, _, _) in zip(ten["PFA"][1], ten["PFB"][1]):
        ra, rb = await _snapshot_results(ten["PFA"][0].id, ma), await _snapshot_results(ten["PFB"][0].id, mb)
        assert ra[0] and (ra[1] or ra[2]) and ra == rb
        assert await _checkpoint(ma) == await _checkpoint(mb)


async def test_slice_shrinks_after_timeout_so_a_slow_host_still_catches_up(client, monkeypatch):
    """Sprint 7b: on a slow/contended host even a bounded slice can exceed DOWNTIME_MACHINE_TIMEOUT_S. The timed-out
    pass is rolled back (checkpoint unchanged); re-running the SAME slice would time out forever. The slice of that
    machine is halved after a timeout (and grows back after successes), so the checkpoint always advances and the
    result equals one unbounded pass of a twin machine. Simulated host: a pass over more than 600 readings per tag
    takes "5 s", the timeout is 1 s."""
    import asyncio

    from src.config import settings
    from src.db import AsyncSessionLocal
    from src.models import DowntimeDetectorState
    from src.services.oee import detector
    from src.services.oee.engine import load_calendar, machine_infos
    monkeypatch.setattr(settings, "downtime_settle_s", 0.0)
    monkeypatch.setattr(settings, "downtime_machine_timeout_s", 1.0)
    monkeypatch.setattr(settings, "downtime_slice_readings", 2000)
    t = await _tenant("CUS")
    p = await build_plant(client, t, "CUS")
    a = p.machine
    a_run = await add_tag(client, t, p, "a_run", "run_state", data_type="bool")
    a_fault = await add_tag(client, t, p, "a_fault", "fault_state")
    r = await client.post("/api/machines/", headers=t.headers("admin"), json={"line_id": p.line["id"], "name": "B"})
    b = r.json()
    p.machine = b
    b_run = await add_tag(client, t, p, "b_run", "run_state", data_type="bool")
    b_fault = await add_tag(client, t, p, "b_fault", "fault_state")
    t0 = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=10)
    end = t0 + timedelta(hours=8)
    run, fault = signals(t0)
    run = [x for x in run if x[0] < end]
    fault = [x for x in fault if x[0] < end]
    fx = FakeInflux()
    for tag, pts in ((a_run, run), (a_fault, fault), (b_run, run), (b_fault, fault)):
        fx.add(tag["id"], pts)
    async with AsyncSessionLocal() as s:
        for mid in (a["id"], b["id"]):
            s.add(DowntimeDetectorState(machine_id=mid, tenant_id=t.id, checkpoint=t0 - timedelta(seconds=1),
                                        state={}))
        await s.commit()
        infos = await machine_infos(s, t.id, [a["id"], b["id"]])
        cal = await load_calendar(s, t.id, infos[a["id"]].plant_id, infos[a["id"]].tz)

    real = detector.detect_machine
    seen_slices = []

    async def slow_host(db, info, cal_, now, fetch, *args, slice_readings=None, **kw):
        if info.id == a["id"]:
            seen_slices.append(slice_readings)
            if not slice_readings or slice_readings > 600:
                await asyncio.sleep(5)                       # cancelled by the 1 s timeout
        return await real(db, info, cal_, now, fetch, *args, slice_readings=slice_readings, **kw)
    monkeypatch.setattr(detector, "detect_machine", slow_host)

    timeouts, out = 0, None
    for _ in range(40):                                      # ticks
        out = await detector.run_machine(infos[a["id"]], cal, end, fx.fetch, budget_s=30)
        timeouts += out["timeout"]
        if out["caught_up"]:
            break
    assert out["caught_up"], seen_slices
    assert timeouts >= 1 and min(s for s in seen_slices if s) <= 600   # shrank below the "slow" size
    monkeypatch.setattr(settings, "downtime_slice_readings", 0)            # twin: one unbounded pass, fast host
    monkeypatch.setattr(settings, "downtime_machine_timeout_s", 120.0)
    for _ in range(3):                         # first pass only records what it saw (maturity); then one pass
        whole = await detector.run_machine(infos[b["id"]], cal, end, fx.fetch, budget_s=120)
        if whole["checkpoint"] == out["checkpoint"]:
            break
    assert whole["passes"] == 1 and whole["caught_up"]
    assert out["checkpoint"] == whole["checkpoint"]
    ra, rb = await _snapshot_results(t.id, a["id"]), await _snapshot_results(t.id, b["id"])
    assert len(ra[0]) >= 8 and ra == rb


def test_slice_feedback_halves_on_timeout_and_grows_back(monkeypatch):
    from src.config import settings
    from src.services.oee import detector
    monkeypatch.setattr(settings, "downtime_slice_readings", 20000)
    mid = "slice-feedback-machine"
    detector._slice_size.pop(mid, None)
    assert detector.slice_for(mid) == 20000
    for expect in (10000, 5000, 2500):
        detector._slice_feedback(mid, True)
        assert detector.slice_for(mid) == expect
    for _ in range(10):
        detector._slice_feedback(mid, True)
    assert detector.slice_for(mid) == detector.SLICE_MIN_READINGS
    for _ in range(10):
        detector._slice_feedback(mid, False)
    assert detector.slice_for(mid) == 20000 and mid not in detector._slice_size
