"""Sprint 7 — KPI audit items (docs/audit_kpis.md) that need the database / InfluxDB. The pure counter-examples live
in docs/scenarios/test_audit_kpis.py; here the same rules are checked through the API and the services:

  * A-6  failures_outside_production in GET /api/reliability and GET /api/maintenance/reliability (+ tenant isolation)
  * A-20 MART (repair_finished_at − repair_started_at) in both reliability APIs
  * A-7  `estimated` on reliability rows whose cycles are parts × cycles_per_part
  * A-2  nominal RUL with an old installed_at and few days of data (component_usage_detail through the API)
  * A-16 operating_seconds clips the current hour with the caller's `now`
  * A-18 conditional risk (reliability_life) with < 3 / < 14 days of pace
  * A-19 live cadence of the map from the raw counter readings (InfluxDB)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from oee_helpers import add_shift
from test_oee_api import DAY, L, _plant_with_tags, _tenant
from usage_helpers import add_component, add_position, add_tag, api, build_plant

UTC = timezone.utc


def _hour(t: datetime) -> datetime:
    return t.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


async def _add_failures(tenant_id, machine_id, items, position_id=None):
    from src.db import AsyncSessionLocal
    from src.models import FailureRecord
    async with AsyncSessionLocal() as s:
        for failed, restored, rs, rf in items:
            s.add(FailureRecord(tenant_id=tenant_id, machine_id=machine_id, position_id=position_id,
                                failed_at=failed, restored_at=restored, repair_started_at=rs,
                                repair_finished_at=rf, severity="critical", status="draft",
                                detection_method="production_interference"))
        await s.commit()


async def _add_buckets(tenant_id, scope, entity_id, measure, start, hours, per_hour):
    from src.db import AsyncSessionLocal
    from src.models import UsageBucket
    async with AsyncSessionLocal() as s:
        for k in range(hours):
            s.add(UsageBucket(scope=scope, entity_id=entity_id, measure=measure, tenant_id=tenant_id,
                              bucket_start=start + timedelta(hours=k), value=float(per_hour)))
        await s.commit()


async def _set_total(tenant_id, scope, entity_id, measure, value):
    from src.db import AsyncSessionLocal
    from src.models import UsageTotal
    async with AsyncSessionLocal() as s:
        s.add(UsageTotal(scope=scope, entity_id=entity_id, measure=measure, tenant_id=tenant_id, value=float(value)))
        await s.commit()


# ── A-6 / A-20 ──────────────────────────────────────────────────────────────

async def test_A6_A20_reliability_apis_outside_production_and_mart(client):
    """kpis S5-1c case break_fault_shift: Turno A 06–14, café 10:00–10:15, refeição 12:00–12:30; failures at 08:00
    (production), 10:05 (break), 12:10 (break), 14:30 (off shift) → failures_outside_production = 3. Plus one at
    09:00 inside a declared planned stop (PLN-02 08:55–09:30) → 4. MART from the two failures with repair times."""
    t = await _tenant("S7REL")
    other = await _tenant("S7RELB")
    p, run, fault = await _plant_with_tags(client, t, "S7REL")
    await add_shift(client, t, p.plant["id"], "Turno A", "06:00", "14:00", breaks=[
        {"start_local": "10:00", "end_local": "10:15", "reason_code": "PLN-01"},
        {"start_local": "12:00", "end_local": "12:30", "reason_code": "PLN-01"}])
    mid = p.machine["id"]
    f = lambda hhmm: L(DAY, hhmm)  # noqa: E731
    await _add_failures(t.id, mid, [
        (f("08:00"), f("08:20"), f("08:05"), f("08:15")),          # repair 10 min
        (f("10:05"), f("10:40"), f("10:10"), f("10:40")),          # repair 30 min
        (f("12:10"), f("12:20"), None, None),
        (f("14:30"), f("15:00"), None, None),
        (f("09:00"), f("09:10"), None, None)])
    from src.db import AsyncSessionLocal
    from src.models import DowntimeEvent
    async with AsyncSessionLocal() as s:
        s.add(DowntimeEvent(tenant_id=t.id, machine_id=mid, start=f("08:55"), end=f("09:30"), duration_s=2100,
                            source="manual", planned=True, reason_code="PLN-02"))
        await s.commit()
    params = {"scope": "machine", "id": mid, "start": f("00:00").isoformat(), "stop": L(DAY, "00:00", 1).isoformat()}
    r = await api(client, "GET", "/api/reliability", t.headers("admin"), params=params)
    assert r["failures"] == 5 and r["failures_outside_production"] == 4
    assert abs(r["mart_s"] - 1200.0) < 1e-6 and r["repairs_timed"] == 2
    # no run_state data at all → MTBF undefined, never 0 (A-4)
    assert r["mtbf_h"] is None and r["reason"] == "insufficient_data"
    rel = await api(client, "GET", "/api/maintenance/reliability", t.headers("admin"), params={
        "group_by": "machine", "start": params["start"], "stop": params["stop"]})
    row = next(x for x in rel["rows"] if x["machine_id"] == mid)
    assert row["n"] == 5 and row["failures_outside_production"] == 4
    assert abs(row["mart_min"] - 20.0) < 1e-6 and row["estimated"] is False
    # tenant isolation: B neither sees A's machine (404) nor its rows
    rb = await client.get("/api/reliability", headers=other.headers("admin"), params=params)
    assert rb.status_code == 404
    relb = await api(client, "GET", "/api/maintenance/reliability", other.headers("admin"), params={
        "group_by": "machine", "start": params["start"], "stop": params["stop"]})
    assert all(x["machine_id"] != mid for x in relb["rows"])


# ── A-7 ─────────────────────────────────────────────────────────────────────

async def test_A7_estimated_cycles_flag_on_reliability_rows(client):
    """Gripper G1 without its own cycle tag (cycles = parts × cycles_per_part) → estimated = true; cylinder C1 with
    its own cycle counter → false. Component-type row of the gripper is estimated too."""
    t = await _tenant("S7EST")
    p = await build_plant(client, t, "S7EST")
    g1 = await add_position(client, t, p, "Garra G1", component_type="gripper", cycles_per_part=2)
    c1 = await add_position(client, t, p, "Cilindro C1")
    await add_tag(client, t, p, "total", "part_counter_total")
    await add_tag(client, t, p, "c1cyc", "cycle_counter", position=c1)
    now = datetime.now(UTC)
    start = _hour(now - timedelta(days=2))
    await _add_buckets(t.id, "position", g1["id"], "cycles", start, 2, 700)
    await _add_buckets(t.id, "position", c1["id"], "cycles", start, 2, 500)
    await _add_failures(t.id, p.machine["id"], [(start + timedelta(minutes=30), None, None, None)], g1["id"])
    await _add_failures(t.id, p.machine["id"], [(start + timedelta(minutes=40), None, None, None)], c1["id"])
    q = {"start": (now - timedelta(days=3)).isoformat(), "stop": now.isoformat()}
    rel = await api(client, "GET", "/api/maintenance/reliability", t.headers("admin"), params={**q, "group_by": "position"})
    rows = {r["position_id"]: r for r in rel["rows"]}
    assert rows[g1["id"]]["estimated"] is True and rows[g1["id"]]["mcbf_cycles"] == 1400
    assert rows[c1["id"]]["estimated"] is False and rows[c1["id"]]["mcbf_cycles"] == 1000
    assert abs(rows[g1["id"]]["failure_rate_per_million_cycles"] - 1e6 / 1400) < 1e-6      # A-8
    typ = await api(client, "GET", "/api/maintenance/reliability", t.headers("admin"),
                    params={**q, "group_by": "component_type"})
    by = {r["component_type"]: r for r in typ["rows"]}
    assert by["gripper"]["estimated"] is True and by["pneumatic_cylinder"]["estimated"] is False


# ── A-2 ─────────────────────────────────────────────────────────────────────

async def test_A2_nominal_rul_uses_days_with_data(client):
    """Cylinder installed 400 days ago (life 5 000 000, used 3 600 000), gateway online 5 days at 20 000 cycles/day →
    pace 20 000/day over 5 days (not ÷ 14): 70 days, low_confidence."""
    t = await _tenant("S7RUL")
    p = await build_plant(client, t, "S7RUL")
    pos = await add_position(client, t, p, "Cilindro U3")
    now = datetime.now(UTC)
    comp = await add_component(client, t, p, "U3", position=pos, nominal_life={"cycles": 5_000_000},
                               installed_at=now - timedelta(days=400))
    await _set_total(t.id, "component", comp["id"], "cycles", 3_600_000)
    first = _hour(now) - timedelta(days=5)
    for scope, eid in (("component", comp["id"]), ("position", pos["id"])):
        await _add_buckets(t.id, scope, eid, "cycles", first, 5 * 24, 20000 / 24)
    d = await api(client, "GET", f"/api/positions/{pos['id']}", t.headers("admin"))
    cu = d["component_usage"]
    assert abs(cu["rate_per_day"] - 20000.0) < 1e-6
    assert cu["rul_days"] == 70 and cu["low_confidence"] is True


# ── A-16 ────────────────────────────────────────────────────────────────────

async def test_A16_operating_seconds_uses_the_given_now(client):
    t = await _tenant("S7CLK")
    p, run, _ = await _plant_with_tags(client, t, "S7CLK")
    h10 = L(DAY, "10:00")
    await _add_buckets(t.id, "machine", p.machine["id"], "operating_seconds", h10, 1, 1800.0)
    from src.db import AsyncSessionLocal
    from src.services.oee.engine import operating_seconds
    async with AsyncSessionLocal() as s:
        # frozen clock at 10:30: the 10:00 bucket holds the 30 min so far — counted whole, not ½ (wall clock)
        assert await operating_seconds(s, t.id, [p.machine["id"]], h10, L(DAY, "10:30"), L(DAY, "10:30")) == 1800.0
        # after the hour (default now = wall clock, days later) the bucket is a full hour: half of it in [10:00, 10:30)
        assert await operating_seconds(s, t.id, [p.machine["id"]], h10, L(DAY, "10:30")) == 900.0


# ── A-18 ────────────────────────────────────────────────────────────────────

async def test_A18_conditional_risk_pace_needs_three_days(client):
    t = await _tenant("S7WB")
    p = await build_plant(client, t, "S7WB")
    now = datetime.now(UTC)
    pa = await add_position(client, t, p, "Cilindro A")
    pb = await add_position(client, t, p, "Cilindro B")
    young = await add_component(client, t, p, "young", position=pa, nominal_life={"cycles": 5_000_000},
                                installed_at=now - timedelta(hours=3))
    mid = await add_component(client, t, p, "mid", position=pb, nominal_life={"cycles": 5_000_000},
                              installed_at=now - timedelta(days=5, minutes=30))
    await _add_buckets(t.id, "component", young["id"], "cycles", _hour(now) - timedelta(hours=2), 2, 1000)
    await _add_buckets(t.id, "component", mid["id"], "cycles", _hour(now) - timedelta(days=5), 5 * 24, 20000 / 24)
    from src.db import AsyncSessionLocal
    from src.services.reports.life import reliability_life
    async with AsyncSessionLocal() as s:
        out = await reliability_life(s, t.id, now)
    units = {u["component_id"]: u for ty in out["types"] for g in ty["groups"] for u in g["units"]}
    assert units[young["id"]]["rate_per_day"] is None and units[young["id"]]["rate_window_days"] < 3
    assert abs(units[mid["id"]]["rate_per_day"] - 20000.0) < 1e-6
    assert units[mid["id"]]["rate_low_confidence"] is True and abs(units[mid["id"]]["rate_window_days"] - 5.0) < 1e-6


# ── A-19 ────────────────────────────────────────────────────────────────────

async def test_A19_live_cadence_from_raw_counter_readings(client):
    """400 parts in the 3 h 45 before the last 15 min (≈ 107/h) and 15 parts in the last 15 min → live cadence
    15 × 4 = 60 parts/h from the counter readings in InfluxDB (the hourly buckets cannot separate them)."""
    from src.services.influx import influx
    from src.services.layout.service import live_parts, part_counter_tags
    t = await _tenant("S7CAD")
    p = await build_plant(client, t, "S7CAD")
    tag = await add_tag(client, t, p, "total", "part_counter_total", counter_max=65535)
    now = datetime.now(UTC).replace(microsecond=0)
    v = 65130.0
    ts = now - timedelta(minutes=240)
    while ts <= now - timedelta(minutes=15):
        await influx.write(t.id, tag["id"], float(int(v) % 65536), machine_id=p.machine["id"], ts=ts)
        v += 400 / 225
        ts += timedelta(minutes=1)
    v = 65130 + 400                             # the window crosses the rollover (domain.md §3.3)
    for k in range(1, 16):
        await influx.write(t.id, tag["id"], float(int(v + k) % 65536), machine_id=p.machine["id"],
                           ts=now - timedelta(minutes=15) + timedelta(minutes=k))
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        tags = await part_counter_tags(s, t.id, [p.machine["id"]])
    assert [x[0] for x in tags] == [tag["id"]]
    got = await live_parts(t.id, [p.machine["id"]], tags, now)
    assert abs(got[p.machine["id"]] - 15) <= 1          # 15 parts (± the fractional baseline)
    # another tenant's machine id never reads this tenant's series
    other = await _tenant("S7CADB")
    assert await live_parts(other.id, [p.machine["id"]], [(tag["id"], p.machine["id"], "part_counter_total", 65535)],
                            now) == {}


async def test_A19_map_marks_cadence_source(client):
    """Through shift_kpis: counter readings → cadence_source counter; Influx down → hourly-bucket estimate."""
    from src.db import AsyncSessionLocal
    from src.models import Line, Machine, Plant
    from src.services.influx import influx
    from src.services.layout import service as lsvc
    t = await _tenant("S7MAP")
    p = await build_plant(client, t, "S7MAP")
    tag = await add_tag(client, t, p, "total", "part_counter_total")
    now = datetime.now(UTC).replace(microsecond=0)
    for k in range(0, 21):
        await influx.write(t.id, tag["id"], float(1000 + 2 * k), machine_id=p.machine["id"],
                           ts=now - timedelta(minutes=20) + timedelta(minutes=k))
    async with AsyncSessionLocal() as s:
        plant = await s.get(Plant, p.plant["id"])
        lines = list((await s.execute(select(Line).where(Line.id == p.line["id"]))).scalars())
        machines = list((await s.execute(select(Machine).where(Machine.id == p.machine["id"]))).scalars())
        _, per_m, _, _ = await lsvc.shift_kpis(s, t.id, plant, lines, machines, now)
        k = per_m[p.machine["id"]]
        assert k["cadence_source"] == "counter" and abs(k["cadence_per_hour"] - 30 * 4) < 1e-6

        async def boom(*a, **kw):
            raise RuntimeError("influx down")
        orig = lsvc.live_parts
        lsvc.live_parts = lambda *a, **kw: boom()
        try:
            _, per_m, _, _ = await lsvc.shift_kpis(s, t.id, plant, lines, machines, now)
        finally:
            lsvc.live_parts = orig
        assert per_m[p.machine["id"]]["cadence_source"] == "buckets"
