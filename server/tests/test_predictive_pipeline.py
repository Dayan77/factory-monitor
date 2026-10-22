"""P-1 feature extraction: pure window arithmetic and the Influx → condition_features pipeline for stroke-time
blocks (with and without a cycle counter) and analog windows; WebSocket `predictive_alert` messages."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from maint_helpers import FixedClock, api, make_maint_tenant
from src.services.predictive import features as fx

T = datetime(2027, 5, 3, 4, 0, tzinfo=timezone.utc)


def at(h: float) -> datetime:
    return T + timedelta(hours=h)


def test_time_weighted_mean_only_while_running():
    # current: 10 A for 2 h then 20 A (deadband: one reading per change); running only from 1 h to 5 h
    sig = [(at(0), 10.0), (at(2), 20.0)]
    run = [(at(0), 0.0), (at(1), 1.0), (at(5), 0.0)]
    wf = fx.time_window_feature(sig, run, at(0), at(8))
    assert wf.valid and wf.running_h == pytest.approx(4.0) and wf.coverage == pytest.approx(1.0)
    assert wf.x == pytest.approx((1 * 10 + 3 * 20) / 4)                 # not the arithmetic mean of samples (15)
    # stopped motor: not "low current", no point
    wf = fx.time_window_feature(sig, [(at(0), 0.0)], at(0), at(8))
    assert not wf.valid and wf.x is None and wf.reason == "not_running"
    # running < 25 % of the window → invalid
    wf = fx.time_window_feature(sig, [(at(0), 1.0), (at(1.9), 0.0)], at(0), at(8))
    assert not wf.valid
    # data hole: no reading of the machine for a while → the held value does not cross the hole
    pres = [(at(0), at(1.5))]
    wf = fx.time_window_feature(sig, [(at(0), 1.0)], at(0), at(8), presence=pres)
    assert not wf.valid and wf.reason == "not_running"                     # 1.5 h of data: < 25 % of 8 h
    wf = fx.time_window_feature(sig, [(at(0), 1.0)], at(0), at(8), presence=[(at(0), at(3))])
    assert wf.valid and wf.running_h == pytest.approx(3.0) and wf.x == pytest.approx((2 * 10 + 1 * 20) / 3)
    wf = fx.time_window_feature([(at(0), 10.0)], None, at(0), at(8), presence=[(at(0), at(8))])
    assert wf.valid and wf.x == pytest.approx(10.0)
    # coverage < 80 % of running time → invalid
    wf = fx.time_window_feature([(at(6), 10.0)], [(at(0), 1.0)], at(0), at(8))
    assert not wf.valid and wf.reason == "low_coverage"
    # ΔT with ambient tag
    wf = fx.time_window_feature([(at(0), 60.0)], [(at(0), 1.0)], at(0), at(8), ambient=[(at(0), 25.0)])
    assert wf.x == pytest.approx(35.0)


def test_cycle_blocks_with_and_without_counter():
    strokes = [(at(0) + timedelta(seconds=k + 1), 200.0 + (k % 5)) for k in range(25)]
    blocks, cur = fx.cycle_blocks(strokes, 10, at(0))
    assert [b.k for b in blocks] == [1, 2] and all(b.valid and b.n == 10 for b in blocks)
    assert blocks[0].x == 202.0 and cur["end_at"] == strokes[19][0]
    # counter: cumulative cycles 0, 6, 12, 18, 24 (rollover-safe), strokes only in 7 of the 12 → invalid block 2
    counter = [(at(0) + timedelta(seconds=6 * j), float(6 * j)) for j in range(5)]
    cum = fx.cumulative_cycles(counter)
    blocks, cur = fx.cycle_blocks(strokes[:17], 10, at(0) - timedelta(seconds=1), 1, cum, 0.0)
    assert [b.k for b in blocks] == [1, 2]
    assert blocks[0].valid and blocks[0].n == 12 and not blocks[1].valid
    assert cur["carry"] == pytest.approx(4.0)
    assert fx.cumulative_cycles([(at(0), 65530.0), (at(1), 4.0)], counter_max=65535)[-1][1] == pytest.approx(10.0)


@pytest.fixture
def clk(monkeypatch):
    from src.services import clock
    c = FixedClock(T)
    monkeypatch.setattr(clock, "now", c)
    return c


async def test_stroke_blocks_from_influx_and_ws(client, clk):
    from src.db import AsyncSessionLocal
    from src.models import ComponentType, Position, Tag
    from src.models.tag import ProtocolType, SemanticRole
    from src.services.influx import influx
    ctx = await make_maint_tenant("PP1")
    mach = ctx.machines["Estação de Fixação"]
    async with AsyncSessionLocal() as s:
        p = Position(tenant_id=ctx.id, machine_id=mach, name="Cilindro P", component_type=ComponentType.pneumatic_cylinder)
        s.add(p)
        await s.flush()
        tag = Tag(tenant_id=ctx.id, machine_id=mach, position_id=p.id, name="curso P", unit="ms",
                  protocol=ProtocolType.mqtt, address=f"p/st/{uuid.uuid4().hex[:6]}", semantic_role=SemanticRole.stroke_time)
        s.add(tag)
        await s.commit()
        pos, tag_id = p.id, tag.id
    # 6 blocks of 20 strokes, one stroke per minute; block 6 drifts up
    vals = []
    for b in range(6):
        for k in range(20):
            vals.append(200.0 + (k % 3) - 1 + (30.0 if b == 5 else 0.0))
    for n, v in enumerate(vals):
        await influx.write(ctx.id, tag_id, v, machine_id=mach, unit="ms", ts=T + timedelta(minutes=n + 1))
    hp = ctx.h("maintenance_planner")
    m = await api(client, "POST", "/api/predictive/monitors", hp, 201, json={
        "position_id": pos, "feature": "stroke_time", "tag_id": tag_id, "window_cycles": 20, "warmup_windows": 1,
        "start_at": T.isoformat(), "params": {"baseline_min_windows": 3, "baseline_min_span_h": 0},
        "rules": [{"code": "THR", "type": "threshold", "value": 220.0, "sustained_for": 1, "severity": "critical"}]})
    # websocket: the planner's socket receives predictive_alert; the operator's does not
    from src.websocket.manager import manager

    class FakeWS:
        def __init__(self):
            self.msgs = []

        async def send_json(self, msg):
            self.msgs.append(msg)

    planner_ws, operator_ws = FakeWS(), FakeWS()
    await manager.connect(planner_ws, ctx.id, accept=False, user_id=ctx.users["maintenance_planner"]["id"])
    await manager.connect(operator_ws, ctx.id, accept=False, user_id=ctx.users["operator"]["id"])
    try:
        clk.at = T + timedelta(minutes=len(vals) + 10)
        r = await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", hp)
        assert r["windows_added"] == 6
        feats = await api(client, "GET", f"/api/predictive/features?position_id={pos}", hp)
        pts = feats["series"][0]["points"]
        assert [p["i"] for p in pts] == [1, 2, 3, 4, 5, 6]
        assert [p["x"] for p in pts] == [200.0] * 5 + [230.0] and all(p["n"] == 20 for p in pts)
        assert [p["u"] for p in pts] == [20.0 * k for k in range(1, 7)]
        assert pts[0]["phase"] == "warmup" and pts[3]["phase"] == "baseline" and pts[4]["phase"] == "monitoring"
        (a,) = await api(client, "GET", f"/api/predictive/alerts?position_id={pos}", hp)
        assert a["severity"] == "critical" and a["opened_window"] == 6 and "THR" in a["first_triggers"]
        # processing again is idempotent: no new windows, same alert
        r2 = await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", hp)
        assert r2["windows_added"] == 0 and r2["opened"] == []
        msgs = [x for x in planner_ws.msgs if x.get("type") == "predictive_alert"]
        assert msgs and msgs[0]["event"] == "opened" and msgs[0]["alert"]["id"] == a["id"]
        assert not any(x.get("type") == "predictive_alert" for x in operator_ws.msgs)
        # critical → planner notification with push channel kind
        notes = [x for x in planner_ws.msgs if x.get("type") == "notification"]
        assert any(n["notification"]["kind"] == "predictive_critical" for n in notes)
    finally:
        manager.disconnect(planner_ws, ctx.id)
        manager.disconnect(operator_ws, ctx.id)
