"""Tenant isolation and RBAC for every /api/predictive endpoint (CLAUDE.md: no endpoint without an isolation test).

Read: maintenance_planner, manager, technician, production_supervisor (+ admin); configure / rebaseline: planner,
manager, admin; recompute: admin; evaluation: planner, manager, admin; badges: any authenticated user."""
from __future__ import annotations

from datetime import timedelta

import pytest

from maint_helpers import FixedClock, api, make_maint_tenant
from test_predictive_lifecycle import T0, _position, feed, small_monitor

READERS = ("maintenance_planner", "manager", "technician", "production_supervisor", "admin")
CONFIGURERS = ("maintenance_planner", "manager", "admin")


@pytest.fixture
def clk(monkeypatch):
    from src.services import clock
    c = FixedClock(T0)
    monkeypatch.setattr(clock, "now", c)
    return c


async def _world(client, clk, label):
    ctx = await make_maint_tenant(label)
    pos = await _position(ctx)
    m = await small_monitor(client, ctx, clk, pos)
    await feed(client, ctx, clk, m["id"], [10.0, 10.1, 9.9, 16.0])
    (a,) = await api(client, "GET", f"/api/predictive/alerts?position_id={pos}", ctx.h("maintenance_planner"))
    return ctx, pos, m, a


async def test_tenant_isolation_every_endpoint(client, clk):
    A, posA, mA, aA = await _world(client, clk, "PIA")
    B, posB, mB, aB = await _world(client, clk, "PIB")
    hb = B.h("admin")
    ruleA = mA["rules"][0]["id"]
    mach_a = A.machines["Transportador de Saída"]
    # B never sees A's rows in listings
    for url in ("/api/predictive/monitors", "/api/predictive/alerts", "/api/predictive/rules",
                f"/api/predictive/monitors?position_id={posA}", f"/api/predictive/alerts?position_id={posA}",
                f"/api/predictive/rules?monitor_id={mA['id']}"):
        rows = await api(client, "GET", url, hb)
        assert all(r["id"] not in (mA["id"], aA["id"], ruleA) for r in rows), url
        assert all(r.get("position_id") in (None, posB) for r in rows), url
    assert mach_a not in await api(client, "GET", "/api/predictive/badges", hb)
    assert await api(client, "GET", f"/api/predictive/badges?machine_id={mach_a}", hb) == {}
    # A's ids answer 404 to B (same as nonexistent)
    calls = [
        ("GET", f"/api/predictive/monitors/{mA['id']}", None),
        ("PATCH", f"/api/predictive/monitors/{mA['id']}", {"enabled": False}),
        ("POST", f"/api/predictive/monitors/{mA['id']}/rebaseline", {"reason": "teste de isolamento"}),
        ("POST", f"/api/predictive/monitors/{mA['id']}/process", None),
        ("PATCH", f"/api/predictive/rules/{ruleA}", {"value": 1.0}),
        ("DELETE", f"/api/predictive/rules/{ruleA}", None),
        ("POST", "/api/predictive/rules", {"monitor_id": mA["id"], "type": "threshold", "value": 1.0}),
        ("GET", f"/api/predictive/features?position_id={posA}", None),
        ("GET", f"/api/predictive/features?monitor_id={mA['id']}", None),
        ("GET", f"/api/predictive/alerts/{aA['id']}", None),
        ("POST", f"/api/predictive/alerts/{aA['id']}/transition", {"to": "acknowledged"}),
        ("POST", f"/api/predictive/alerts/{aA['id']}/convert", {}),
        ("POST", "/api/predictive/recompute", {"monitor_id": mA["id"]}),
        ("POST", "/api/predictive/monitors", {"position_id": posA, "feature": "vibration"}),
        ("PUT", "/api/predictive/production-plan", {"machine_id": mach_a, "days": []}),
        ("GET", f"/api/predictive/production-plan?machine_id={mach_a}", None),
        ("POST", "/api/predictive/condition-plans", {"position_id": posA, "feature": "current"}),
        ("GET", f"/api/predictive/evaluation?line_id={A.line_id}", None),
    ]
    for method, url, body in calls:
        r = await client.request(method, url, headers=hb, json=body)
        assert r.status_code == 404, (method, url, r.status_code, r.text)
    # nothing of A changed
    a_after = await api(client, "GET", f"/api/predictive/alerts/{aA['id']}", A.h("maintenance_planner"))
    assert a_after["state"] == "open" and a_after["work_order_id"] is None
    m_after = await api(client, "GET", f"/api/predictive/monitors/{mA['id']}", A.h("maintenance_planner"))
    assert m_after["enabled"] is True and len(m_after["rules"]) == 1 and len(m_after["baselines"]) == 1
    # evaluation of B counts only B's alerts
    ev = await api(client, "GET", "/api/predictive/evaluation?start=2027-01-01T00:00:00Z&stop=2027-12-01T00:00:00Z",
                   B.h("manager"))
    assert {r["alert_id"] for r in ev["alert_outcomes"]} == {aB["id"]}


