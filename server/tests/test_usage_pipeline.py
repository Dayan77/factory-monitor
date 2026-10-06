"""MQTT → usage accumulators (domain.md §3.3–3.5, §1.1) through the real handler, Postgres and Influx."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from conftest import _make_tenant
from usage_helpers import (add_component, add_position, add_tag, api, build_plant, publish, totals_of)


def base_ts(hours_ago: int = 3) -> datetime:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return now - timedelta(hours=hours_ago)


async def _events(db, tag_id):
    from src.models import UsageEvent
    rows = (await db.execute(select(UsageEvent.event).where(UsageEvent.tag_id == tag_id)
                             .order_by(UsageEvent.ts))).scalars().all()
    return list(rows)


async def _bucket_sum(db, scope, eid, measure):
    from src.models import UsageBucket
    return (await db.execute(select(func.coalesce(func.sum(UsageBucket.value), 0)).where(
        UsageBucket.scope == scope, UsageBucket.entity_id == eid, UsageBucket.measure == measure))).scalar_one()


async def test_counter_rules_rollover_reset_duplicate(client, tenants, db):
    t = tenants.a
    p = await build_plant(client, t)
    pos = await add_position(client, t, p, "Cilindro A1")
    comp = await add_component(client, t, p, "c1", pos, nominal_life={"cycles": 5_000_000},
                               installed_at=base_ts(24))
    tag = await add_tag(client, t, p, "A1.ciclos", "cycle_counter", pos, counter_max=65535, max_rate_per_s=1000)
    T = base_ts()
    seq = [(0, 1000), (100, 1250), (100, 1250), (200, 65530), (201, 4), (300, 120), (400, 50)]
    for sec, v in seq:
        await publish(t.id, tag["id"], v, T + timedelta(seconds=sec))
    expected = 250 + 64280 + 10 + 116 + 50
    assert (await totals_of(db, "position", pos["id"]))["cycles"] == expected
    assert (await totals_of(db, "component", comp["id"]))["cycles"] == expected
    assert await _bucket_sum(db, "position", pos["id"], "cycles") == expected
    assert await _events(db, tag["id"]) == ["counter_rollover", "counter_reset"]

    # re-delivery of an already processed reading changes nothing (idempotent)
    await publish(t.id, tag["id"], 50, T + timedelta(seconds=400))
    await publish(t.id, tag["id"], 120, T + timedelta(seconds=300))     # older → ignored
    assert (await totals_of(db, "position", pos["id"]))["cycles"] == expected


async def test_counter_glitch_keeps_prev(client, tenants, db):
    t = tenants.a
    p = await build_plant(client, t)
    pos = await add_position(client, t, p, "Cilindro B1")
    tag = await add_tag(client, t, p, "B1.ciclos", "cycle_counter", pos, counter_max=65535, max_rate_per_s=2)
    T = base_ts()
    for sec, v in [(0, 1000), (1, 900000), (2, 1003)]:
        await publish(t.id, tag["id"], v, T + timedelta(seconds=sec))
    assert (await totals_of(db, "position", pos["id"]))["cycles"] == 3
    assert await _events(db, tag["id"]) == ["counter_glitch"]


async def test_default_max_rate_from_machine_ideal_cycle_time(client, tenants, db):
    t = tenants.a
    p = await build_plant(client, t, ideal_cycle_time_s=1.0)    # max_rate = 2/s
    pos = await add_position(client, t, p, "Cilindro C1")
    tag = await add_tag(client, t, p, "C1.ciclos", "cycle_counter", pos)
    T = base_ts()
    for sec, v in [(0, 10), (1, 5000), (2, 12)]:
        await publish(t.id, tag["id"], v, T + timedelta(seconds=sec))
    assert (await totals_of(db, "position", pos["id"]))["cycles"] == 2
    assert await _events(db, tag["id"]) == ["counter_glitch"]


async def test_reading_for_tag_of_other_tenant_is_rejected(client, tenants, db):
    from src.models import Machine, TagState
    t = tenants.a
    p = await build_plant(client, t)
    pos = await add_position(client, t, p, "Cilindro X")
    tag = await add_tag(client, t, p, "X.ciclos", "cycle_counter", pos)
    T = base_ts()
    for sec, v in [(0, 10), (10, 15)]:
        await publish(tenants.b.id, tag["id"], v, T + timedelta(seconds=sec))   # wrong tenant in topic
    db.expire_all()
    assert await db.get(TagState, tag["id"]) is None
    assert await totals_of(db, "position", pos["id"]) == {}
    m = await db.get(Machine, p.machine["id"])
    assert m.last_seen_at is None
    from src.services.mqtt_subscriber import pipeline
    pl = pipeline()                             # per-tag queues do not accumulate (Sprint 5b pipeline)
    assert pl is None or (tenants.b.id, tag["id"]) not in pl._queues


async def test_component_swap_position_keeps_counting(client, tenants, db):
    t = tenants.a
    h = t.headers("admin")
    p = await build_plant(client, t)
    pos = await add_position(client, t, p, "Cilindro D1")
    T = base_ts()
    c1 = await add_component(client, t, p, "c1", pos, nominal_life={"cycles": 1000}, installed_at=T - timedelta(days=1))
    tag = await add_tag(client, t, p, "D1.ciclos", "cycle_counter", pos, max_rate_per_s=100)
    for sec, v in [(0, 0), (100, 100)]:
        await publish(t.id, tag["id"], v, T + timedelta(seconds=sec))
    removed = await api(client, "POST", f"/api/components/{c1['id']}/remove", h, json={
        "reason": "preventive_replacement", "removed_at": (T + timedelta(seconds=150)).isoformat()})
    assert removed["state"] == "removed" and removed["position_id"] is None
    assert removed["usage_since_install"]["cycles"] == 100 and removed["life_consumed_pct"] == 0.1
    c2 = await add_component(client, t, p, "c2", None, nominal_life={"cycles": 1000})
    await api(client, "POST", f"/api/positions/{pos['id']}/install", h, json={
        "component_id": c2["id"], "installed_at": (T + timedelta(seconds=200)).isoformat()})
    # PLC counter also zeroed at the swap → reset rule, no negative cycles
    for sec, v in [(300, 40)]:
        await publish(t.id, tag["id"], v, T + timedelta(seconds=sec))
    assert (await totals_of(db, "position", pos["id"]))["cycles"] == 140
    assert (await totals_of(db, "component", c1["id"]))["cycles"] == 100
    assert (await totals_of(db, "component", c2["id"]))["cycles"] == 40

    detail = await api(client, "GET", f"/api/positions/{pos['id']}", t.headers("operator"))
    assert detail["usage"]["cycles_total"] == 140 and detail["usage"]["cycles_estimated"] is False
    assert detail["installed_component"]["id"] == c2["id"]
    assert detail["component_usage"]["cycles"] == 40
    assert detail["component_usage"]["life_consumed_pct"] == pytest.approx(0.04)
    hist = await api(client, "GET", "/api/components/", t.headers("operator"), params={"position_id": pos["id"]})
    assert {c["id"] for c in hist} == {c1["id"], c2["id"]}


async def test_run_state_hours_starts_debounce_and_machine_status(client, tenants, db):
    from src.models import Machine
    t = tenants.a
    p = await build_plant(client, t)
    motor = await add_position(client, t, p, "Motor esteira", "electric_motor")
    m1 = await add_component(client, t, p, "m1", motor, component_type="electric_motor",
                             nominal_life={"hours": 30000, "starts": 200000}, installed_at=base_ts(24))
    run = await add_tag(client, t, p, "run", "run_state", data_type="bool")
    fault = await add_tag(client, t, p, "fault", "fault_state")
    T = base_ts()
    statuses = []

    async def pub(tag, sec, v):
        await publish(t.id, tag["id"], v, T + timedelta(seconds=sec))
        db.expire_all()
        statuses.append((await db.get(Machine, p.machine["id"])).status.value)

    await pub(run, 0, 0)        # baseline → stopped
    await pub(run, 10, 1)       # start → running
    await pub(run, 40, 1)       # heartbeat
    await pub(run, 70, 0)       # 60 s on → stopped
    await pub(run, 70.5, 1)     # 0 of 0.5 s < 1 s debounce → no new start
    await pub(run, 100, 0)      # +29.5 s
    assert statuses == ["stopped", "running", "running", "stopped", "running", "stopped"]
    mt = await totals_of(db, "machine", p.machine["id"])
    assert mt["operating_seconds"] == pytest.approx(89.5) and mt["starts"] == 1
    pt = await totals_of(db, "position", motor["id"])      # inherits the machine run_state
    assert pt["operating_seconds"] == pytest.approx(89.5) and pt["starts"] == 1
    assert (await totals_of(db, "component", m1["id"]))["starts"] == 1

    # fault_state has priority (§1.1)
    await pub(fault, 110, 0)
    await pub(run, 120, 1)
    await pub(fault, 130, 3)
    await pub(run, 140, 1)
    await pub(fault, 150, 0)
    assert statuses[-5:] == ["stopped", "running", "error", "error", "running"]

    # a non-semantic reading does not flip a machine that has run_state
    analog = await add_tag(client, t, p, "temp", "analog", data_type="float")
    await pub(run, 160, 0)
    await pub(analog, 170, 42.0)
    assert statuses[-1] == "stopped"


async def test_part_counters_production_and_estimated_cycles(client, tenants, db):
    t = tenants.a
    h = t.headers("admin")
    p = await build_plant(client, t)
    grip = await add_position(client, t, p, "Garra", "gripper", cycles_per_part=2)
    cyl = await add_position(client, t, p, "Cilindro", "pneumatic_cylinder")
    await add_tag(client, t, p, "cyl.ciclos", "cycle_counter", cyl, max_rate_per_s=100)
    good = await add_tag(client, t, p, "boas", "part_counter_good", max_rate_per_s=100)
    scrap = await add_tag(client, t, p, "refugo", "part_counter_scrap", max_rate_per_s=100)
    await api(client, "PATCH", f"/api/lines/{p.line['id']}", h, json={"output_machine_id": p.machine["id"]})

    now = datetime.now(timezone.utc)
    from src.services.usage.queries import local_midnight_utc, tzinfo
    midnight = local_midnight_utc(now, tzinfo("America/Manaus"))
    t1 = max(now - timedelta(seconds=30), midnight + timedelta(seconds=1))
    t2 = t1 + timedelta(seconds=10)
    t3 = t1 + timedelta(seconds=20)
    await publish(t.id, good["id"], 1000, t1)
    await publish(t.id, scrap["id"], 10, t1)
    await publish(t.id, good["id"], 1007, t2)
    await publish(t.id, good["id"], 1010, t3)
    await publish(t.id, scrap["id"], 12, t3)

    mt = await totals_of(db, "machine", p.machine["id"])
    assert (mt["good"], mt["scrap"], mt["total"]) == (10, 2, 12)
    assert (await totals_of(db, "position", grip["id"]))["cycles"] == 24     # 12 parts × 2, estimated
    assert "cycles" not in await totals_of(db, "position", cyl["id"])        # has its own counter

    usage = await api(client, "GET", f"/api/machines/{p.machine['id']}/usage", t.headers("operator"))
    assert usage["production"]["good"] == 10 and usage["production"]["scrap"] == 2
    assert usage["production"]["total"] == 12 and usage["production"]["timezone"] == "America/Manaus"
    rows = {r["name"]: r for r in usage["positions"]}
    assert rows["Garra"]["cycles_total"] == 24 and rows["Garra"]["cycles_estimated"] is True
    assert rows["Cilindro"]["cycles_estimated"] is False

    prod = await api(client, "GET", f"/api/lines/{p.line['id']}/production", t.headers("operator"),
                     params={"start": (t1 - timedelta(hours=1)).isoformat(), "stop": (t3 + timedelta(hours=1)).isoformat()})
    assert (prod["good"], prod["scrap"], prod["total"]) == (10, 2, 12)
    assert prod["cadence_per_hour"] > 0
    assert sum(pt["good"] for pt in prod["points"]) == 10 and sum(pt["scrap"] for pt in prod["points"]) == 2

    hist = await api(client, "GET", "/api/usage/history", t.headers("operator"), params={
        "position_id": grip["id"], "measure": "cycles",
        "start": (t1 - timedelta(hours=2)).isoformat(), "stop": (t3 + timedelta(hours=1)).isoformat()})
    assert sum(pt["value"] for pt in hist["points"]) == 24


async def test_good_derived_from_total_minus_scrap(client, tenants, db):
    t = tenants.a
    p = await build_plant(client, t)
    total = await add_tag(client, t, p, "total", "part_counter_total", max_rate_per_s=100)
    scrap = await add_tag(client, t, p, "refugo", "part_counter_scrap", max_rate_per_s=100)
    T = base_ts()
    for tag, sec, v in [(total, 0, 0), (scrap, 0, 0), (total, 60, 50), (scrap, 60, 4)]:
        await publish(t.id, tag["id"], v, T + timedelta(seconds=sec))
    hist = await api(client, "GET", "/api/usage/history", t.headers("operator"), params={
        "machine_id": p.machine["id"], "measure": "good",
        "start": (T - timedelta(hours=1)).isoformat(), "stop": (T + timedelta(hours=1)).isoformat()})
    assert sum(pt["value"] for pt in hist["points"]) == 46
    mt = await totals_of(db, "machine", p.machine["id"])
    assert mt["total"] == 50 and mt["scrap"] == 4


async def test_usage_ws_messages_are_coalesced(client, tenants, db):
    from src.services.usage.broadcaster import usage_broadcaster
    from src.websocket.manager import manager

    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send_json(self, msg):
            self.sent.append(msg)

    t = tenants.a
    p = await build_plant(client, t)
    pos = await add_position(client, t, p, "Cilindro WS")
    tag = await add_tag(client, t, p, "ws.ciclos", "cycle_counter", pos, max_rate_per_s=100)
    ws = FakeWS()
    await usage_broadcaster.flush()
    await manager.connect(ws, t.id, accept=False)
    try:
        T = datetime.now(timezone.utc) - timedelta(seconds=10)
        for sec, v in [(0, 100), (1, 103), (2, 107), (3, 110)]:
            await publish(t.id, tag["id"], v, T + timedelta(seconds=sec))
        await usage_broadcaster.flush()
    finally:
        manager.disconnect(ws, t.id)
    usage = [m for m in ws.sent if m["type"] == "usage" and m["position_id"] == pos["id"]]
    assert len(usage) == 1          # 3 deltas within one flush interval → one message
    msg = usage[0]
    assert msg == {**msg, "machine_id": p.machine["id"], "measure": "cycles", "delta": 10, "total": 10}
    assert any(m["type"] == "sensor_reading" for m in ws.sent)   # existing messages unchanged


async def test_recompute_equals_incremental(client, db):
    """§3.5: accumulators rebuilt from history (raw readings in Influx) == incremental ones."""
    from src.db import AsyncSessionLocal
    from src.models import UsageBucket
    from src.services.usage.recompute import load_readings, recompute_usage, replay
    from src.services.usage.topology import load_topology

    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, "R")
        await s.commit()
    h = t.headers("admin")
    p = await build_plant(client, t)
    cyl = await add_position(client, t, p, "Cilindro R1")
    grip = await add_position(client, t, p, "Garra R", "gripper", cycles_per_part=2)
    motor = await add_position(client, t, p, "Motor R", "electric_motor")
    T = base_ts(5)
    c1 = await add_component(client, t, p, "c1", cyl, nominal_life={"cycles": 10000}, installed_at=T - timedelta(days=2))
    await add_component(client, t, p, "g1", grip, component_type="gripper", installed_at=T - timedelta(days=2))
    await add_component(client, t, p, "m1", motor, component_type="electric_motor", installed_at=T - timedelta(days=2))
    cnt = await add_tag(client, t, p, "R1.ciclos", "cycle_counter", cyl, counter_max=65535, max_rate_per_s=50)
    good = await add_tag(client, t, p, "boas", "part_counter_good", max_rate_per_s=50)
    run = await add_tag(client, t, p, "run", "run_state", data_type="bool")

    seq = [
        (cnt, 0, 65000), (good, 0, 0), (run, 0, 0), (run, 5, 1),
        (cnt, 30, 65400), (good, 30, 10), (run, 35, 1),
        (cnt, 60, 65530), (cnt, 61, 20), (cnt, 61, 20),           # rollover + duplicate
        (cnt, 62, 999999),                                         # glitch
        (cnt, 90, 300), (good, 90, 25), (run, 65, 1), (run, 95, 0), (run, 95.4, 1),  # debounce
        (run, 3700, 1), (run, 3720, 0),                            # data gap, crosses an hour
        (cnt, 3800, 5), (good, 3800, 30),                          # reset
    ]
    for tag, sec, v in seq:
        await publish(t.id, tag["id"], v, T + timedelta(seconds=sec))
    # component swap halfway (affects attribution windows in both paths the same way)
    await api(client, "POST", f"/api/components/{c1['id']}/remove", h,
              json={"reason": "failure", "removed_at": (T + timedelta(seconds=3850)).isoformat()})
    for tag, sec, v in [(cnt, 3900, 45), (good, 3900, 33), (run, 3900, 1), (run, 3960, 0)]:
        await publish(t.id, tag["id"], v, T + timedelta(seconds=sec))

    async with AsyncSessionLocal() as s:
        report = await recompute_usage(s, t.id)
        assert report.readings == len(seq) + 4 - 1          # the exact duplicate collapses in Influx
        assert report.differences == []
        assert report.totals[("position", cyl["id"], "cycles")] == 400 + 130 + 26 + 280 + 5 + 40
        topo = await load_topology(s, t.id)
        replayed = replay(topo, await load_readings(t.id, list(topo.tags)))
        stored = {(b.scope, b.entity_id, b.measure, b.bucket_start): b.value for b in (await s.execute(
            select(UsageBucket).where(UsageBucket.tenant_id == t.id))).scalars()}
        assert stored.keys() == replayed.buckets.keys()
        for k, v in stored.items():
            assert v == pytest.approx(replayed.buckets[k], abs=1e-6), k
        applied = await recompute_usage(s, t.id, apply=True)
        assert applied.applied and applied.differences == []
    async with AsyncSessionLocal() as s:
        again = await recompute_usage(s, t.id)
        assert again.differences == []
    # the API exposes the dry run (admin only)
    r = await client.post("/api/usage/recompute", headers=t.headers("admin"))
    assert r.status_code == 200 and r.json()["differences"] == []
    assert (await client.post("/api/usage/recompute", headers=t.headers("operator"))).status_code == 403


async def test_position_counter_tag_wins_over_cross_check_tags(client, tenants, db):
    """Position with counter_tag_id (edge pulse) + a PLC counter linked as cross-check: no double count."""
    t = tenants.a
    p = await build_plant(client, t)
    pos = await add_position(client, t, p, "Cilindro A1")
    pulse = await add_tag(client, t, p, "A1.eos", "cycle_pulse", pos, max_rate_per_s=100)
    plc = await add_tag(client, t, p, "A1.ciclos", "cycle_counter", pos, counter_max=65535, max_rate_per_s=100)
    await api(client, "PATCH", f"/api/positions/{pos['id']}", t.headers("admin"), json={"counter_tag_id": pulse["id"]})
    T = base_ts()
    for tag, sec, v in [(pulse, 0, 0), (plc, 0, 500), (pulse, 30, 12), (plc, 30, 512)]:
        await publish(t.id, tag["id"], v, T + timedelta(seconds=sec))
    assert (await totals_of(db, "position", pos["id"]))["cycles"] == 12


async def test_servo_motor_alias_accepted(client, tenants):
    t = tenants.a
    p = await build_plant(client, t)
    pos = await add_position(client, t, p, "Servo X", "servo_motor")
    assert pos["component_type"] == "servo"
    comp = await add_component(client, t, p, "s", pos, component_type="servo_motor", nominal_life={"hours": 25000})
    assert comp["component_type"] == "servo" and comp["state"] == "installed"
