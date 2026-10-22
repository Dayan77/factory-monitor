"""Alert lifecycle (P-8), conversion into predictive WOs and condition plans (P-9), baseline resets (P-2):
automatic on WO completion and component swap, manual with reason, on configuration change; failure before action.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from maint_helpers import FixedClock, api, make_maint_tenant

T0 = datetime(2027, 3, 1, 4, 0, tzinfo=timezone.utc)      # local midnight in Manaus


@pytest.fixture
def clk(monkeypatch):
    from src.services import clock
    c = FixedClock(T0)
    monkeypatch.setattr(clock, "now", c)
    return c


async def _position(ctx, name="Motor L", machine="Transportador de Saída") -> str:
    from src.db import AsyncSessionLocal
    from src.models import ComponentType, Position
    async with AsyncSessionLocal() as s:
        p = Position(tenant_id=ctx.id, machine_id=ctx.machines[machine], name=name,
                     component_type=ComponentType.electric_motor)
        s.add(p)
        await s.commit()
        return p.id


async def small_monitor(client, ctx, clk, pos: str, **kw) -> dict:
    """current, 8 h windows, baseline of 3 windows, threshold 15 A `warning` sustained 1."""
    clk.at = T0
    body = {"position_id": pos, "feature": "current", "window_hours": 8, "warmup_windows": 0,
            "failure_limit": 20.0, "start_at": T0.isoformat(),
            "params": {"baseline_min_windows": 3, "baseline_min_span_h": 0},
            "rules": [{"code": "THR", "type": "threshold", "value": 15.0, "sustained_for": 1, "severity": "warning"}]}
    body.update(kw)
    return await api(client, "POST", "/api/predictive/monitors", ctx.h("maintenance_planner"), 201, json=body)


async def feed(client, ctx, clk, mid: str, xs: list, process: bool = True) -> None:
    """Appends windows after the last stored one (8 h each, aligned to the anchor) and runs the pipeline."""
    from src.db import AsyncSessionLocal
    from src.models import PredictiveMonitor
    from src.services.predictive import engine as pe, service as ps
    for x in xs:
        async with AsyncSessionLocal() as s:
            m = await s.get(PredictiveMonitor, mid)
            reset = await ps.latest_reset(s, m)
            last = await pe.last_window(s, m)
            start = last.end_at if last is not None else m.anchor_at
            start = max(ps.aware(start), ps.aware(reset.reset_at))
            await pe.store_window(s, m, reset, start=start, end=start + timedelta(hours=8), x=x, n=10,
                                  running_h=8.0 if x is not None else 0.0)
            await s.commit()
            clk.at = start + timedelta(hours=8)
        if process:
            await api(client, "POST", f"/api/predictive/monitors/{mid}/process", ctx.h("maintenance_planner"))


async def _alert(client, ctx, pos) -> dict:
    rows = await api(client, "GET", f"/api/predictive/alerts?position_id={pos}&state=open&state=acknowledged"
                                    f"&state=converted", ctx.h("maintenance_planner"))
    assert len(rows) == 1, rows
    return rows[0]


async def _wo_to_in_progress(client, ctx, wo_id: str) -> None:
    h = ctx.h("manager")
    await api(client, "POST", f"/api/work-orders/{wo_id}/transition", h,
              json={"to": "scheduled", "scheduled_for": "2027-03-10"})
    await api(client, "POST", f"/api/work-orders/{wo_id}/transition", h, json={"to": "in_progress"})


async def test_convert_complete_confirmed_resets_baseline(client, clk):
    ctx = await make_maint_tenant("PL1")
    pos = await _position(ctx)
    m = await small_monitor(client, ctx, clk, pos)
    await feed(client, ctx, clk, m["id"], [10.0, 10.1, 9.9, 16.0])
    a = await _alert(client, ctx, pos)
    assert a["severity"] == "warning" and a["state"] == "open" and a["first_triggers"] == ["THR"]
    assert a["allowed_transitions"] == ["acknowledged", "closed", "converted"]
    # invalid transition → 422 with allowed; operator cannot even read
    r = await client.post(f"/api/predictive/alerts/{a['id']}/transition", headers=ctx.h("maintenance_planner"),
                          json={"to": "closed"})
    assert r.status_code == 422 and "allowed_resolutions" in r.json()["detail"]
    await api(client, "POST", f"/api/predictive/alerts/{a['id']}/convert", ctx.h("technician"), 403, json={})
    await api(client, "POST", f"/api/predictive/alerts/{a['id']}/transition", ctx.h("operator"), 403,
              json={"to": "acknowledged"})
    ack = await api(client, "POST", f"/api/predictive/alerts/{a['id']}/transition", ctx.h("technician"),
                    json={"to": "acknowledged", "comment": "vou avaliar"})
    assert ack["state"] == "acknowledged" and ack["history"][-1]["by"] == ctx.users["technician"]["id"]
    out = await api(client, "POST", f"/api/predictive/alerts/{a['id']}/convert", ctx.h("maintenance_planner"), 201,
                    json={"estimated_hours": 3})
    wo = out["work_order"]
    assert out["state"] == "converted" and out["work_order_id"] == wo["id"]
    assert wo["type"] == "predictive" and wo["state"] == "approved" and wo["source_alert_id"] == a["id"]
    today = (clk.at - timedelta(hours=4)).date()                       # America/Manaus (UTC−4)
    assert wo["estimated_hours"] == 3 and wo["due_by"] == (today + timedelta(days=7)).isoformat()   # no RUL yet
    assert "Gráfico: /maintenance/predictive?alert=" in wo["description"]
    r = await client.post(f"/api/predictive/alerts/{a['id']}/convert", headers=ctx.h("maintenance_planner"), json={})
    assert r.status_code == 422                                      # one WO per alert
    # completion requires `finding`; defect_confirmed requires a compatible failure record of the WO
    await _wo_to_in_progress(client, ctx, wo["id"])
    h = ctx.h("manager")
    r = await client.post(f"/api/work-orders/{wo['id']}/transition", headers=h, json={"to": "completed"})
    assert r.status_code == 422 and "finding" in str(r.json())
    r = await client.post(f"/api/work-orders/{wo['id']}/transition", headers=h,
                          json={"to": "completed", "finding": "defect_confirmed"})
    assert r.status_code == 422 and "OHE" in str(r.json())
    await api(client, "POST", "/api/failures/", h, 201, json={
        "machine_id": ctx.machines["Transportador de Saída"], "position_id": pos, "failed_at": clk.at.isoformat(),
        "severity": "incipient", "failure_mode": "OHE", "detection_method": "continuous_condition_monitoring",
        "work_order_id": wo["id"]})
    done = await api(client, "POST", f"/api/work-orders/{wo['id']}/transition", h,
                     json={"to": "completed", "finding": "defect_confirmed"})
    assert done["finding"] == "defect_confirmed" and done["resets_baseline"] is True
    a2 = await api(client, "GET", f"/api/predictive/alerts/{a['id']}", h)
    assert a2["state"] == "closed" and a2["resolution"] == "confirmed"
    assert a2["history"][-1]["source"] == "work_order"
    mon = await api(client, "GET", f"/api/predictive/monitors/{m['id']}", h)
    assert [b["reset_kind"] for b in mon["baselines"]] == ["initial", "work_order"]
    assert mon["baselines"][-1]["warmup"] is True and mon["state"] == "learning"
    # the incipient failure found by the predictive WO does not close anything nor count as ground truth
    feed_after = await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", h)
    assert feed_after["closed"] == []


async def test_cancel_returns_to_acknowledged_and_no_defect_is_false_alarm(client, clk):
    ctx = await make_maint_tenant("PL2")
    pos = await _position(ctx)
    m = await small_monitor(client, ctx, clk, pos)
    await feed(client, ctx, clk, m["id"], [10.0, 10.1, 9.9, 16.0])
    a = await _alert(client, ctx, pos)
    hp = ctx.h("maintenance_planner")
    wo = (await api(client, "POST", f"/api/predictive/alerts/{a['id']}/convert", hp, 201, json={}))["work_order"]
    await api(client, "POST", f"/api/work-orders/{wo['id']}/transition", ctx.h("manager"),
              json={"to": "cancelled", "cancel_reason": "production_conflict"})
    a2 = await api(client, "GET", f"/api/predictive/alerts/{a['id']}", hp)
    assert a2["state"] == "acknowledged"                                   # P-8: back, still without action
    # the fold replays the cancellation: processing again keeps it acknowledged
    await feed(client, ctx, clk, m["id"], [16.5])
    assert (await api(client, "GET", f"/api/predictive/alerts/{a['id']}", hp))["state"] == "acknowledged"
    wo2 = (await api(client, "POST", f"/api/predictive/alerts/{a['id']}/convert", hp, 201, json={}))["work_order"]
    assert wo2["id"] != wo["id"]
    await _wo_to_in_progress(client, ctx, wo2["id"])
    await api(client, "POST", f"/api/work-orders/{wo2['id']}/transition", ctx.h("manager"),
              json={"to": "completed", "finding": "no_defect", "resets_baseline": False, "labor_hours": 2})
    a3 = await api(client, "GET", f"/api/predictive/alerts/{a['id']}", hp)
    assert a3["state"] == "closed" and a3["resolution"] == "false_alarm"
    mon = await api(client, "GET", f"/api/predictive/monitors/{m['id']}", hp)
    assert [b["reset_kind"] for b in mon["baselines"]] == ["initial"]     # resets_baseline=false honoured
    # PMP side: predictive no-defect hours shown apart
    k = await api(client, "GET", "/api/maintenance/kpis?start=2027-03-01T00:00:00Z&stop=2027-04-01T00:00:00Z",
                  ctx.h("manager"))
    assert k["predictive_no_defect_hours"] == 2.0
    rc = await api(client, "POST", "/api/predictive/recompute", ctx.h("admin"), json={"monitor_id": m["id"]})
    assert rc["same"] is True, rc


async def test_condition_plan_creates_one_draft_and_plan_due(client, clk):
    ctx = await make_maint_tenant("PL3")
    pos = await _position(ctx)
    hp = ctx.h("maintenance_planner")
    plan = await api(client, "POST", "/api/predictive/condition-plans", hp, 201, json={
        "position_id": pos, "feature": "current", "min_severity": "warning", "estimated_hours": 4,
        "task_list": ["Medir corrente", "Inspecionar rolamentos"]})
    m = await small_monitor(client, ctx, clk, pos)
    await feed(client, ctx, clk, m["id"], [10.0, 10.1, 9.9, 16.0, 16.2])
    a = await _alert(client, ctx, pos)
    assert a["state"] == "converted" and a["work_order_id"]
    wo = await api(client, "GET", f"/api/work-orders/{a['work_order_id']}", hp)
    assert wo["state"] == "draft" and wo["plan_id"] == plan["id"] and wo["type"] == "predictive"
    assert wo["source_alert_id"] == a["id"] and len(wo["task_list"]) == 2 and wo["estimated_hours"] == 4
    st = await api(client, "GET", f"/api/maintenance-plans/status?position_id={pos}", hp)
    row = next(r for r in (st if isinstance(st, list) else st.get("plans", st)) if r["plan_id"] == plan["id"])
    assert row["state"] == "due" and row["due_trigger"] == "condition"
    assert row["open_work_order_id"] == wo["id"]
    a_conv = next(e for e in (await api(client, "GET", f"/api/predictive/alerts/{a['id']}", hp))["history"]
                  if e["action"] == "convert")
    assert a_conv["source"] == "plan"
    rc = await api(client, "POST", "/api/predictive/recompute", ctx.h("admin"), json={"monitor_id": m["id"]})
    assert rc["same"] is True, rc


async def test_failure_before_action_cancels_not_started_wo(client, clk):
    ctx = await make_maint_tenant("PL4")
    pos = await _position(ctx)
    m = await small_monitor(client, ctx, clk, pos)
    await feed(client, ctx, clk, m["id"], [10.0, 10.1, 9.9, 16.0])
    a = await _alert(client, ctx, pos)
    hp = ctx.h("maintenance_planner")
    wo = (await api(client, "POST", f"/api/predictive/alerts/{a['id']}/convert", hp, 201, json={}))["work_order"]
    await api(client, "POST", "/api/failures/", ctx.h("manager"), 201, json={
        "machine_id": ctx.machines["Transportador de Saída"], "position_id": pos, "failed_at": clk.at.isoformat(),
        "severity": "critical", "failure_mode": "OHE"})
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", hp)
    a2 = await api(client, "GET", f"/api/predictive/alerts/{a['id']}", hp)
    assert a2["state"] == "closed" and a2["resolution"] == "failed_before_action"
    w = await api(client, "GET", f"/api/work-orders/{wo['id']}", hp)
    assert w["state"] == "cancelled" and w["cancel_reason"] == "superseded"


async def test_rebaseline_manual_config_change_and_component_swap(client, clk):
    from src.db import AsyncSessionLocal
    from src.models import Component, ComponentType
    ctx = await make_maint_tenant("PL5")
    pos = await _position(ctx)
    m = await small_monitor(client, ctx, clk, pos)
    await feed(client, ctx, clk, m["id"], [10.0, 10.1, 9.9, 10.0])
    hp = ctx.h("maintenance_planner")
    mon = await api(client, "GET", f"/api/predictive/monitors/{m['id']}", hp)
    assert mon["state"] == "monitoring" and mon["z"] is not None and mon["ewma"] is not None
    # manual: reason required, planner/manager/admin only, no warm-up
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/rebaseline", hp, 422, json={})
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/rebaseline", ctx.h("technician"), 403,
              json={"reason": "novo produto"})
    clk.at += timedelta(minutes=5)
    b = await api(client, "POST", f"/api/predictive/monitors/{m['id']}/rebaseline", hp,
                  json={"reason": "novo produto mais pesado"})
    assert b["reset_kind"] == "manual" and b["warmup"] is False and b["state"] == "learning"
    assert b["user_id"] == ctx.users["maintenance_planner"]["id"]
    mon = await api(client, "GET", f"/api/predictive/monitors/{m['id']}", hp)
    assert mon["state"] == "learning" and mon["z"] is None and mon["rul_condition"]["reason"] == "baseline_learning"
    # windows after the reset relearn: 3 windows → monitoring again
    await feed(client, ctx, clk, m["id"], [12.0, 12.1, 11.9, 12.0])
    mon = await api(client, "GET", f"/api/predictive/monitors/{m['id']}", hp)
    assert mon["state"] == "monitoring" and mon["baseline"]["m0"] == pytest.approx(12.0)
    # component installed at the position (components API / data) → automatic reset with warm-up
    clk.at += timedelta(hours=1)
    async with AsyncSessionLocal() as s:
        s.add(Component(tenant_id=ctx.id, component_type=ComponentType.electric_motor, position_id=pos,
                        last_position_id=pos, installed_at=clk.at, serial_number="MOT-NEW"))
        await s.commit()
    await api(client, "POST", f"/api/predictive/monitors/{m['id']}/process", hp)
    mon = await api(client, "GET", f"/api/predictive/monitors/{m['id']}", hp)
    assert [x["reset_kind"] for x in mon["baselines"]] == ["initial", "manual", "installation"]
    assert mon["baselines"][-1]["warmup"] is True
    # configuration change (window size) restarts the baseline
    clk.at += timedelta(minutes=10)
    await api(client, "PATCH", f"/api/predictive/monitors/{m['id']}", hp, json={"window_hours": 4})
    mon = await api(client, "GET", f"/api/predictive/monitors/{m['id']}", hp)
    assert mon["baselines"][-1]["reset_kind"] == "config_change" and mon["window"] == {"hours": 4}
    # baselines are never deleted
    assert len(mon["baselines"]) == 4


async def test_component_swap_in_wo_resets_baseline(client, clk):
    from src.db import AsyncSessionLocal
    from src.models import Component, ComponentType
    ctx = await make_maint_tenant("PL6")
    pos = await _position(ctx)
    async with AsyncSessionLocal() as s:
        old = Component(tenant_id=ctx.id, component_type=ComponentType.electric_motor, position_id=pos,
                        last_position_id=pos, installed_at=T0 - timedelta(days=100), serial_number="OLD")
        new = Component(tenant_id=ctx.id, component_type=ComponentType.electric_motor, serial_number="NEW")
        s.add_all([old, new])
        await s.commit()
        new_id = new.id
    m = await small_monitor(client, ctx, clk, pos)
    await feed(client, ctx, clk, m["id"], [10.0, 10.1, 9.9, 10.0])
    h = ctx.h("manager")
    wo = await api(client, "POST", "/api/work-orders/", h, 201, json={
        "type": "preventive", "machine_id": ctx.machines["Transportador de Saída"], "position_id": pos,
        "title": "Troca do motor", "state": "open"})
    await api(client, "POST", f"/api/work-orders/{wo['id']}/transition", h, json={"to": "approved"})
    await api(client, "POST", f"/api/work-orders/{wo['id']}/replacements", h, 201,
              json={"position_id": pos, "new_component_id": new_id, "removal_reason": "preventive_replacement"})
    await _wo_to_in_progress(client, ctx, wo["id"])
    done = await api(client, "POST", f"/api/work-orders/{wo['id']}/transition", h, json={"to": "completed"})
    assert done["resets_baseline"] is True                                   # swap ⇒ default true
    mon = await api(client, "GET", f"/api/predictive/monitors/{m['id']}", h)
    kinds = [b["reset_kind"] for b in mon["baselines"]]
    assert kinds == ["initial", "installation"] and mon["state"] == "learning"


async def test_monitor_defaults_and_suggested_rules(client, clk):
    ctx = await make_maint_tenant("PL7")
    pos = await _position(ctx, name="Cilindro Z", machine="Estação de Fixação")
    clk.at = T0
    m = await api(client, "POST", "/api/predictive/monitors", ctx.h("maintenance_planner"), 201,
                  json={"position_id": pos, "feature": "stroke_time"})
    assert m["window"] == {"cycles": 1000} and m["warmup_windows"] == 5
    assert m["failure_limit"] == 1.2 and m["failure_limit_relative"] is True
    assert [(r["type"], r["value"], r["severity"], r["sustained_for"]) for r in m["rules"]] == \
        [("pct_of_baseline", 1.10, "warning", 2)]
    pos2 = await _position(ctx, name="Motor Z")
    m2 = await api(client, "POST", "/api/predictive/monitors", ctx.h("manager"), 201,
                   json={"position_id": pos2, "feature": "current", "failure_limit": 15.0})
    assert m2["window"] == {"hours": 8} and m2["warmup_windows"] == 1
    assert {(r["type"], r["severity"]) for r in m2["rules"]} == {("threshold", "critical"), ("pct_of_baseline", "warning")}
    r = await client.post("/api/predictive/monitors", headers=ctx.h("manager"),
                          json={"position_id": pos2, "feature": "current"})
    assert r.status_code == 409                                              # one monitor per (position, signal)
    # sustained as duration → ceil(duration / window)
    rule = await api(client, "POST", "/api/predictive/rules", ctx.h("manager"), 201, json={
        "monitor_id": m2["id"], "type": "rate_of_change", "value": 0.01, "sustained_duration": "20h"})
    assert rule["sustained_for"] == 3