async def test_rbac_matrix(client, clk):
    ctx, pos, m, a = await _world(client, clk, "PIR")
    all_roles = ("maintenance_planner", "manager", "technician", "production_supervisor", "operator", "admin")
    read_urls = ["/api/predictive/monitors", f"/api/predictive/monitors/{m['id']}", "/api/predictive/rules",
                 f"/api/predictive/features?position_id={pos}", "/api/predictive/alerts",
                 f"/api/predictive/alerts/{a['id']}",
                 f"/api/predictive/production-plan?machine_id={ctx.machines['Transportador de Saída']}"]
    for role in all_roles:
        for url in read_urls:
            r = await client.get(url, headers=ctx.h(role))
            assert r.status_code == (200 if role in READERS else 403), (role, url, r.status_code)
        r = await client.get("/api/predictive/badges", headers=ctx.h(role))
        assert r.status_code == 200, role
        r = await client.get("/api/predictive/evaluation", headers=ctx.h(role))
        assert r.status_code == (200 if role in ("maintenance_planner", "manager", "admin") else 403), role
        r = await client.post("/api/predictive/recompute", headers=ctx.h(role), json={"monitor_id": m["id"]})
        assert r.status_code == (200 if role == "admin" else 403), role
    # configuration endpoints
    pos2 = await _position(ctx, name="Motor RBAC")
    for role in all_roles:
        ok = role in CONFIGURERS
        h = ctx.h(role)
        r = await client.patch(f"/api/predictive/monitors/{m['id']}", headers=h, json={"sensor_resolution": 0.0})
        assert r.status_code == (200 if ok else 403), (role, "patch")
        r = await client.post("/api/predictive/rules", headers=h,
                              json={"monitor_id": m["id"], "type": "pct_of_baseline", "value": 1.2,
                                    "code": f"R-{role}"})
        assert r.status_code == (201 if ok else 403), (role, "rule")
        if ok:
            rid = r.json()["id"]
            assert (await client.patch(f"/api/predictive/rules/{rid}", headers=h, json={"value": 1.3})).status_code == 200
            assert (await client.delete(f"/api/predictive/rules/{rid}", headers=h)).status_code == 204
        clk.at += timedelta(seconds=1)
        r = await client.post(f"/api/predictive/monitors/{m['id']}/rebaseline", headers=h, json={"reason": f"teste {role}"})
        assert r.status_code == (200 if ok else 403), (role, "rebaseline")
        r = await client.post(f"/api/predictive/monitors/{m['id']}/process", headers=h)
        assert r.status_code == (200 if ok else 403), (role, "process")
        r = await client.put("/api/predictive/production-plan", headers=h, json={
            "machine_id": ctx.machines["Transportador de Saída"], "days": [{"day": "2027-03-10", "planned_parts": 100}]})
        assert r.status_code == (200 if ok else 403), (role, "plan")
        r = await client.post("/api/predictive/condition-plans", headers=h, json={"position_id": pos2, "feature": "current"})
        assert r.status_code == (201 if ok else 403), (role, "condition-plan")
    r = await client.post("/api/predictive/monitors", headers=ctx.h("technician"),
                          json={"position_id": pos2, "feature": "current"})
    assert r.status_code == 403
    r = await client.post("/api/predictive/monitors", headers=ctx.h("admin"), json={"position_id": pos2, "feature": "current"})
    assert r.status_code == 201
    # lifecycle: acknowledge = planner/manager/technician; close and convert = planner/manager (+admin)
    for role in ("production_supervisor", "operator"):
        r = await client.post(f"/api/predictive/alerts/{a['id']}/transition", headers=ctx.h(role),
                              json={"to": "acknowledged"})
        assert r.status_code == 403, role
    r = await client.post(f"/api/predictive/alerts/{a['id']}/transition", headers=ctx.h("technician"),
                          json={"to": "closed", "resolution": "false_alarm"})
    assert r.status_code == 403
    for role in ("technician", "production_supervisor", "operator"):
        r = await client.post(f"/api/predictive/alerts/{a['id']}/convert", headers=ctx.h(role), json={})
        assert r.status_code == 403, role
    r = await client.post(f"/api/predictive/alerts/{a['id']}/transition", headers=ctx.h("manager"),
                          json={"to": "closed", "resolution": "known_cause", "comment": "teste de engenharia"})
    assert r.status_code == 200 and r.json()["resolution"] == "known_cause"
    r = await client.post(f"/api/predictive/alerts/{a['id']}/transition", headers=ctx.h("manager"),
                          json={"to": "acknowledged"})
    assert r.status_code == 422 and r.json()["detail"]["allowed"] == []      # closed is final
